"""
Комплексный тест всех улучшений (Фазы 0-4).
Запуск: python test_phases.py
"""
import sys, re
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from app.services.quantity_calc import (
    detect_material_properties, calculate_quantities
)
from app.services.image_analyzer import RecognizedModule, _validate_module, GeminiImageAnalyzer
from app.services.full_pipeline import _calculate_quality, RoomSpec
from app.services.template_filler import _find_row_for_material_fuzzy

# ═══════════════════════════════════════════════════
# УТИЛИТЫ
# ═══════════════════════════════════════════════════
passed = 0
failed = 0

def check(condition, desc):
    global passed, failed
    if condition:
        passed += 1
        print(f'  ✅ {desc}')
    else:
        failed += 1
        print(f'  ❌ {desc}')

def mk(type, w, d, h, q=1, corner=False):
    return RecognizedModule(type=type, width=w, depth=d, height=h, quantity=q, is_corner=corner)


# ═══════════════════════════════════════════════════
# ТЕСТ 1: detect_material_properties (18 вариантов)
# ═══════════════════════════════════════════════════
print('━' * 60)
print('ТЕСТ 1: detect_material_properties')
print('━' * 60)

r = detect_material_properties([])
check(r == {'surface': 'plain', 'brand': 'unknown', 'facade_type': 'unknown', 'has_glass': False}, 'пустой список')

r = detect_material_properties(['EGGER H1379'])
check(r['surface'] == 'texture' and r['brand'] == 'EGGER', 'EGGER H1379 → текстура')

r = detect_material_properties(['EGGER H3158'])
check(r['surface'] == 'texture', 'EGGER H3158 → текстура')

r = detect_material_properties(['EGGER U104'])
check(r['surface'] == 'plain' and r['brand'] == 'EGGER', 'EGGER U104 → однотон')

r = detect_material_properties(['EGGER W1000'])
check(r['surface'] == 'plain', 'EGGER W1000 → однотон')

r = detect_material_properties(['EXTRAVERT текстура'])
check(r['surface'] == 'texture' and r['brand'] == 'EXTRAVERT', 'EXTRAVERT текстура')

r = detect_material_properties(['LAMARTY однотон'])
check(r['surface'] == 'plain' and r['brand'] == 'LAMARTY', 'LAMARTY однотон')

r = detect_material_properties(['Дуб сонома'])
check(r['surface'] == 'texture', 'Дуб → текстура')

r = detect_material_properties(['ОРЕХ'])
check(r['surface'] == 'texture', 'Орех → текстура')

r = detect_material_properties(['EMDIWAY Titan'])
check(r['facade_type'] == 'emdiway_titan', 'EMDIWAY Titan')

r = detect_material_properties(['EMDIWAY'])
check(r['facade_type'] == 'emdiway', 'EMDIWAY обычный')

r = detect_material_properties(['Лакокраска матовая'])
check(r['facade_type'] == 'paint_matte', 'Лакокраска матовая')

r = detect_material_properties(['Глянец'])
check(r['facade_type'] == 'paint_gloss', 'Глянец')

r = detect_material_properties(['ПВХ фасад'])
check(r['facade_type'] == 'pvh', 'ПВХ')

r = detect_material_properties(['Стекло'])
check(r['has_glass'] == True, 'Стекло → has_glass')

r = detect_material_properties(['Зеркало'])
check(r['has_glass'] == True, 'Зеркало → has_glass')

r = detect_material_properties(['EGGER H1379', 'EMDIWAY'])
check(r['surface'] == 'texture' and r['brand'] == 'EGGER' and r['facade_type'] == 'emdiway', 'комбо: EGGER H1379 + EMDIWAY')

r = detect_material_properties(['ТОМЛЕСДРЕВ'])
check(r['brand'] == 'ТОМЛЕСДРЕВ', 'ТОМЛЕСДРЕВ')


# ═══════════════════════════════════════════════════
# ТЕСТ 2: _validate_module (12 проверок)
# ═══════════════════════════════════════════════════
print()
print('━' * 60)
print('ТЕСТ 2: _validate_module')
print('━' * 60)

for mod, exp_ok, desc in [
    (mk('lower_base', 600, 560, 820), True, 'нормальный lower_base'),
    (mk('upper_base', 600, 320, 720), True, 'нормальный upper_base'),
    (mk('penal', 600, 560, 2100), True, 'нормальный пенал'),
    (mk('corner', 900, 900, 820, corner=True), True, 'угловой 900×900'),
    (mk('corner', 600, 600, 820, corner=True), True, 'угловой 600×600'),
    (mk('lower_base', 0, 560, 820), False, 'нулевая ширина'),
    (mk('lower_base', 6000, 560, 820), False, 'ширина 6000'),
    (mk('lower_base', 600, 560, 1500), False, 'нижняя база h=1500'),
    (mk('upper_base', 600, 320, 1500), False, 'верхняя база h=1500'),
    (mk('penal', 600, 560, 800), False, 'пенал h=800'),
    (mk('corner', 600, 560, 820, corner=True), False, 'угловой 600×560'),
    (mk('corner', 700, 700, 820, corner=True), False, 'угловой 700×700'),
]:
    ok, reason = _validate_module(mod)
    check(ok == exp_ok, f'{desc}: valid={ok} (exp={exp_ok})')


# ═══════════════════════════════════════════════════
# ТЕСТ 3: _calculate_quality (5 сценариев)
# ═══════════════════════════════════════════════════
print()
print('━' * 60)
print('ТЕСТ 3: _calculate_quality')
print('━' * 60)

room = RoomSpec('Кухонный гарнитур', confidence='high', modules=[
    mk('lower_base',600,560,820,q=3), mk('upper_base',600,320,720,q=2)
])
score = _calculate_quality(room)
check(score >= 0.7, f'high + 5 модулей: score={score:.0%} (flags={room.quality_flags})')

room = RoomSpec('Кухня', confidence='low', modules=[mk('lower_base',600,560,820,q=1)])
score = _calculate_quality(room)
check(score <= 0.5, f'low + 1 модуль: score={score:.0%}')

room = RoomSpec('Пустая', confidence='low', modules=[])
score = _calculate_quality(room)
check(score <= 0.3, f'нет модулей: score={score:.0%}')

room = RoomSpec('Шкаф', confidence='medium', modules=[
    mk('lower_base',600,560,820), mk('lower_base',600,560,820), mk('lower_base',600,560,820)
])
score = _calculate_quality(room)
check(score <= 0.7 and 'дубликат' in str(room.quality_flags).lower(), f'3 одинаковых: score={score:.0%}')

room = RoomSpec('Прихожая', confidence='high', modules=[
    mk('lower_base',800,560,820), mk('upper_base',800,320,720)
])
score = _calculate_quality(room)
check(score >= 0.8, f'не кухня, ок: score={score:.0%}')


# ═══════════════════════════════════════════════════
# ТЕСТ 4: calculate_quantities (флаги + стенки)
# ═══════════════════════════════════════════════════
print()
print('━' * 60)
print('ТЕСТ 4: calculate_quantities')
print('━' * 60)

mods_kitchen = [mk('lower_base',600,560,820,q=3), mk('upper_base',600,320,720,q=2)]
# v3.0: тип помещения определяется через zone_type (get_rules), а не room_name.
# При has_spec=True авто-добавление подавляется — комплектующие приходят из spec.yaml
# (дедупликация авто-комплектующих, commit 5a62ba5).
q_auto = calculate_quantities(mods_kitchen, 'Кухня', ['EGGER H1379'], zone_type='kitchen')
q_off  = calculate_quantities(mods_kitchen, 'Кухня', ['EGGER H1379'],
                               zone_type='kitchen',
                               auto_accessories=False, auto_drawers=False, auto_led=False)
q_spec = calculate_quantities(mods_kitchen, 'Кухня', ['EGGER H1379'],
                               zone_type='kitchen', has_spec=True)

check(q_auto.drawers_count > 0, f'авто-ящики: {q_auto.drawers_count} шт')
check(q_off.drawers_count == 0, f'без авто: drawers=0')
check(q_auto.cutlery_tray_count > 0, f'авто-лоток: {q_auto.cutlery_tray_count} шт')
check(q_off.cutlery_tray_count == 0, f'без авто: tray=0')
check(q_auto.led_strip_m > 0, f'авто-LED: {q_auto.led_strip_m:.1f}м')
check(q_off.led_strip_m == 0, f'без авто: led=0')
check(q_spec.drawers_count == 0 and q_spec.cutlery_tray_count == 0 and q_spec.led_strip_m == 0,
       f'spec-режим: авто подавлено '
       f'(drawers={q_spec.drawers_count}, tray={q_spec.cutlery_tray_count}, led={q_spec.led_strip_m})')

# Смежные стенки: 3 модуля 600×560×820
mods3 = [mk('lower_base',600,560,820,q=3)]
q_adj = calculate_quantities(mods3, 'Кухня', [], auto_accessories=False, auto_drawers=False, auto_led=False)
# Без учёта стенок было бы ~6.0 м², с учётом должно быть меньше
check(q_adj.ldsp_area_m2 < 6.0, f'смежные стенки: LDSP={q_adj.ldsp_area_m2:.2f} м² (< 6.0 = экономия)')


# ═══════════════════════════════════════════════════
# ТЕСТ 5: русская «х» в regex
# ═══════════════════════════════════════════════════
print()
print('━' * 60)
print('ТЕСТ 5: русская «х» в регулярках')
print('━' * 60)

patterns = [
    re.compile(r'(\d{2,4})\s*[×xX\*хХ]\s*(\d{2,4})\s*[×xX\*хХ]\s*(\d{2,4})'),
    re.compile(r'(\d{2,4})\s+[×xX\*хХ]\s+(\d{2,4})'),
]

for s, exp, desc in [
    ('600х560х840', True, 'русская строчная х'),
    ('600Х560Х840', True, 'русская заглавная Х'),
    ('600×560×840', True, 'знак умножения ×'),
    ('600x560x840', True, 'латинская x'),
    ('600X560X840', True, 'латинская X'),
    ('600*560*840', True, 'звёздочка'),
]:
    found = any(p.search(s) for p in patterns)
    check(found == exp, f'{desc}: "{s}" → found={found}')


# ═══════════════════════════════════════════════════
# ТЕСТ 6: _extract_json (разные форматы)
# ═══════════════════════════════════════════════════
print()
print('━' * 60)
print('ТЕСТ 6: _extract_json')
print('━' * 60)

analyzer = GeminiImageAnalyzer()

for text, expected, desc in [
    ('```json\n{"modules":[{"type":"lower_base"}]}\n```', '"modules"', 'markdown block'),
    ('Преамбула...\n{"modules":[{"type":"penal"}],"confidence":"high"}', '"penal"', 'reasoning + JSON'),
    ('{"modules":[{"type":"corner"}],"zone_type":"Кухня"}', '"corner"', 'чистый JSON'),
    ('  {"modules": []}  ', '"modules"', 'JSON с пробелами'),
    ('\n{"materials":["EGGER"],"modules":[{"type":"lower_base"}]}', '"lower_base"', 'c новой строки'),
]:
    result = analyzer._extract_json(text)
    check(expected in result, f'{desc}: found={expected in result}')


# ═══════════════════════════════════════════════════
# ТЕСТ 7: Fuzzy matching
# ═══════════════════════════════════════════════════
print()
print('━' * 60)
print('ТЕСТ 7: _find_row_for_material_fuzzy')
print('━' * 60)

from openpyxl import Workbook
wb = Workbook()
ws = wb.active
ws['A1'] = 'EGGER ЛДСП однотонный'
ws['B1'] = '16мм'
ws['A2'] = 'Кромка EGGER 0,8*19'
ws['B2'] = 'белый'
ws['A3'] = 'Петля FIRMAX с доводчиком'
ws['B3'] = ''

row = _find_row_for_material_fuzzy(ws, ['EGGER ЛДСП', 'однотон'], threshold=0.6)
check(row == 1, f'точное совпадение → row={row}')

row = _find_row_for_material_fuzzy(ws, ['EGGER ЛДСП', 'однотонный'], threshold=0.6)
check(row == 1, f'опечатка «однотонный» → row={row}')

row = _find_row_for_material_fuzzy(ws, ['EGGER 0,8*19'], threshold=0.6)
check(row == 2, f'«EGGER 0,8*19» → row={row}')

row = _find_row_for_material_fuzzy(ws, ['Петля', 'FIRMAX'], threshold=0.5)
check(row == 3, f'«Петля FIRMAX» → row={row}')

row = _find_row_for_material_fuzzy(ws, ['НЕСУЩЕСТВУЮЩИЙ', 'МАТЕРИАЛ'], threshold=0.8)
check(row is None, f'несуществующий → row=None')


# ═══════════════════════════════════════════════════
# ИТОГО
# ═══════════════════════════════════════════════════
print()
print('=' * 60)
print(f'🏁 ИТОГО: {passed} ✅ / {failed} ❌ (из {passed+failed})')
if failed == 0:
    print('🎉 ВСЕ ТЕСТЫ ПРОЙДЕНЫ!')
else:
    print(f'⚠️  ПРОВАЛЕНО: {failed}')
print('=' * 60)
sys.exit(0 if failed == 0 else 1)
