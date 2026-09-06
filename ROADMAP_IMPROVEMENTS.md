# ROADMAP: Архитектура v2 — универсальная машина расчёта мебели

> Версия: 2.1  
> Дата: 2026-09-04  
> Ветка: `commit-changes`

---

## Обновление 2026-09-04 (v2.1)

### Модели: локальная-first
- **Primary**: `unsloth/Qwen3.8-27B-GGUF` (UD-Q4_K_XL) — **локально**, `http://192.168.1.133:8888/v1` (unsloth-studio), vision умеет.
- Thinking-модель: в локальные запросы добавлено `"chat_template_kwargs": {"enable_thinking": false}` → чистый JSON, ~20–33 с/страница (с thinking — ~12 мин и битый JSON).
- Облако (RouterAI / Z.ai) осталось запасным; `analyze_facades()` и фасадные скрипты — cloud-only.
- Настройки: `LOCAL_LLM_API_URL/KEY`, `LOCAL_VISION_MODEL` в `.env` (`app/config.py`).

### Валидация на реальном проекте
- Альбом «Рокоссовского 59-79»: **12/12 страниц** прогнано локально (1 кухня-эталон + 11 батчем, ~20 с/стр).
- Помещения определены верно на всех страницах (Прихожая/Кухня/Гостиная/Спальня/Детская/Ванная).
- Локальный vs облачный прогон стр. 0003: идентичная структура (QC/лист/Проблемы), 5 расхождений количеств + 4 по Проблемам — качество модели, не регрессия.
- Вердикт QC на всех страницах — `🔴 ПЕРЕПРОВЕРИТЬ` (консервативное правило пайплайна, не ошибка модели).

### Расчёт: parity петель
- **Единое правило `hinges_per_door(height_mm, brand)`** в `hardware_calc.py` (FIRMAX-«правила заказчика» ≥2000→4 / 901–1999→3 / ≤900→2; BLUM/HETTICH — высотная таблица до 5).
- `quantity_calc` и `calc_engine → hardware_calc` считают одинаково (дефолт FIRMAX; бренд из `selected_hardware.hinges.brand`). Тест parity: 19 новых проверок.
- Фикс: высота двери = высота модуля (facades.count = двери **рядом**), деление высоты убрано.

### Скрипты и чистка
- 14 скриптов в `scripts/` чинили пути после переноса в `scripts/` (sys.path/TEMPLATE/OUTPUT → корень) и получили guard'ы `if __name__ == "__main__"`. Аудит: дефектов кода нет, всё упирается в окружение.
- `check_phases.py`: 59/59 (ожидания актуализированы под v3.0 zone_type).
- spec.yaml: дедупликация авто-комплектующих (`apply_spec_to_quantities`, коммит `5a62ba5`).

---

## Что сделано (v1 → v2)

### Этап 1 — Баг-фикс `[a520ed8]`
- `total_width_mm = 3000` → честный `return [], None, []` — больше никаких выдуманных размеров
- `scale = 600/20` → `return []`

### Этап 2 — Единый промпт + 1 запрос `[a520ed8]`
- **UNIFIED_PROMPT_V2**: bbox-проценты + типы модулей + материалы — всё в одном запросе
- **analyze_page()**: один вызов qwen3-vl-235b вместо каскада scaled→facades→modules
- _process_page упрощён до 2 шагов: analyze_page → fallback analyze_drawing

### Этап 3 — Модели `[0709407]`
- **Primary**: `qwen3-vl-235b` (RouterAI) — лучший spatial reasoning
- **Fallback**: `glm-5v-turbo` (Z.ai) — быстрый, другой провайдер
- Удалены: `FALLBACK_CHAIN` (3 модели), `_ensemble_merge()`, ансамбль

### Этап 4 — Универсальность `[f1df341]`
- **furniture_defaults.py** — таблица стандартов для 11 типов помещений
- **FurnitureRules**: размеры, door_system (hinged/sliding), авто-аксессуары
- Strategy pattern: `is_kitchen` → `rules.family == "kitchen"`
- `RoomSpec.zone_type` — сквозной параметр через весь пайплайн

### Этап 5 — Чистка `[014ea45]`
- Удалено 276 строк мёртвого кода (SCALE_PX_PROMPT, PX-функции, `_estimate_facade_weight`, хардкод ROW_MAPPING)

---

## Архитектура (актуальная)

```
PDF → GLM-OCR → Page Picker → Render JPEG
                                   │
                    ┌──────────────▼──────────────┐
                    │  analyze_page()              │
                    │  Qwen3.8-27B (локально)      │
                    │  UNIFIED_PROMPT_V2           │
                    │  bbox + модули + материалы   │
                    └──────────────┬──────────────┘
                                   │
                    ┌──────────────▼──────────────┐
                    │  total_width_mm > 0?        │
                    │  YES → scale_calc → точные мм│
                    │  NO  → стандартные размеры   │
                    └──────────────┬──────────────┘
                                   │
                    ┌──────────────▼──────────────┐
                    │  quantity_calc              │
                    │  FURNITURE_DEFAULTS[zone]   │
                    │  strategy pattern           │
                    │  петли: hinges_per_door()   │
                    └──────────────┬──────────────┘
                                   │
                    ┌──────────────▼──────────────┐
                    │  template_filler            │
                    │  row_mapping.json → Excel   │
                    └─────────────────────────────┘
```

---

## Модели (актуально, v2.1)

| Роль | Модель | Провайдер |
|------|--------|-----------|
| Primary | `unsloth/Qwen3.8-27B-GGUF` (локальная, vision) | unsloth-studio `192.168.1.133:8888` |
| Fallback | `glm-5v-turbo` | Z.ai |
| Legacy-фасадный | `qwen/qwen3-vl-235b-a22b-thinking` | RouterAI.ru (cloud-only, `analyze_facades`) |
| OCR | `glm-ocr` | Z.ai |

---

## Файловая структура (ключевые изменения)

| Файл | Назначение |
|------|-----------|
| `app/services/furniture_defaults.py` | **NEW** — стандарты размеров + правила для 11 типов помещений |
| `app/services/image_analyzer.py` | UNIFIED_PROMPT_V2, analyze_page(), analyze_drawing() |
| `app/services/scale_calc.py` | calculate_scaled_facades() — bbox % → мм |
| `app/services/full_pipeline.py` | _process_page упрощён, RoomSpec.zone_type |
| `app/services/quantity_calc.py` | strategy pattern, zone_type-based эвристики |
| `app/services/template_filler.py` | ROW_MAPPING только из JSON |

---

## Что дальше (TODO)

### Ближайшее (актуально на 2026-09-04)
- [x] **Тестирование** unified-промпта: альбом «Рокоссовского 59-79», 12/12 страниц локально
- [x] **Parity петель** quantity_calc ↔ calc_engine (единый `hinges_per_door`)
- [x] **Починка скриптов** `scripts/` (пути на корень + guard'ы)
- [ ] **Замер точности по альбому**: сверить локальные сметы по 12 страницам с чертежами/прайсом (0008 и 0011 — были авто-коррекции ширины фасадов). Статус 2026-07-23: V2-валидация 12/12 завершена, zone_type и габариты 100%; 0008 (кровать) подтверждён корректным — фасадов на чертеже нет. **Статус 2026-09-06:** сделан кросс-маршрутный замер (локальная Qwen3.8 vs облачная qwen3-vl-235b, один промпт, 12 стр.) — зоны 11/12, габарит 10/12, фасады 4/12 согласованы; авто-коррекции на 5 стр. (0002/0003/0004/0008/0011). Таблица и выводы: `ЗАМЕЧАНИЯ_ТЕСТИРОВАНИЯ.md` §5. **Осталось:** vision-сверка оператором страниц 0011 (Кухня vs Ванная), 0001 (1650 vs 1100), 0008 (кровать vs модули), 0004 (1 корнер vs 4 фасада) — в текущей сессии модель без image-входа
- [x] **Конфиги**: вынести UNIFIED_PROMPT_V2 в `prompts/unified.txt` (ключи/модель уже в `.env`) — сделано 2026-09-06, коммит `1e90ccb` (fallback на встроенную константу)
- [x] **Фасадные скрипты-дубли** (`process_facades`, `process_ensemble`, `run_scale_bbox`, `scripts/dev/*`) — **удалены 2026-09-06** (12 скриптов: `process_facades`, `process_ensemble`, `run_scale_bbox`, `process_all_pages`, `process_fast`, `run_input_images`, `run_kitchen_only`, `run_pipeline_test`, `recheck_pages`, `deep_check_pages`, `calibrate_kitchen`, `dev/compare_prompts_ab`). Вывод аудита: «унифицировать на `analyze_facades`» — неверно, это cloud-only legacy-контур; канонический путь — `analyze_page` (локально) → `_facades_to_modules`. Оставлены: `process_images.py` (живой, local-first), `process_all_resumable.py` (пакетный, параметры вынесены в CLI: `--images/--spec/--project/--out`), `run_quick.py` (PDF-smoke), `check_phases.py`, `dev/v2_validate_all.py`. Сервисный legacy-контур `analyze_facades`/`_try_analyze_facades`/`analyze_facades_scaled` + `FACADE_PROMPT`/`SCALE_PROMPT`/`FacadeResult` остался без потребителей — кандидат на удаление отдельным шагом (не трогать `_facades_to_modules`/module-level `facades_to_modules` — под тестами)

### Среднесрочное
- [ ] **Расширить промпт** на 12 типов модулей (сейчас 4: lower/upper/penal/corner, нужно +8)
- [ ] **Раздвижные двери** для wardrobe (направляющие/ролики вместо петель); петли «высота×вес» (BLUM-таблица, сейчас только высота)
- [ ] **Модули без фасадов**: null-safe обработка (appliance-пеналы уже пропускаются)
- [ ] **Few-shot для не-кухонь**: примеры шкафа-купе и гардеробной в промпт

### Дальнее
- [ ] **Кросс-валидация OCR↔Vision**: флаги расхождений в листе «Контроль качества»
- [ ] **Интерфейс оператора**: web-интерфейс для проверки/коррекции модулей перед расчётом
- [ ] **Калибровка bbox**: референсный объект для повышения точности масштаба
- [ ] **Цены в конфиге**: `config/pricing.yaml` вместо хардкода в `fill_template_for_room()`/`quantity_calc`

---

## Метрики

| Метрика | Было (v1) | Стало (v2) | Цель |
|---------|-----------|------------|------|
| Моделей в ротации | 5 | 2 | — |
| Запросов на страницу | 1-3 | 1 | — |
| Мёртвого кода (строк) | ~200 | 0 | — |
| Типов помещений | 1 (кухня) | 11 | Все |
| Ошибка «выдуманный размер» | 3000мм default | fail → fallback | 0 |
| Время оператора на проект | ~20 мин | ? | <5 мин |
