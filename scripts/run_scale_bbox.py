"""
SCALE_PROMPT подход: модель даёт bbox + ОДНО число габарита → Python считает.
Самый точный метод (88-93%), без cognitive overload от модулей.
"""
import asyncio, sys, json, base64, io, logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)-7s %(message)s', datefmt='%H:%M:%S')
logging.getLogger('httpx').setLevel(logging.WARNING)

import httpx
from PIL import Image, ImageEnhance, ImageFilter
from app.config import settings
from app.services.scale_calc import calculate_scaled_facades
from app.services.furniture_defaults import get_rules
from app.services.image_analyzer import RecognizedModule
from app.services.full_pipeline import PipelineResult, RoomSpec
from app.services.template_filler import fill_template_from_pipeline

IMAGES_DIR = Path(r"D:\БИЗНЕС\ПРО МЕБЕЛЬ\ПРОЕКТЫ\АНДЕЛИС\картинки")
TEMPLATE = Path(__file__).parent / "templates" / "Таблица для расчетов пустая.xlsx"
OUTPUT = Path(__file__).parent / "output" / "Расчет_SCALE_BBOX.xlsx"

# SCALE_PROMPT — только bbox + габарит, без модулей
SCALE_PROMPT_V3 = """Ты — конструктор-технолог мебельной фабрики. Проанализируй чертёж корпусной мебели.

═══════════════════════════════════════
ЗАДАЧА 1: ОБЩИЙ ГАБАРИТ (total_width_mm)
═══════════════════════════════════════
Найди размерную линию над ВСЕМ рядом нижних шкафов. Это горизонтальная линия со СТРЕЛКАМИ на концах (← →) и ОДНИМ числом. Она охватывает ВСЕ нижние модули от левого края до правого.

ПРОВЕРЬ СЕБЯ:
- Число должно быть >1000мм и <8000мм
- Если число <1000 — это размер отдельного шкафа, ищи линию над ВСЕМ рядом
- Если несколько размерных линий — бери САМУЮ ДЛИННУЮ
- Типичные значения: 1800, 2400, 3000, 3600, 4200, 4800
- Если размерная линия не читается — total_width_mm=0

═══════════════════════════════════════
ЗАДАЧА 2: ВСЕ ДВЕРЦЫ С BBOX (facades)
═══════════════════════════════════════
Перечисли АБСОЛЮТНО ВСЕ видимые дверцы на чертеже. Каждая дверца = ОДИН элемент facades.
Считай их по одной, слева направо. НЕ группируй! НЕ пропускай!

Для КАЖДОЙ дверцы:
- zone: "lower" | "upper" | "penal"
- bbox_x_pct: позиция ЛЕВОГО края дверцы в % от ширины ИЗОБРАЖЕНИЯ (0-100)
- bbox_w_pct: ШИРИНА дверцы в % от ширины ИЗОБРАЖЕНИЯ (0-100)
- is_corner: true ТОЛЬКО для угловой (она заметно шире остальных)

ВАЖНО:
★ Оценивай проценты с точностью до 1% (НЕ округляй до 5 или 10!)
★ Сумма bbox_w_pct всех НИЖНИХ дверей = их доля на изображении
★ Пеналы НЕ включай в сумму нижних (они с краю, отдельно)
★ Планки-заполнители (40-80мм) НЕ учитывай
★ Дверца с треугольником открывания = одна дверца (треугольник это маркер)
★ Дверца с штриховкой/сеткой = стекло → has_glass_facades

═══════════════════════════════════════
ПРИМЕР: прямая кухня (5 дверей)
═══════════════════════════════════════
{"zone_type":"Кухня","materials":["EGGER H1379"],"total_width_mm":3000,
 "facades":[
   {"zone":"lower","bbox_x_pct":3,"bbox_w_pct":19,"is_corner":false},
   {"zone":"lower","bbox_x_pct":23,"bbox_w_pct":19,"is_corner":false},
   {"zone":"lower","bbox_x_pct":43,"bbox_w_pct":19,"is_corner":false},
   {"zone":"upper","bbox_x_pct":3,"bbox_w_pct":19,"is_corner":false},
   {"zone":"upper","bbox_x_pct":23,"bbox_w_pct":19,"is_corner":false}
 ],"has_glass_facades":[],"confidence":"high","notes":""}

═══════════════════════════════════════
ПРИМЕР: угловая кухня с пеналом (7 дверей)
═══════════════════════════════════════
{"zone_type":"Кухня","materials":["EGGER H3158"],"total_width_mm":3300,
 "facades":[
   {"zone":"lower","bbox_x_pct":2,"bbox_w_pct":26,"is_corner":true},
   {"zone":"lower","bbox_x_pct":28,"bbox_w_pct":13,"is_corner":false},
   {"zone":"lower","bbox_x_pct":41,"bbox_w_pct":13,"is_corner":false},
   {"zone":"lower","bbox_x_pct":54,"bbox_w_pct":14,"is_corner":false},
   {"zone":"upper","bbox_x_pct":28,"bbox_w_pct":13,"is_corner":false},
   {"zone":"upper","bbox_x_pct":41,"bbox_w_pct":13,"is_corner":false},
   {"zone":"penal","bbox_x_pct":72,"bbox_w_pct":13,"is_corner":false}
 ],"has_glass_facades":[3,7],"confidence":"high","notes":""}

═══════════════════════════════════════
ПРИМЕР: большая кухня (16 дверей)
═══════════════════════════════════════
{"zone_type":"Кухня","materials":["EGGER H1379","EGGER H3158"],"total_width_mm":3600,
 "facades":[
   {"zone":"lower","bbox_x_pct":2,"bbox_w_pct":10,"is_corner":false},
   {"zone":"lower","bbox_x_pct":12,"bbox_w_pct":10,"is_corner":false},
   {"zone":"lower","bbox_x_pct":22,"bbox_w_pct":10,"is_corner":false},
   {"zone":"lower","bbox_x_pct":32,"bbox_w_pct":10,"is_corner":false},
   {"zone":"lower","bbox_x_pct":42,"bbox_w_pct":10,"is_corner":false},
   {"zone":"lower","bbox_x_pct":52,"bbox_w_pct":10,"is_corner":false},
   {"zone":"upper","bbox_x_pct":2,"bbox_w_pct":9,"is_corner":false},
   {"zone":"upper","bbox_x_pct":11,"bbox_w_pct":9,"is_corner":false},
   {"zone":"upper","bbox_x_pct":20,"bbox_w_pct":9,"is_corner":false},
   {"zone":"upper","bbox_x_pct":29,"bbox_w_pct":9,"is_corner":false},
   {"zone":"upper","bbox_x_pct":38,"bbox_w_pct":9,"is_corner":false},
   {"zone":"upper","bbox_x_pct":47,"bbox_w_pct":9,"is_corner":false},
   {"zone":"upper","bbox_x_pct":56,"bbox_w_pct":9,"is_corner":false},
   {"zone":"penal","bbox_x_pct":70,"bbox_w_pct":10,"is_corner":false},
   {"zone":"penal","bbox_x_pct":80,"bbox_w_pct":10,"is_corner":false},
   {"zone":"penal","bbox_x_pct":91,"bbox_w_pct":10,"is_corner":false}
 ],"has_glass_facades":[1,9],"confidence":"high","notes":""}

ОПРЕДЕЛИ ЗОНУ: Кухня, Гостиная, Спальня, Детская, Прихожая, Ванная, Гардеробная, Кабинет, Балкон, Столовая, Постирочная.
МАТЕРИАЛЫ: перечисли декоры если указаны (EGGER, и т.п.).

Верни ТОЛЬКО JSON: zone_type, materials, total_width_mm, facades, has_glass_facades, confidence, notes.
confidence: "high" если габарит прочитан; "medium" если часть размеров оценена; "low" если много предположений."""


def facades_to_modules(scaled_facades, zone_type, glass_indices):
    """Python: дверцы → модули (без помощи модели!)."""
    if not scaled_facades:
        return []
    
    rules = get_rules(zone_type)
    glass_indices = glass_indices or set()
    modules = []
    
    by_zone = {}
    for i, sf in enumerate(scaled_facades):
        zone = sf.zone if sf.zone in ("lower", "upper", "penal") else "lower"
        by_zone.setdefault(zone, []).append((i, sf))
    
    for zone in ["lower", "upper", "penal"]:
        zone_items = by_zone.get(zone, [])
        if not zone_items:
            continue
        
        zone_items.sort(key=lambda x: x[1].width_mm)
        
        i = 0
        while i < len(zone_items):
            idx, sf = zone_items[i]
            
            is_corner = getattr(sf, 'is_corner', False) or (
                zone == "lower" and 800 <= sf.width_mm <= 1100
            )
            
            if is_corner:
                modules.append(RecognizedModule(
                    type="corner", width=sf.width_mm, depth=sf.width_mm,
                    height=sf.height_mm, quantity=1, is_corner=True,
                    has_glass=idx in glass_indices,
                    facades={"count": 1, "type": "doors"},
                ))
                i += 1
                continue
            
            if zone == "penal":
                modules.append(RecognizedModule(
                    type="penal", width=sf.width_mm,
                    depth=rules.default_depth_lower, height=sf.height_mm,
                    quantity=1, has_glass=idx in glass_indices,
                    facades={"count": 1, "type": "doors"},
                ))
                i += 1
                continue
            
            # Группируем одинаковые по ширине (±30mm)
            group = [(idx, sf)]
            j = i + 1
            while j < len(zone_items):
                jdx, jsf = zone_items[j]
                if abs(jsf.width_mm - sf.width_mm) <= 30:
                    group.append((jdx, jsf))
                    j += 1
                else:
                    break
            
            avg_w = sum(g[1].width_mm for g in group) // len(group)
            avg_h = sum(g[1].height_mm for g in group) // len(group)
            any_glass = any(g[0] in glass_indices for g in group)
            
            mtype = "upper_base" if zone == "upper" else "lower_base"
            depth = rules.default_depth_upper if zone == "upper" else rules.default_depth_lower
            
            modules.append(RecognizedModule(
                type=mtype, width=avg_w, depth=depth, height=avg_h,
                quantity=len(group), has_glass=any_glass,
                facades={"count": 1, "type": "doors"},
            ))
            i = j
    
    return modules


async def process_page(img_path, page_num):
    """SCALE_PROMPT: bbox → scale_calc → модули."""
    # Предобработка
    image = Image.open(img_path)
    enhancer = ImageEnhance.Contrast(image); image = enhancer.enhance(1.3)
    image = image.filter(ImageFilter.SHARPEN)
    max_size = 1536
    if image.width > max_size or image.height > max_size:
        ratio = min(max_size / image.width, max_size / image.height)
        image = image.resize((int(image.width * ratio), int(image.height * ratio)), Image.Resampling.LANCZOS)
    
    buffer = io.BytesIO(); image.save(buffer, format='JPEG', quality=85)
    b64 = base64.b64encode(buffer.getvalue()).decode()
    
    body = {
        'model': 'qwen/qwen3-vl-235b-a22b-thinking',
        'messages': [{'role': 'user', 'content': [
            {'type': 'text', 'text': SCALE_PROMPT_V3},
            {'type': 'image_url', 'image_url': {'url': f'data:image/jpeg;base64,{b64}'}}
        ]}],
        'temperature': 0.0, 'max_tokens': 8000,
        'response_format': {'type': 'json_object'},
    }
    
    async with httpx.AsyncClient(timeout=httpx.Timeout(180.0)) as client:
        resp = await client.post(
            'https://routerai.ru/api/v1/chat/completions',
            headers={'Authorization': f'Bearer {settings.routerai_api_key}', 'Content-Type': 'application/json'},
            json=body
        )
        data = resp.json()
        content = data['choices'][0]['message']['content']
    
    parsed = json.loads(content)
    total_width_mm = parsed.get('total_width_mm', 0)
    facades_data = parsed.get('facades', [])
    glass_indices = set(parsed.get('has_glass_facades', []))
    zone_type = parsed.get('zone_type')
    materials = parsed.get('materials', [])
    confidence = parsed.get('confidence', 'medium')
    
    print(f"  Стр.{page_num}: габарит={total_width_mm}мм, дверей={len(facades_data)}, confidence={confidence}")
    
    if total_width_mm <= 0 or not facades_data:
        print(f"    ⚠️ Нет данных для масштаба")
        return page_num, [], zone_type, materials, 'low'
    
    scaled = calculate_scaled_facades(facades_data, total_width_mm)
    modules = facades_to_modules(scaled, zone_type, glass_indices)
    
    return page_num, modules, zone_type, materials, confidence


async def main():
    images = sorted(IMAGES_DIR.glob("*.jpg"))
    # Тест: только страницы 2-5
    images = [p for p in images if int(p.stem.split('_page-')[-1]) in (2, 3, 4, 5)]
    print(f"📁 {len(images)} страниц | 📐 SCALE_PROMPT (bbox + габарит)")
    
    rooms = []
    total_mods = 0
    
    for i, img_path in enumerate(images):
        page_num = i + 1
        print(f"\n🖼️  Стр. {page_num}/{len(images)}: {img_path.name}")
        
        try:
            pn, modules, zone_type, materials, confidence = await process_page(img_path, page_num)
        except Exception as e:
            print(f"  ❌ Ошибка: {e}")
            continue
        
        if modules:
            zone_ru = zone_type or f"Стр.{page_num}"
            room = RoomSpec(
                room_name=zone_ru, page=page_num, zone_type=zone_type,
                modules=modules, materials=materials, confidence=confidence,
            )
            rooms.append(room)
            total_mods += len(modules)
            
            for m in modules:
                c = '📐' if m.is_corner else '  '
                print(f"    {c} {m.type}: {m.width}×{m.depth}×{m.height}mm ×{m.quantity}")
        else:
            print(f"  ⏭️  Без модулей")
    
    print(f"\n{'='*60}")
    print(f"✅ {len(rooms)} помещений, {total_mods} модулей")
    print(f"{'='*60}")
    
    if rooms:
        result = PipelineResult(success=True, project_name="Рокоссовского 59-79", rooms=rooms)
        OUTPUT.parent.mkdir(exist_ok=True)
        fill_template_from_pipeline(result, str(TEMPLATE), str(OUTPUT))
        print(f"\n📊 Excel: {OUTPUT}")

asyncio.run(main())
