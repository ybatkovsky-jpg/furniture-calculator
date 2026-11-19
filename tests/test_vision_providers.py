"""
Тесты выбора vision-провайдера и фолбэка (app/services/image_analyzer.py).

Без сети: клиент подменяется заглушкой, проверяются чистые решения —
порядок провайдеров, тело запроса под каждого и переход к следующему
провайдеру, если первый упал (это и был дефект: при выключенном локальном
сервере пайплайн не доходил до облака).
"""

import asyncio
import json

import httpx
import pytest

from app.config import settings
from app.services.image_analyzer import GeminiImageAnalyzer


@pytest.fixture()
def analyzer(monkeypatch):
    """Анализатор с явно заданными провайдерами (не зависит от .env)."""
    monkeypatch.setattr(settings, "vision_provider", "")
    a = GeminiImageAnalyzer()
    a.local_llm_url = "http://127.0.0.1:9/v1"
    a.local_vision_model = "local-model"
    a.deepseek_key = "ds-key"
    a.deepseek_model = "deepseek-flash"
    a.routerai_key = "ra-key"
    return a


# ════════════════════════════════════════════════════════════════════
# Порядок провайдеров
# ════════════════════════════════════════════════════════════════════

def test_auto_order_local_deepseek(analyzer):
    """Без VISION_PROVIDER: local → deepseek (routerai отключён решением 20.11)."""
    assert [p for p, _ in analyzer._unified_targets()] == ["local", "deepseek"]


def test_forced_provider_goes_first_and_others_stay_as_fallback(analyzer, monkeypatch):
    """VISION_PROVIDER=deepseek: DeepSeek первым, local — резерв."""
    monkeypatch.setattr(settings, "vision_provider", "deepseek")
    targets = analyzer._unified_targets()
    assert targets[0] == ("deepseek", "deepseek-flash")
    assert [p for p, _ in targets] == ["deepseek", "local"]


def test_forced_provider_without_key_falls_back_to_auto(analyzer, monkeypatch):
    """VISION_PROVIDER=deepseek, но ключа нет — авто-порядок, без падения."""
    monkeypatch.setattr(settings, "vision_provider", "deepseek")
    analyzer.deepseek_key = ""
    assert [p for p, _ in analyzer._unified_targets()] == ["local"]


def test_no_providers_at_all(analyzer):
    analyzer.local_llm_url = ""
    analyzer.deepseek_key = ""
    analyzer.routerai_key = ""
    assert analyzer._unified_targets() == []


# ════════════════════════════════════════════════════════════════════
# Тело запроса: у провайдеров разные требования
# ════════════════════════════════════════════════════════════════════

def test_body_deepseek_needs_json_format_and_big_budget(analyzer):
    """DeepSeek без response_format/json_object уходит в reasoning и не отдаёт JSON."""
    body = analyzer._unified_body("deepseek", "deepseek-flash", [])
    assert body["response_format"] == {"type": "json_object"}
    assert body["max_tokens"] >= 32000


def test_body_local_disables_thinking_and_has_no_response_format(analyzer):
    body = analyzer._unified_body("local", "local-model", [])
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert "response_format" not in body


def test_body_routerai_keeps_json_format(analyzer):
    body = analyzer._unified_body("routerai", "qwen/qwen3-vl-235b-a22b-thinking", [])
    assert body["response_format"] == {"type": "json_object"}


# ════════════════════════════════════════════════════════════════════
# Фолбэк между провайдерами
# ════════════════════════════════════════════════════════════════════

class _Response:
    def __init__(self, payload=None, status: int = 200, text: str = ""):
        self._payload = payload
        self.status_code = status
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("POST", "http://test")
            raise httpx.HTTPStatusError(
                f"status {self.status_code}", request=request,
                response=httpx.Response(self.status_code),
            )

    def json(self):
        return self._payload


def _reply(content: str = '{"zone_type": "Кухня"}') -> dict:
    return {"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}


class _Client:
    """Заглушка httpx.AsyncClient: падает на одном URL, отвечает на другом."""

    def __init__(self, fail_urls=(), reply_content='{"zone_type": "Кухня"}'):
        self.fail_urls = set(fail_urls)
        self.reply_content = reply_content
        self.calls = []

    async def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append(url)
        if url in self.fail_urls:
            raise httpx.ConnectError("connection refused", request=httpx.Request("POST", url))
        return _Response(payload=_reply(self.reply_content))


def test_unified_request_skips_dead_provider(analyzer):
    """Локальный сервер лежит → запрос уходит к DeepSeek, а не заканчивается ничем."""
    client = _Client(fail_urls={"http://127.0.0.1:9/v1/chat/completions"})
    targets = analyzer._unified_targets()

    text = asyncio.run(analyzer._unified_request(client, targets, []))

    assert json.loads(text)["zone_type"] == "Кухня"
    assert client.calls[0].startswith("http://127.0.0.1:9")
    assert "api.deepseek.com" in client.calls[1]


def test_unified_request_empty_json_counts_as_failure(analyzer):
    """Пустой content (reasoning съел бюджет) → провайдер считается негодным."""
    client = _Client(reply_content="")
    targets = [("deepseek", "deepseek-flash"), ("routerai", "qwen/qwen3-vl-235b-a22b-thinking")]

    with pytest.raises(ValueError):
        asyncio.run(analyzer._unified_request(client, targets, []))
    assert len(client.calls) == 2  # попробовал обоих и только потом упал


# ════════════════════════════════════════════════════════════════════
# Повторы на 429/5xx (DeepSeek под нагрузкой отдавал 503)
# ════════════════════════════════════════════════════════════════════

class _FlakyClient:
    """Отдаёт заданную последовательность статусов, затем успех."""

    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.calls = 0

    async def post(self, url, headers=None, json=None, timeout=None):
        self.calls += 1
        status = self.statuses.pop(0) if self.statuses else 200
        if status != 200:
            return _Response(payload={"error": "overloaded"}, status=status)
        return _Response(payload=_reply('{"zone_type": "Кухня"}'))


def test_retry_on_503_then_success(analyzer, monkeypatch):
    """503 — временный сбой: один повтор спасает страницу (было: страница терялась)."""
    from app.services import image_analyzer as ia
    monkeypatch.setattr(ia, "_retry_delay", lambda *a, **k: 0.0)

    client = _FlakyClient([503, 200])
    text = asyncio.run(
        analyzer._unified_request(client, [("deepseek", "deepseek-flash")], [])
    )

    assert json.loads(text)["zone_type"] == "Кухня"
    assert client.calls == 2


def test_no_retry_on_connection_refused(analyzer, monkeypatch):
    """Выключенный локальный сервер — постоянная ошибка: сразу к следующему."""
    from app.services import image_analyzer as ia
    monkeypatch.setattr(ia, "_retry_delay", lambda *a, **k: 0.0)

    client = _Client(fail_urls={"http://127.0.0.1:9/v1/chat/completions"})
    targets = analyzer._unified_targets()
    asyncio.run(analyzer._unified_request(client, targets, []))

    # по одному запросу на провайдера, без повторов мёртвого локального
    assert sum(1 for u in client.calls if u.startswith("http://127.0.0.1:9")) == 1


class _TimeoutClient:
    """Отвечает таймаутом — так облако «висит», не отдавая ответ."""

    def __init__(self):
        self.calls = 0

    async def post(self, url, headers=None, json=None, timeout=None):
        self.calls += 1
        raise httpx.ReadTimeout("read timeout", request=httpx.Request("POST", url))


def test_timeout_is_not_retried(analyzer, monkeypatch):
    """180 с уже потрачены — сразу пробуем следующего, а не ждём ещё 2×180."""
    from app.services import image_analyzer as ia
    monkeypatch.setattr(ia, "_retry_delay", lambda *a, **k: 0.0)

    client = _TimeoutClient()
    with pytest.raises(httpx.ReadTimeout):
        asyncio.run(
            analyzer._unified_request(client, [("deepseek", "deepseek-flash")], [])
        )
    assert client.calls == 1
