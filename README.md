# 🪑 PRO Мебель — Furniture Calculator

Telegram-бот и локальный веб-интерфейс для расчёта корпусной мебели: менеджер
загружает чертёж или PDF → AI распознаёт модули → система считает материалы,
фурнитуру и стоимость → Excel, смета в чате и коммерческое предложение.

> **Актуальное состояние (2026-11-24).** В проекте есть и Telegram-бот, и
> локальный веб-интерфейс `app/web/server.py` (FastAPI, `scripts/run_web.py`):
> загрузка изображения или PDF, выбор страницы, проверка состава модулей,
> Excel и DOCX-КП. PDF проходит через `FullPipeline`, изображения — через веб
> и бот; `FullPipeline.process_imported` принимает уже импортированный проект.
> Импорт приведён к единому слою `app/services/importing/` (изображения, PDF,
> выгрузка ПРО100). Коэффициенты — из `app/config.py`/`.env`; цены — из прайса
> SQLite. Смета чата, Excel и КП используют один `CostBreakdown`. Проверки:
> `pytest` — 754 passed, `scripts/check_phases.py` — 59/59,
> `ruff check app` — чисто.
>
> **Важно:** `docs/financial_policy_v1.0.md` имеет статус «проект на утверждение»
> и содержит 30 %/×1,13, а код и решения собственника 17.11.2026 — 35 %/×1,10.
> До утверждения единой редакции окончательное КП считается предварительным.

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

### 5. Импортировать проект в единое хранилище

```powershell
$env:PYTHONIOENCODING='utf-8'
.\.venv\Scripts\python.exe scripts\import_project.py "pdfs\альбом.pdf"
```

Изображения, PDF и выгрузка ПРО100 (`details.txt`) проходят один вход и
сохраняются в `data/projects/<project_id>/`; повторная загрузка не создаёт
дубликат. Подробности — [`docs/import.md`](docs/import.md).

Продвинутые прогоны (возобновляемый, ансамбль, фасады, масштаб) — скрипты в [`scripts/`](scripts/).

## 📊 Формула расчёта

Числа ниже — фактические значения `app/config.py` на 24.11.2026. Они меняются
через `.env`, а не правкой кода. Перед выпуском КП проверьте, что редакция
финансовой политики утверждена (см. предупреждение выше).

```
1. Закупка = Σ(ЛДСП + кромка + фасады + фурнитура + стекло + столешница + крепёж + ...)
2. Изготовление = Закупка × 0.80
3. Проектировка = Изготовление × 0.10
4. Прочие расходы = Закупка × 0.05
5. Замер = 2 500 ₽; внутренняя доставка = 6 500 ₽ (учитывается отдельно)
6. Монтаж = 15 % от конечной цены клиента (решается уравнением, входит в базу)
7. Полная себестоимость = Закупка + Изготовление + Монтаж + Замер +
   Проектировка + Прочие
8. Прибыль = Полная себестоимость × 0.35
9. Тех. директор = (Себестоимость + Прибыль) × 0.015
10. Скидка или наценка (взаимоисключающие; скидка > 10 % — только с согласованием)
11. Бонус дизайнера (если включён): цена / (1 − 0.10)
12. Клиентская доставка = 10 000 ₽ отдельной строкой, без наценки
13. Безнал = цена мебели × 1.10 + доставка
```

Ключевые инварианты: смета чата, Excel и КП берут один `CostBreakdown`;
позиция без цены блокирует КП; цена ниже полных затрат (`below_cost`) блокирует
КП; сохранённый расчёт не пересчитывается при обновлении прайса.

## 📁 Структура проекта

```
furniture-calculator/
├── app/
│   ├── main.py                    # инициализация БД + запуск Telegram-бота
│   ├── config.py                  # Pydantic Settings (.env): коэффициенты, ключи, пути
│   ├── bot/                       # Telegram-бот (aiogram 3)
│   │   ├── bot.py
│   │   ├── handlers/              # start, проекты, изображения, материалы, скидки, КП, прайс
│   │   ├── keyboards/             # inline-клавиатуры
│   │   └── states/                # FSM состояния (aiogram)
│   ├── web/                       # локальный FastAPI-интерфейс менеджера
│   ├── db/                        # Сессии БД (async SQLAlchemy)
│   ├── models/                    # SQLAlchemy-модели (project, price, calculation, glass, settings)
│   └── services/                  # Бизнес-логика и AI-конвейер
│       ├── importing/             # единый импорт: изображение, PDF, ПРО100
│       ├── image_analyzer.py      # Vision: локальная Qwen3.8 + облачный fallback
│       ├── pdf_parser.py          # GLM-OCR — парсинг PDF (layout_parsing)
│       ├── pdf_renderer.py        # Рендеринг страниц PDF → JPEG
│       ├── full_pipeline.py       # Сборка конвейера OCR + Vision
│       ├── quantity_calc.py       # Расчёт количеств материалов (модули → позиции)
│       ├── calc_engine.py         # Главный расчётный движок
│       ├── calculation_service.py # Единая точка пересчёта/скидок/версий
│       ├── hardware_calc.py       # Петли (единый hinges_per_door), фурнитура
│       ├── fastener_calc.py       # Расчёт крепежа
│       ├── glass_calc.py          # Расчёт стекла
│       ├── edge_calc.py           # Исторический расчёт кромки (в смете не участвует)
│       ├── sheet_calc.py          # Расчёт листов ЛДСП/МДФ
│       ├── scale_calc.py          # Калибровка масштаба (bbox)
│       ├── project_spec.py        # Спецификация проекта (project_spec.yaml)
│       ├── furniture_defaults.py  # Дефолты мебельных конструкций
│       ├── cost_calc.py           # Итоговая смета + скидки/бонусы
│       ├── template_filler.py     # Заполнение шаблона «Таблица для расчетов пустая.xlsx»
│       ├── price_manager.py       # Импорт/экспорт прайса
│       ├── kp_generator.py        # PDF КП (ReportLab)
│       └── kp_docx.py             # DOCX КП с тремя вариантами
├── scripts/                       # CLI-скрипты пайплайна
│   ├── import_project.py          # единый импорт проекта (этап 1)
│   ├── process_images.py          # Прогон отдельных картинок → Excel (local-first)
│   ├── process_all_resumable.py   # Пакетный прогон каталога с кэшем (--images/--spec/--project/--out)
│   ├── run_quick.py               # Smoke-прогон PDF → Excel (5 стр.)
│   ├── check_phases.py            # Валидатор фаз пайплайна (59 проверок)
│   ├── import_price.py            # Импорт прайса
│   └── dev/                       # dev-инструменты (v2_validate_all.py — валидатор промпта на эталонах)
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

## 🧭 История архитектуры (ROADMAP v2.1, 2026-09-04)

- ✅ **Local-first** — основная vision-модель: локальная **Qwen3.8-27B (GGUF, llama.cpp)**, OpenAI-совместимый API (`LOCAL_LLM_API_URL`); thinking отключён → чистый JSON; облако (RouterAI.ru / Z.ai) — fallback; фасады — cloud-only
- ✅ **Parity петель** — единое правило `hinges_per_door(height, brand)` в `hardware_calc.py`: `quantity_calc` и `calc_engine` считают одинаково (FIRMAX: ≥2000→4 / 901–1999→3 / ≤900→2; BLUM/HETTICH — высотная таблица до 5); 19 тестов parity
- ✅ **Спецификация проекта** — `project_spec.yaml`: правила авто-комплектующих и петель конкретного проекта (дедупликация позиций)
- ✅ **Валидация на реальном проекте** — альбом «Рокоссовского 59-79»: 12/12 страниц прогнано локально, помещения определены верно на всех страницах
- ✅ **Возобновляемый прогон** — `scripts/process_all_resumable.py` продолжает с места остановки (кэш в `output/pages_cache.json`); каталог картинок/spec/имя проекта/выход — параметры CLI

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
| OCR | Tesseract 5.4 (числа размерных линий), GLM-OCR (структура PDF) |
| База данных | SQLite + aiosqlite (→ PostgreSQL) |
| ORM | SQLAlchemy 2.0 (async) |
| Excel | openpyxl |
| PDF | PyMuPDF, ReportLab, python-docx |
| Web (API) | FastAPI + uvicorn, локально `127.0.0.1` |

## 📝 Лицензия

Проприетарное ПО. © PRO Мебель, Хабаровск.
