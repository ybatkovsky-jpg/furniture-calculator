"""
Пакетный прогон всех изображений из каталога с сохранением прогресса.
Можно прервать и продолжить — результаты кешируются в output/pages_cache.json.

Использование:
    python scripts/process_all_resumable.py [--images DIR] [--spec PATH]
                                            [--project NAME] [--out PATH]

По умолчанию берёт input_images/ в корне проекта, без спецификации,
имя проекта — «Проект из чертежей», Excel — output/Расчет_ПОЛНЫЙ_ВСЕ.xlsx.
"""
import asyncio, sys, json, logging, time, argparse
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)-7s %(message)s', datefmt='%H:%M:%S')
logging.getLogger('httpx').setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

from app.services.image_analyzer import GeminiImageAnalyzer, RecognizedModule
from app.services.full_pipeline import PipelineResult, RoomSpec, _calculate_quality
from app.services.template_filler import fill_template_from_pipeline
from app.services.project_spec import load_project_spec

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_IMAGES_DIR = ROOT / "input_images"
CACHE_FILE = ROOT / "output" / "pages_cache.json"
DEFAULT_TEMPLATE = ROOT / "templates" / "Таблица для расчетов пустая.xlsx"
DEFAULT_OUTPUT = ROOT / "output" / "Расчет_ПОЛНЫЙ_ВСЕ.xlsx"


def parse_args():
    p = argparse.ArgumentParser(description="Пакетный прогон изображений → Excel-смета (с кэшем)")
    p.add_argument("--images", type=Path, default=DEFAULT_IMAGES_DIR,
                   help="каталог с картинками (default: input_images/)")
    p.add_argument("--spec", type=Path, default=None,
                   help="project_spec.yaml проекта (default: без спецификации)")
    p.add_argument("--project", default="Проект из чертежей", help="имя проекта в Excel")
    p.add_argument("--out", type=Path, default=DEFAULT_OUTPUT, help="путь выходного .xlsx")
    return p.parse_args()


def load_cache() -> dict:
    if CACHE_FILE.exists():
        return json.loads(CACHE_FILE.read_text(encoding='utf-8'))
    return {}


def save_cache(cache: dict):
    CACHE_FILE.parent.mkdir(exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding='utf-8')


def modules_to_dict(modules: list) -> list:
    """Сериализовать модули в словарь."""
    result = []
    for m in modules:
        result.append({
            'type': m.type, 'width': m.width, 'depth': m.depth, 'height': m.height,
            'quantity': m.quantity, 'has_glass': m.has_glass, 'is_corner': m.is_corner,
            'drawers': m.drawers, 'facades': m.facades,
        })
    return result


def dict_to_modules(data: list) -> list:
    """Десериализовать словарь в модули."""
    result = []
    for d in data:
        result.append(RecognizedModule(
            type=d['type'], width=d['width'], depth=d['depth'], height=d['height'],
            quantity=d.get('quantity', 1), has_glass=d.get('has_glass', False),
            is_corner=d.get('is_corner', False), drawers=d.get('drawers'),
            facades=d.get('facades'),
        ))
    return result


async def process_page(analyzer, img_path: Path, page_num: int) -> dict:
    """Обработать одну страницу, вернуть словарь с результатом."""
    try:
        modules, zone, mats, conf = await analyzer.analyze_page(img_path)
        if modules:
            return {'page': page_num, 'zone': zone, 'materials': mats,
                    'confidence': conf, 'modules': modules_to_dict(modules), 'method': 'SCALE'}
    except Exception:
        pass
    
    # Fallback
    try:
        fb = await analyzer.analyze_drawing(img_path)
        if fb.modules:
            return {'page': page_num, 'zone': fb.zone_type, 'materials': fb.materials_mentioned,
                    'confidence': fb.confidence, 'modules': modules_to_dict(fb.modules), 'method': 'GLM-5V'}
    except Exception:
        pass
    
    return {'page': page_num, 'zone': None, 'materials': [], 'confidence': 'low', 'modules': [], 'method': 'FAIL'}


async def main():
    args = parse_args()
    images_dir, spec_path, project_name, output_path = (
        args.images, args.spec, args.project, args.out)
    template_path = DEFAULT_TEMPLATE

    images = sorted(images_dir.glob("*.jpg"))
    if not images:
        print(f"❌ Нет картинок в {images_dir}"); return
    
    cache = load_cache()
    analyzer = GeminiImageAnalyzer()
    t0 = time.monotonic()
    
    try:
        for i, img_path in enumerate(images):
            page_num = i + 1
            cache_key = str(page_num)
            
            # Пропускаем уже обработанные
            if cache_key in cache and cache[cache_key].get('modules'):
                entry = cache[cache_key]
                print(f"⏭️  Стр.{page_num}: уже в кеше ({len(entry['modules'])} мод.)")
                continue
            
            print(f"🖼️  Стр.{page_num}/{len(images)}: {img_path.name}...", end=' ', flush=True)
            
            entry = await process_page(analyzer, img_path, page_num)
            cache[cache_key] = entry
            save_cache(cache)
            
            if entry['modules']:
                drw = sum(1 for m in entry['modules'] if m.get('drawers'))
                print(f"{len(entry['modules'])} мод., {drw} ящ. ({entry['method']})")
            else:
                print("пусто")
            
            elapsed = time.monotonic() - t0
            remaining = len(images) - i - 1
            eta = (elapsed / (i + 1)) * remaining if i > 0 else 0
            print(f"   ⏱️  {elapsed:.0f}с | осталось ~{eta:.0f}с")
        
        # ── Сборка Excel ──
        rooms = []
        for key in sorted(cache.keys(), key=int):
            entry = cache[key]
            if not entry.get('modules'):
                continue
            modules = dict_to_modules(entry['modules'])
            zone = entry.get('zone') or f"Стр.{entry['page']}"
            room = RoomSpec(
                room_name=zone, page=entry['page'], zone_type=entry.get('zone'),
                modules=modules, materials=entry.get('materials', []),
                confidence=entry.get('confidence', 'medium'),
            )
            _calculate_quality(room)  # QC-скор и флаги для листа «Контроль качества»
            rooms.append(room)
        
        if rooms:
            result = PipelineResult(success=True, project_name=project_name, rooms=rooms)
            spec = load_project_spec(str(spec_path)) if spec_path and spec_path.exists() else None
            output_path.parent.mkdir(exist_ok=True)
            fill_template_from_pipeline(result, str(template_path), str(output_path), project_spec=spec)
            
            total_mods = sum(sum(m.quantity for m in r.modules) for r in rooms)
            total_drw = sum(sum(1 for m in r.modules if m.drawers) for r in rooms)
            print(f"\n{'='*60}")
            print(f"✅ {len(rooms)} помещений, {total_mods} модулей, {total_drw} с ящиками")
            print(f"📊 {output_path}")
            print(f"{'='*60}")
        else:
            print("❌ Модули не найдены")
    
    finally:
        await analyzer.close()
        save_cache(cache)


if __name__ == "__main__":
    asyncio.run(main())
