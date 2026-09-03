"""
Глубокий анализ страниц 2 и 3:
- Показать RAW ответ AI (JSON)
- Показать OCR-данные по этим страницам
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app.services.pdf_parser import GLMOCRParser
from app.services.image_analyzer import GeminiImageAnalyzer
from app.services.pdf_renderer import render_page


async def analyze_page(pdf: Path, page_0based: int, label: str):
    print(f"\n{'='*70}")
    print(f"📄 {label}")
    print(f"{'='*70}")

    # ── OCR ──
    print("\n── OCR (спецификации/таблицы) ──")
    ocr = GLMOCRParser()
    ocr_result = await ocr.parse_pdf(pdf)
    for room in ocr_result.rooms:
        # Ищем комнаты с указанием этой страницы
        if f"page={page_0based}" in room.raw_text or room.name:
            print(f"  🏠 OCR Room: '{room.name}'")
            if room.materials:
                print(f"     Materials: {room.materials}")
            if room.notes:
                for n in room.notes[:5]:
                    print(f"     Note: {n[:200]}")
    # Покажем таблицы, относящиеся к странице
    for t in ocr_result.tables:
        if t.page == page_0based + 1 or t.page == 0:
            print(f"  📊 Table (page {t.page}): {t.rows[:3]}")

    await ocr.close()

    # ── Vision ──
    print("\n── Vision AI (RAW + parsed) ──")
    jpeg = render_page(pdf, page_0based)
    tmp = Path(f"temp/deep_pg{page_0based+1}.jpg")
    tmp.parent.mkdir(exist_ok=True)
    tmp.write_bytes(jpeg)

    # Делаем прямой запрос к AI без постобработки чтобы увидеть raw
    import base64, io, httpx
    from PIL import Image
    from app.config import settings

    img = Image.open(tmp)
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=85)
    img_b64 = base64.b64encode(buf.getvalue()).decode()

    prompt = """Ты — парсер мебельных чертежей. ВСЕ модули выпиши в JSON.

ВАЖНО: 
- Если на чертеже ДВА пенала — выведи ОБА (даже если одинаковые).
- Количество (quantity) — это число ОДИНАКОВЫХ модулей, НЕ сумма всех!
- Для острова: quantity=1 для каждого уникального размера, если не указано иное.
- НЕ суммируй разные модули в один!

ОБЯЗАТЕЛЬНЫЙ ФОРМАТ ОТВЕТА:
```json
{
  "zone_type": "Кухня",
  "materials": ["Материал1", "Материал2"],
  "modules": [
    {
      "type": "lower_base|upper_base|penal|corner",
      "width": 600,
      "depth": 560,
      "height": 820,
      "quantity": 1,
      "has_glass": false,
      "is_corner": false,
      "comment": "что написано на чертеже про этот модуль"
    }
  ],
  "confidence": "high",
  "notes": "любые наблюдения"
}
```

ТИПЫ:
- lower_base: напольный (~820-850мм высота, ~560мм глубина)
- upper_base: навесной (~700-920мм высота, ~320мм глубина)
- penal: высокий от пола (~2000-2400мм)
- corner: угловой квадратный (600×600, 900×900)

НЕ ПРОПУСКАЙ МОДУЛИ! Выведи ВСЁ что видишь."""

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
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}}
                    ]
                }],
                "temperature": 0.1,
                "max_tokens": 8000,
            }
        )
        resp.raise_for_status()
        data = resp.json()
        raw_text = data["choices"][0]["message"].get("content", "")
        print(f"  RAW ответ ({len(raw_text)} символов):")
        print(f"  {raw_text[:3000]}")
        if len(raw_text) > 3000:
            print(f"  ... (ещё {len(raw_text)-3000} символов)")

        # Попробуем распарсить JSON
        import re
        match = re.search(r'```json\s*(.*?)\s*```', raw_text, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(1))
                print(f"\n  📦 PARSED modules ({len(parsed.get('modules', []))}):")
                for i, m in enumerate(parsed.get('modules', []), 1):
                    print(f"    {i}. {m.get('type','?'):15s} "
                          f"{m.get('width','?')}×{m.get('depth','?')}×{m.get('height','?')}mm "
                          f"×{m.get('quantity',1)}  "
                          f"glass={m.get('has_glass',False)}  "
                          f"corner={m.get('is_corner',False)}")
                    if m.get('comment'):
                        print(f"       💬 {m['comment']}")
                print(f"  🎨 Materials: {parsed.get('materials',[])}")
                print(f"  🏠 Zone: {parsed.get('zone_type','?')}")
                print(f"  📝 Notes: {parsed.get('notes','')}")
            except json.JSONDecodeError as e:
                print(f"  ❌ JSON parse error: {e}")
        else:
            print("  ⚠️ No ```json block found in response")

    finally:
        await client.aclose()


async def main():
    pdf = Path(r"D:\БИЗНЕС\ПРО МЕБЕЛЬ\ПРОЕКТЫ\АНДЕЛИС\Альбом чертежей Рокоссовского-59-79_compressed.pdf")

    # Страница 2 (index 1) — Кухонный гарнитур
    await analyze_page(pdf, 1, "СТРАНИЦА 2 — Кухонный гарнитур")

    # Страница 3 (index 2) — Остров
    await analyze_page(pdf, 2, "СТРАНИЦА 3 — Остров")


if __name__ == "__main__":
    asyncio.run(main())
