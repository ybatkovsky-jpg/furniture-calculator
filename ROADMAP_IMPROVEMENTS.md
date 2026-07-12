# ROADMAP: Архитектура v2 — универсальная машина расчёта мебели

> Версия: 2.0  
> Дата: 2026-07-12  
> Ветка: `commit-changes`

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
                    │  qwen3-vl-235b               │
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
                    └──────────────┬──────────────┘
                                   │
                    ┌──────────────▼──────────────┐
                    │  template_filler            │
                    │  row_mapping.json → Excel   │
                    └─────────────────────────────┘
```

---

## Модели

| Роль | Модель | Провайдер |
|------|--------|-----------|
| Primary | `qwen/qwen3-vl-235b-a22b-thinking` | RouterAI.ru |
| Fallback | `glm-5v-turbo` | Z.ai |
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

### Ближайшее
- [ ] **Конфиги**: вынести UNIFIED_PROMPT_V2 в `prompts/unified.txt`
- [ ] **Конфиги**: вынести модели в `config/models.yaml`
- [ ] **Тестирование** на 3-5 реальных проектах с новым unified-промптом
- [ ] **Замер точности**: bbox-размеры vs реальные с чертежа

### Среднесрочное
- [ ] **Расширить промпт** на 12 типов модулей (сейчас 4: lower/upper/penal/corner, нужно +8)
- [ ] **Раздвижные двери**: модуль расчёта для wardrobe (направляющие, ролики вместо петель)
- [ ] **Модули без фасадов**: гарантировать null-safe обработку в quantity_calc
- [ ] **Few-shot для не-кухонь**: добавить примеры шкафа-купе и гардеробной в промпт

### Дальнее
- [ ] **Кросс-валидация OCR↔Vision**: флаги расхождений в листе «Контроль качества»
- [ ] **Интерфейс оператора**: web-интерфейс для проверки/коррекции модулей перед расчётом
- [ ] **Калибровка bbox**: референсный объект для повышения точности масштаба
- [ ] **Цены в конфиге**: `config/pricing.yaml` вместо хардкода в `fill_template_for_room()`

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
