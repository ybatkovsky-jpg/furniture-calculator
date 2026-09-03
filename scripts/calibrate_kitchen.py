"""
Калиброванный анализ кухни — говорим модели примерное число модулей.
"""
import asyncio, json, sys, base64, io, re
from pathlib import Path
import httpx
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from app.config import settings
from app.services.pdf_renderer import render_page


PROMPT = """Ты — конструктор-технолог мебельной фабрики. Проанализируй чертёж кухонного гарнитура.

ВАЖНЫЕ ПРАВИЛА (нарушение = брак):
1. Модуль = ОДИН физический корпус. Две дверцы на одном корпусе = 1 модуль.
2. НЕ дроби шкаф с несколькими фасадами на несколько модулей!
3. Общее число модулей должно быть ~8–14 для кухонного гарнитура.
4. Если видишь размерную линию или выноску — это НЕ модуль.
5. На чертеже могут быть планки-заполнители (40–80мм) — это НЕ модули.
6. Пенал — высокий шкаф от пола (~2100–2400мм). Если их два — укажи ОБА.
7. Угловой модуль — квадратный (600×600 или 900×900) — ОДИН модуль.

ФОРМАТ — ТОЛЬКО JSON:
```json
{
  "modules": [
    {
      "type": "penal|corner|lower_base|upper_base",
      "width": 600,
      "depth": 560,
      "height": 820,
      "quantity": 1,
      "has_glass": false,
      "is_corner": false,
      "comment": "кратко что за модуль"
    }
  ],
  "materials": [],
  "zone_type": "Кухня",
  "confidence": "high"
}
```"""


async def analyze(pdf: Path, page_0based: int, label: str):
    print(f"\n{'='*60}")
    print(f"📐 {label}")
    print(f"{'='*60}")

    jpeg = render_page(pdf, page_0based)
    img = Image.open(io.BytesIO(jpeg))

    # Не сжимаем — даём полное качество
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=92)
    img_b64 = base64.b64encode(buf.getvalue()).decode()

    client = httpx.AsyncClient(timeout=httpx.Timeout(180.0))
    try:
        resp = await client.post(
            "https://api.z.ai/api/paas/v4/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.zai_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "glm-5v-turbo",
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": PROMPT},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}}
                    ]
                }],
                "temperature": 0.0,
                "max_tokens": 8000,
            }
        )
        data = resp.json()
        raw = data["choices"][0]["message"].get("content", "")

        # Извлекаем JSON
        match = re.search(r'```json\s*(.*?)\s*```', raw, re.DOTALL)
        if match:
            parsed = json.loads(match.group(1))
        else:
            # Ищем { } с modules
            start = raw.find('{')
            end = raw.rfind('}') + 1
            parsed = json.loads(raw[start:end]) if start >= 0 else {}

        modules = parsed.get('modules', [])
        total_qty = sum(m.get('quantity', 1) for m in modules)

        print(f"📦 Найдено модулей: {len(modules)} типов, всего {total_qty} шт.")
        print()

        for i, m in enumerate(modules, 1):
            t = m.get('type', '?')
            w, d, h = m.get('width','?'), m.get('depth','?'), m.get('height','?')
            q = m.get('quantity', 1)
            g = '🪟' if m.get('has_glass') else ' '
            c = '📐' if m.get('is_corner') else ' '
            print(f"  {i:2d}. {g}{c} {t:15s} {str(w):>5s}×{str(d):>4s}×{str(h):>5s}mm ×{q}")
            if m.get('comment'):
                print(f"      💬 {m['comment']}")

        print(f"\n🎨 Материалы: {parsed.get('materials', [])}")
        print(f"🏠 Зона: {parsed.get('zone_type', '?')}")
        print(f"📝 Заметки: {parsed.get('notes', parsed.get('comment', ''))}")

        # Суммарная ширина для проверки
        total_width = sum(m.get('width', 0) * m.get('quantity', 1) for m in modules)
        print(f"📏 Суммарная ширина модулей: {total_width} мм")

    finally:
        await client.aclose()


async def main():
    pdf = Path(r"D:\БИЗНЕС\ПРО МЕБЕЛЬ\ПРОЕКТЫ\АНДЕЛИС\Альбом чертежей Рокоссовского-59-79_compressed.pdf")
    await analyze(pdf, 1, "СТРАНИЦА 2 — Кухонный гарнитур (калиброванный промпт)")
    await analyze(pdf, 2, "СТРАНИЦА 3 — Остров (калиброванный промпт)")


if __name__ == "__main__":
    asyncio.run(main())
