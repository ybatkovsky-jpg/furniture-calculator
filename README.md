# 🪑 PRO Мебель — Furniture Calculator

Telegram-бот для автоматического расчёта стоимости корпусной мебели. Менеджер загружает PDF-альбом чертежей → AI распознаёт модули → бот считает материалы и генерирует смету в Excel.

## 🎯 Что делает

```
PDF-альбом чертежей → AI-распознавание → Расчёт материалов → Excel-смета
(страницы → JPEG)    (GLM-OCR, Vision:   (ЛДСП, кромка,       (шаблон «Таблица
                      локальная Qwen3.8,  фасады, петли,        для расчетов
                      облако — fallback)  ящики, Gola, LED)     пустая» + QC)
```

## 🧠 AI-конвейер

| Этап | Модель | Что делает |
|------|--------|-----------|
| **OCR** | GLM-OCR (Z.ai) | Читает PDF (layout_parsing): помещения, размеры, материалы, таблицы |
| **Vision (основная)** | Qwen3.8-27B (GGUF, локально через llama.cpp) | Распознаёт модули мебели на чертежах (тип, Ш×Г×В, количество); thinking отключён → чистый JSON, ~20–33 с/стр. |
| **Облачный fallback** | Qwen3-VL-235B (RouterAI.ru), GLM-5V-Turbo (Z.ai) | Запасные провайдеры; кросс-валидация результатов |
| **Фасады / масштаб** | Qwen3-VL-235B (RouterAI.ru, cloud-only) | Анализ фасадов, калибровка масштаба bbox |
| **Расчёт** | quantity_calc / calc_engine → hardware_calc | Переводит модули в позиции прайса: листы ЛДСП, метры кромки, фасады, петли (единый `hinges_per_door`), ящики, крепёж |
| **Спецификация проекта** | project_spec.py | Применяет project_spec.yaml: правила авто-комплектующих и петель проекта |
| **Заполнение** | template_filler | Заполняет Excel-шаблон «Таблица для расчетов пустая.xlsx» + листы контроля качества |

## 🚀 Быстрый старт

### 1. Клонировать и установить

```bash
git clone https://github.com/ybatkovsky-jpg/furniture-calculator.git
cd furniture-calculator
python -m venv .venv
.venv\Scripts\activate     # Windows
pip install -r requirements.txt
```

### 2. Настроить .env

```env
TELEGRAM_BOT_TOKEN=123456:ABC-DEF
ADMIN_TELEGRAM_IDS=                # ID админов через запятую (управление прайсом)

# Облако — fallback (если LOCAL_LLM_API_URL пуст, используется как основное)
ZAI_API_KEY=your_zai_key           # GLM-OCR, GLM-5V-Turbo
ROUTERAI_API_KEY=sk-...            # Qwen3-VL-235B (ансамбль, фасады)

# Локальная vision-модель — основная (OpenAI-совместимый сервер llama.cpp)
LOCAL_LLM_API_URL=http://<host>:8888/v1   # напр. 192.168.1.133:8888
LOCAL_LLM_API_KEY=
LOCAL_VISION_MODEL=unsloth/Qwen3.8-27B-GGUF

DATABASE_URL=sqlite+aiosqlite:///./data/furniture.db
```

### 3. Запустить Telegram-бота

```bash
python -m app.main
```

### 4. Или запустить конвейер из командной строки

```bash
python -m app.services.template_filler "путь/к/чертежам.pdf" "templates/Таблица для расчетов пустая.xlsx" "выход.xlsx"
```

Продвинутые прогоны (возобновляемый, ансамбль, фасады, масштаб) — скрипты в [`scripts/`](scripts/).

## 📊 Формула расчёта

```
1. Себестоимость = Σ(ЛДСП + кромка + фасады + фурнитура + стекло + ...)
2. Изготовление = Себестоимость × 0.80
3. Монтаж = Себестоимость × 0.55
4. Замер = 2 500 ₽
5. Доставка = 10 000 ₽
6. Проектировка = Изготовление × 0.10
7. Прочие = Себестоимость × 0.05
8. Итого = пп. 1–7
9. Прибыль = Итого × 0.30
10. Тех. директор = (Итого + Прибыль) × 0.015
11. Итого наличка (база) = 9 + 10
12. Скидка / наценка (% или ₽)
13. Бонус дизайнера: финал = цена / (1 − 0.10)
14. Безнал = финал × 1.13
```

## 📁 Структура проекта

```
furniture-calculator/
├── app/
│   ├── main.py                    # FastAPI + запуск Telegram-бота
│   ├── config.py                  # Pydantic Settings (.env): облако + локальный LLM
│   ├── bot/                       # Telegram-бот (aiogram 3)
│   │   ├── bot.py
│   │   ├── handlers/              # start, проекты, изображения, материалы, скидки, КП, прайс
│   │   ├── keyboards/             # inline-клавиатуры
│   │   └── states/                # FSM состояния (aiogram)
│   ├── db/                        # Сессии БД (async SQLAlchemy)
│   ├── models/                    # SQLAlchemy-модели (project, price, calculation, glass, settings)
│   └── services/                  # Бизнес-логика и AI-конвейер
│       ├── image_analyzer.py      # Vision: локальная Qwen3.8 + облачный fallback
│       ├── pdf_parser.py          # GLM-OCR — парсинг PDF (layout_parsing)
│       ├── pdf_renderer.py        # Рендеринг страниц PDF → JPEG
│       ├── full_pipeline.py       # Сборка конвейера OCR + Vision
│       ├── quantity_calc.py       # Расчёт количеств материалов (модули → позиции)
│       ├── calc_engine.py         # Главный расчётный движок
│       ├── hardware_calc.py       # Петли (единый hinges_per_door), фурнитура
│       ├── fastener_calc.py       # Расчёт крепежа
│       ├── glass_calc.py          # Расчёт стекла
│       ├── edge_calc.py           # Расчёт кромки
│       ├── sheet_calc.py          # Расчёт листов ЛДСП/МДФ
│       ├── scale_calc.py          # Калибровка масштаба (bbox)
│       ├── project_spec.py        # Спецификация проекта (project_spec.yaml)
│       ├── furniture_defaults.py  # Дефолты мебельных конструкций
│       ├── cost_calc.py           # Итоговая смета + скидки/бонусы
│       ├── calculation_excel.py   # Генерация Excel с нуля
│       ├── excel_writer.py        # Запись в Excel
│       ├── template_filler.py     # Заполнение шаблона «Таблица для расчетов пустая.xlsx»
│       ├── price_manager.py       # Импорт/экспорт прайса
│       └── kp_generator.py        # Генерация PDF КП
├── scripts/                       # CLI-скрипты пайплайна
│   ├── process_all_resumable.py   # Возобновляемый прогон (с места остановки)
│   ├── process_ensemble.py        # Ансамблевый прогон
│   ├── process_facades.py         # Анализ фасадов
│   ├── run_scale_bbox.py          # Калибровка масштаба
│   ├── check_phases.py / import_price.py / process_images.py / …
│   └── dev/                       # Вспомогательные dev-скрипты
├── templates/
│   ├── row_mapping.json                 # Маппинг материалов → строки шаблона
│   ├── project_spec_EXAMPLE.yaml        # Пример спецификации проекта
│   └── Таблица для расчетов пустая.xlsx # Excel-шаблон сметы (заполняется пайплайном)
├── alembic/                      # Миграции БД
├── tests/                        # calc_engine, hinge_parity, scale_calc, discounts, фасады
├── data/   input_images/   output/   temp/   pdfs/   # артефакты прогонов (в gitignore)
├── Dockerfile / docker-compose.yml
├── ROADMAP_IMPROVEMENTS.md       # ROADMAP: Архитектура v2.1 (2026-09-04)
└── requirements.txt
```

## 🧭 Актуальное состояние (ROADMAP v2.1, 2026-09-04)

- ✅ **Local-first** — основная vision-модель: локальная **Qwen3.8-27B (GGUF, llama.cpp)**, OpenAI-совместимый API (`LOCAL_LLM_API_URL`); thinking отключён → чистый JSON; облако (RouterAI.ru / Z.ai) — fallback; фасады — cloud-only
- ✅ **Parity петель** — единое правило `hinges_per_door(height, brand)` в `hardware_calc.py`: `quantity_calc` и `calc_engine` считают одинаково (FIRMAX: ≥2000→4 / 901–1999→3 / ≤900→2; BLUM/HETTICH — высотная таблица до 5); 19 тестов parity
- ✅ **Спецификация проекта** — `project_spec.yaml`: правила авто-комплектующих и петель конкретного проекта (дедупликация позиций)
- ✅ **Валидация на реальном проекте** — альбом «Рокоссовского 59-79»: 12/12 страниц прогнано локально, помещения определены верно на всех страницах
- ✅ **Возобновляемый прогон** — `scripts/process_all_resumable.py` продолжает с места остановки, с QC-вердиктами по страницам

### Ранее (Фазы 1–5, ROADMAP v1)

- ✅ JSON-format промпты, валидация размеров (150–2400 мм), предобработка изображений (контраст, резкость)
- ✅ Ансамбль моделей — кросс-валидация, overlap ≥70% → confidence high; учёт смежных стенок (экономия ЛДСП)
- ✅ Fuzzy matching строк шаблона (SequenceMatcher); `templates/row_mapping.json` вместо хардкода
- ✅ Листы «⚠ Проблемы» и «✅ Контроль качества» (цветовая индикация) в Excel

Подробнее: [`ROADMAP_IMPROVEMENTS.md`](ROADMAP_IMPROVEMENTS.md)

## 📦 Стек

| Компонент | Технология |
|-----------|-----------|
| Язык | Python 3.12 |
| Telegram Bot | aiogram 3 |
| Vision AI | Qwen3.8-27B (локально, llama.cpp) — primary; Qwen3-VL-235B (RouterAI.ru), GLM-5V-Turbo (Z.ai) — fallback |
| OCR | GLM-OCR (Z.ai) |
| База данных | SQLite + aiosqlite (→ PostgreSQL) |
| ORM | SQLAlchemy 2.0 (async) |
| Excel | openpyxl |
| PDF | ReportLab, pdf2image |
| Web (API) | FastAPI |

## 📝 Лицензия

Проприетарное ПО. © PRO Мебель, Хабаровск.
