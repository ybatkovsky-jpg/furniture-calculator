"""
Распознавание только кухни из PDF-альбома чертежей.
Запуск: python run_kitchen_only.py
"""

import asyncio
import sys
from pathlib import Path

# Добавляем проект в path
sys.path.insert(0, str(Path(__file__).parent))

from app.services.full_pipeline import FullPipeline, print_result, PipelineResult, RoomSpec


# Ключевые слова, однозначно указывающие на КУХНЮ
KITCHEN_KEYWORDS = [
    "кухонный гарнитур", "кухня", "кухн", "остров",
    "kitchen",
]

# Ключевые слова, однозначно указывающие НЕ на кухню
NON_KITCHEN_KEYWORDS = [
    "гостин", "спальн", "детск", "прихож", "ванн",
    "гардероб", "кабинет", "балкон", "постирочн", "санузел",
    "столов", "living", "bedroom", "wardrobe", "bathroom",
]


def is_kitchen_room(room: RoomSpec) -> bool:
    """Проверить, относится ли помещение к кухне (только по названию)."""
    name_lower = room.room_name.lower()

    # Сначала исключаем: если в названии явно указана другая комната
    for kw in NON_KITCHEN_KEYWORDS:
        if kw in name_lower:
            return False

    # Затем включаем: если есть кухонные ключевые слова
    for kw in KITCHEN_KEYWORDS:
        if kw in name_lower:
            return True

    # Если название не содержит ни тех, ни других — проверяем модули:
    # кухня = есть corner ИЛИ есть и lower_base и upper_base вместе
    has_lower = any((m.type or "").lower() == "lower_base" for m in room.modules)
    has_upper = any((m.type or "").lower() == "upper_base" for m in room.modules)
    has_corner = any((m.type or "").lower() == "corner" for m in room.modules)

    if has_corner:
        return True
    if has_lower and has_upper:
        return True

    return False


def filter_kitchen(result: PipelineResult) -> PipelineResult:
    """Оставить только кухонные помещения."""
    kitchen_rooms = [r for r in result.rooms if is_kitchen_room(r)]
    result.rooms = kitchen_rooms
    result.success = len(kitchen_rooms) > 0
    return result


async def main():
    pdf_path = Path(
        r"D:\БИЗНЕС\ПРО МЕБЕЛЬ\ПРОЕКТЫ\АНДЕЛИС"
        r"\Альбом чертежей Рокоссовского-59-79_compressed.pdf"
    )

    if not pdf_path.exists():
        print(f"❌ Файл не найден: {pdf_path}")
        return

    print(f"📄 Файл: {pdf_path.name}")
    print(f"📏 Размер: {pdf_path.stat().st_size / 1024 / 1024:.1f} МБ")
    print()

    pipeline = FullPipeline()
    try:
        print("🔄 Запуск полного конвейера (OCR + Vision)...")
        result = await pipeline.process(str(pdf_path), max_pages=10)

        print(f"\n📊 ВСЕГО распознано помещений: {len(result.rooms)}")
        total_modules = sum(len(r.modules) for r in result.rooms)
        print(f"📦 ВСЕГО модулей: {total_modules}")

        # Фильтруем только кухню
        kitchen_result = filter_kitchen(result)

        print(f"\n🔪 После фильтрации КУХНЯ:")
        print(f"   Помещений: {len(kitchen_result.rooms)}")
        kitchen_modules = sum(len(r.modules) for r in kitchen_result.rooms)
        print(f"   Модулей: {kitchen_modules}")

        print()
        print("=" * 70)
        print("🍳 КУХНЯ — РЕЗУЛЬТАТ РАСПОЗНАВАНИЯ")
        print("=" * 70)

        if kitchen_result.project_address:
            print(f"📍 Адрес: {kitchen_result.project_address}")
        if kitchen_result.designer:
            print(f"👤 Дизайнер: {kitchen_result.designer}")
        print()

        for i, room in enumerate(kitchen_result.rooms, 1):
            page_info = "" if "(стр." in room.room_name else f" (стр. {room.page})"
            print(f"{i}. 🏠 {room.room_name}{page_info}")
            if room.materials:
                print(f"   🎨 Материалы: {', '.join(room.materials[:10])}")
            if room.notes:
                for note in room.notes[:5]:
                    print(f"   📝 {note[:120]}")
            if room.modules:
                print(f"   📦 Модули ({len(room.modules)}):")
                for m in room.modules:
                    g = '🪟' if m.has_glass else ' '
                    c = '📐' if m.is_corner else ' '
                    print(
                        f"      {g}{c} {m.type}: "
                        f"{m.width}×{m.depth}×{m.height}mm ×{m.quantity}"
                    )
            else:
                print(f"   (без модулей — информационная секция)")
            print()

        if kitchen_result.errors:
            print(f"⚠️  Ошибки ({len(kitchen_result.errors)}):")
            for e in kitchen_result.errors:
                print(f"   - {e}")

        print("=" * 70)
        print(f"✅ КУХНЯ: {len(kitchen_result.rooms)} помещений, {kitchen_modules} модулей")
        print("=" * 70)

    finally:
        await pipeline.close()


if __name__ == "__main__":
    asyncio.run(main())
