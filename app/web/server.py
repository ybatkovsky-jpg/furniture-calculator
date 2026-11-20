"""Веб-интерфейс для локальной обкатки: чертёж → смета.

Запуск:
    .\\.venv\\Scripts\\python.exe -m uvicorn app.web.server:app --host 127.0.0.1 --port 8000

Только для локального использования: сервер слушает 127.0.0.1, авторизации нет.
Расчёт идёт тем же путём, что у бота и Excel: analyze_page → calculate_full_cost,
документы берут снимок `quote` (quote_from_result), второго счётчика нет.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from app.services.calc_engine import calculate_full_cost
from app.services.calculation_persist import quote_from_result
from app.services.image_analyzer import GeminiImageAnalyzer
from app.services.recognition_cache import module_to_dict
from app.services.template_filler import fill_template_from_quote

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "templates" / "Таблица для расчетов пустая.xlsx"
DB_PATH = ROOT / "data" / "furniture.db"
OUT_DIR = ROOT / "temp" / "web"

# Снимки расчётов в памяти: id → quote. Локальная обкатка, живут до перезапуска.
QUOTES: dict[str, dict[str, Any]] = {}

TYPE_LABELS = {
    "lower_base": "Нижняя база",
    "upper_base": "Верхний модуль",
    "penal": "Пенал",
    "wardrobe": "Шкаф",
    "table": "Стол",
    "counter": "Стойка",
    "open_unit": "Открытая тумба",
    "shelf_unit": "Стеллаж",
    "column": "Колонна",
    "tumbler": "Тумба",
    "corner": "Угловой",
}

app = FastAPI(title="ПРО МЕБЕЛЬ — расчёт", docs_url=None, redoc_url=None)


def _price_item(connection: sqlite3.Connection, category: str) -> dict | None:
    """Самый дешёвый активный материал категории — значение по умолчанию."""
    row = connection.execute(
        "select * from price_items where is_active=1 and category=? "
        "and unit_price is not null order by unit_price asc limit 1",
        (category,),
    ).fetchone()
    return dict(row) if row else None


def default_materials() -> tuple[dict, dict]:
    """Материалы и фурнитура по умолчанию из прайса.

    MVP: выбор материалов в вебе пока не делается, берём базовые позиции
    из БД, чтобы смета была в рублях, а не нулевая.
    """
    connection = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        ldsp = _price_item(connection, "ЛДСП")
        edge = _price_item(connection, "Кромка")
        facade = _price_item(connection, "Фасады") or _price_item(connection, "МДФ плиты")
        hinges = _price_item(connection, "Петли")
        drawers = _price_item(connection, "Ящики")
    finally:
        connection.close()

    materials: dict[str, Any] = {}
    if ldsp:
        materials["ldsp"] = {
            "id": ldsp["id"],
            "name": ldsp["name"],
            "price": ldsp["unit_price"],
            "price_unit": ldsp["price_unit"],
            "thickness_mm": ldsp["thickness_mm"],
            "brand": ldsp["brand"],
        }
    if edge:
        materials["edge"] = {
            "id": edge["id"],
            "name": edge["name"],
            "price": edge["unit_price"],
            "price_unit": edge["price_unit"],
        }
    if facade:
        materials["facades"] = {
            "id": facade["id"],
            "name": facade["name"],
            "price": facade["unit_price"],
            "price_unit": facade["price_unit"],
        }

    hardware: dict[str, Any] = {}
    if hinges:
        hardware["hinges"] = {
            "id": hinges["id"],
            "name": hinges["name"],
            "price": hinges["unit_price"],
        }
    if drawers:
        hardware["drawers"] = {
            "id": drawers["id"],
            "name": drawers["name"],
            "price": drawers["unit_price"],
        }
    return materials, hardware


def _module_row(module: Any) -> dict:
    """Строка таблицы: тип по-русски, габариты, двери, ящики."""
    kind = str(getattr(module, "type", "") or "")
    facades = getattr(module, "facades", None) or {}
    drawers = getattr(module, "drawers", None) or {}
    return {
        "type": TYPE_LABELS.get(kind, kind or "модуль"),
        "width": int(getattr(module, "width", 0) or 0),
        "depth": int(getattr(module, "depth", 0) or 0),
        "height": int(getattr(module, "height", 0) or 0),
        "quantity": int(getattr(module, "quantity", 1) or 1),
        "doors": int(facades.get("count") or 0) if isinstance(facades, dict) else 0,
        "drawers": int(drawers.get("count") or 0) if isinstance(drawers, dict) else 0,
    }


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return INDEX_HTML


@app.post("/api/analyze")
async def analyze(file: UploadFile = File(...)) -> JSONResponse:  # noqa: B008
    """Принять чертёж, распознать состав и посчитать смету."""
    suffix = Path(file.filename or "upload.jpg").suffix.lower() or ".jpg"
    if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}:
        raise HTTPException(400, "Поддерживаются изображения: JPG, PNG, WEBP, BMP")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    temp_path = OUT_DIR / f"{uuid.uuid4().hex}{suffix}"

    try:
        temp_path.write_bytes(await file.read())
    except OSError as exc:
        raise HTTPException(500, f"Не удалось сохранить файл: {exc}") from exc

    analyzer = GeminiImageAnalyzer()
    try:
        modules, zone_type, _materials, confidence, meta = await analyzer.analyze_page(
            temp_path, return_meta=True
        )
    except Exception as exc:
        logger.exception("Распознавание не удалось")
        raise HTTPException(502, f"Распознавание не удалось: {exc}") from exc
    finally:
        await analyzer.close()

    if not modules:
        flags = list(meta.get("quality_flags") or [])
        return JSONResponse(
            {
                "ok": False,
                "reason": flags[0] if flags else "модули не найдены",
                "flags": flags,
                "quality_score": meta.get("quality_score"),
            }
        )

    materials, hardware = default_materials()
    module_dicts = [module_to_dict(module) for module in modules]
    result = calculate_full_cost(
        module_dicts, materials, hardware, [], zone_type=zone_type
    )
    quote = quote_from_result(result, reason="web")
    quote_id = uuid.uuid4().hex
    QUOTES[quote_id] = quote

    breakdown = result.cost_breakdown
    return JSONResponse(
        {
            "ok": True,
            "quote_id": quote_id,
            "zone": zone_type,
            "confidence": confidence,
            "total_width_mm": int(meta.get("total_width_mm") or 0) or sum(
                row["width"] for row in (_module_row(m) for m in modules)
            ),
            "doors": int(meta.get("door_count") or 0),
            "drawers": int(meta.get("drawer_count") or 0),
            "modules": [_module_row(module) for module in modules],
            "totals": {
                "cost_price": round(breakdown.cost_price),
                "subtotal": round(breakdown.subtotal),
                "final_cash": round(breakdown.final_cash),
                "final_noncash": round(result.total_noncash),
            },
            "flags": list(meta.get("quality_flags") or []),
            "quality_score": meta.get("quality_score"),
            "can_issue_kp": bool(result.can_issue_kp),
            "missing_prices": list(result.missing_prices or []),
        }
    )


@app.get("/api/excel/{quote_id}")
async def excel(quote_id: str) -> FileResponse:
    """Excel из снимка расчёта — тот же путь, что у бота."""
    quote = QUOTES.get(quote_id)
    if not quote:
        raise HTTPException(404, "Расчёт не найден — распознайте заново")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    output = OUT_DIR / f"расчёт-{quote_id[:8]}.xlsx"
    fill_template_from_quote(quote, str(TEMPLATE), str(output))
    return FileResponse(
        output,
        filename="Расчёт.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


INDEX_HTML = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>ПРО МЕБЕЛЬ — расчёт по чертежу</title>
<style>
  :root { color-scheme: light; }
  body { margin:0; font-family: "Segoe UI", system-ui, sans-serif; background:#f4f5f7; color:#1d2330; }
  header { background:#1d2330; color:#fff; padding:18px 28px; }
  header h1 { margin:0; font-size:18px; font-weight:600; letter-spacing:.2px; }
  header span { color:#9aa4b5; font-size:13px; margin-left:10px; }
  main { max-width:980px; margin:26px auto; padding:0 20px; }
  .drop { background:#fff; border:2px dashed #c3cad6; border-radius:12px; padding:34px;
          text-align:center; transition:.15s; cursor:pointer; }
  .drop.hover { border-color:#2f6fed; background:#f0f5ff; }
  .drop p { margin:6px 0; color:#5b6472; }
  .drop strong { font-size:16px; color:#1d2330; }
  .hidden { display:none; }
  .spinner { width:22px; height:22px; border:3px solid #d6dce6; border-top-color:#2f6fed;
             border-radius:50%; animation:spin .8s linear infinite; display:inline-block;
             vertical-align:middle; margin-right:10px; }
  @keyframes spin { to { transform:rotate(360deg); } }
  .card { background:#fff; border-radius:12px; padding:22px 24px; margin-top:20px;
          box-shadow:0 1px 3px rgba(20,30,50,.08); }
  .cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:12px; }
  .kpi { background:#f7f9fc; border-radius:10px; padding:14px 16px; }
  .kpi b { display:block; font-size:22px; margin-top:4px; }
  .kpi span { color:#6b7484; font-size:12px; text-transform:uppercase; letter-spacing:.4px; }
  table { width:100%; border-collapse:collapse; margin-top:14px; font-size:14px; }
  th, td { text-align:left; padding:9px 10px; border-bottom:1px solid #eceff4; }
  th { color:#6b7484; font-weight:600; font-size:12px; text-transform:uppercase; letter-spacing:.4px; }
  td.num, th.num { text-align:right; font-variant-numeric:tabular-nums; }
  .flag { background:#fff6e5; border-left:4px solid #e0a02a; padding:10px 14px;
          border-radius:8px; margin-top:10px; font-size:14px; }
  .button { display:inline-block; margin-top:16px; margin-right:10px; padding:11px 20px;
            background:#2f6fed; color:#fff; border:0; border-radius:9px; font-size:14px;
            cursor:pointer; text-decoration:none; }
  .button.secondary { background:#e8edf6; color:#243044; }
  .error { background:#fdecec; border-left:4px solid #d64545; padding:12px 16px;
           border-radius:8px; margin-top:16px; }
  .muted { color:#6b7484; font-size:13px; }
</style>
</head>
<body>
<header><h1>ПРО МЕБЕЛЬ<span>расчёт по чертежу</span></h1></header>
<main>
  <div id="drop" class="drop">
    <p><strong>Перетащите чертёж сюда</strong></p>
    <p>или нажмите, чтобы выбрать файл</p>
    <p class="muted">JPG, PNG, WEBP, BMP</p>
    <input id="file" type="file" accept="image/*" class="hidden">
  </div>

  <div id="status" class="card hidden"><span class="spinner"></span><span id="status-text">Распознаю…</span></div>
  <div id="error" class="error hidden"></div>

  <div id="result" class="hidden">
    <div class="card">
      <div class="cards" id="kpi"></div>
      <div id="flags"></div>
    </div>
    <div class="card">
      <h3 style="margin:0 0 4px;font-size:15px">Состав</h3>
      <table>
        <thead><tr>
          <th>Модуль</th><th class="num">Ш</th><th class="num">Г</th><th class="num">В</th>
          <th class="num">Корпусов</th><th class="num">Двери</th><th class="num">Ящики</th>
        </tr></thead>
        <tbody id="modules"></tbody>
      </table>
      <button class="button" id="excel">Скачать Excel</button>
      <button class="button secondary" id="reset">Новый расчёт</button>
    </div>
  </div>
</main>
<script>
const drop = document.getElementById('drop');
const fileInput = document.getElementById('file');
const status = document.getElementById('status');
const statusText = document.getElementById('status-text');
const errorBox = document.getElementById('error');
const result = document.getElementById('result');
let quoteId = null;

drop.addEventListener('click', () => fileInput.click());
drop.addEventListener('dragover', e => { e.preventDefault(); drop.classList.add('hover'); });
drop.addEventListener('dragleave', () => drop.classList.remove('hover'));
drop.addEventListener('drop', e => {
  e.preventDefault(); drop.classList.remove('hover');
  if (e.dataTransfer.files.length) upload(e.dataTransfer.files[0]);
});
fileInput.addEventListener('change', () => { if (fileInput.files.length) upload(fileInput.files[0]); });

function money(value) {
  return new Intl.NumberFormat('ru-RU').format(Math.round(value)) + ' ₽';
}

async function upload(file) {
  errorBox.classList.add('hidden');
  result.classList.add('hidden');
  status.classList.remove('hidden');
  statusText.textContent = 'Распознаю чертёж — это занимает до минуты…';

  const form = new FormData();
  form.append('file', file);
  try {
    const response = await fetch('/api/analyze', { method: 'POST', body: form });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Ошибка распознавания');
    if (!data.ok) {
      status.classList.add('hidden');
      errorBox.textContent = 'Не удалось получить состав: ' + (data.reason || 'модули не найдены');
      errorBox.classList.remove('hidden');
      return;
    }
    render(data);
  } catch (err) {
    status.classList.add('hidden');
    errorBox.textContent = err.message || 'Не удалось связаться с сервером';
    errorBox.classList.remove('hidden');
  }
}

function render(data) {
  quoteId = data.quote_id;
  const totals = data.totals;
  document.getElementById('kpi').innerHTML = [
    ['Габарит', (data.total_width_mm || 0) + ' мм'],
    ['Двери', data.doors],
    ['Ящики', data.drawers],
    ['Себестоимость', money(totals.cost_price)],
    ['Клиенту (наличные)', money(totals.final_cash)],
    ['Клиенту (безнал)', money(totals.final_noncash)],
  ].map(([label, value]) => `<div class="kpi"><span>${label}</span><b>${value}</b></div>`).join('');

  const flags = [...(data.flags || [])];
  if ((data.missing_prices || []).length) {
    flags.push('без цены в прайсе: ' + data.missing_prices.join(', '));
  }
  if (data.can_issue_kp === false) {
    flags.push('КП блокируется — есть позиции без цены, нужен счёт поставщика');
  }
  document.getElementById('flags').innerHTML = flags.length
    ? flags.map(f => `<div class="flag">⚠ ${f}</div>`).join('')
    : '';

  document.getElementById('modules').innerHTML = (data.modules || []).map(m => `
    <tr>
      <td>${m.type}</td>
      <td class="num">${m.width}</td>
      <td class="num">${m.depth}</td>
      <td class="num">${m.height}</td>
      <td class="num">${m.quantity}</td>
      <td class="num">${m.doors}</td>
      <td class="num">${m.drawers}</td>
    </tr>`).join('');

  status.classList.add('hidden');
  result.classList.remove('hidden');
}

document.getElementById('excel').addEventListener('click', () => {
  if (quoteId) window.location = '/api/excel/' + quoteId;
});
document.getElementById('reset').addEventListener('click', () => {
  result.classList.add('hidden');
  fileInput.value = '';
});
</script>
</body>
</html>
"""
