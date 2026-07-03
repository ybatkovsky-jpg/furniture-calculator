# 🪑 PRO Мебель — Furniture Calculator

Telegram-бот для автоматического расчёта стоимости корпусной мебели. Менеджер загружает PDF-альбом чертежей → AI распознаёт модули → бот считает материалы и генерирует смету в Excel.

## 🎯 Что делает

```
PDF чертежей  →  AI-распознавание  →  Расчёт материалов  →  Excel-смета
    (21 стр.)      (OCR + Vision)      (ЛДСП, кромка,         (шаблон с
                                         фасады, петли,        заполненными
                                         ящики, Gola, LED)     количествами)
```

## 🧠 AI-конвейер

| Этап | Модель | Что делает |
|------|--------|-----------|
| **OCR** | GLM-OCR (Z.ai) | Читает PDF: помещения, размеры, материалы, таблицы |
| **Vision** | GLM-5V-Turbo + GLM-4.6V | Распознаёт модули мебели на чертежах (тип, Ш×Г×В, количество) |
| **Ансамбль** | Qwen3-VL (fallback) | Кросс-валидация — вторая модель перепроверяет результат |
| **Расчёт** | quantity_calc | Переводит модули в позиции прайса: листы ЛДСП, метры кромки, фасады, петли, ящики |
| **Заполнение** | template_filler | Заполняет Excel-шаблон (колонка «Количество») + листы контроля качества |

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
ZAI_API_KEY=your_zai_key
OPENROUTER_API_KEY=your_openrouter_key
VISION_MODEL=glm-5v-turbo
DATABASE_URL=sqlite+aiosqlite:///./data/furniture.db
```

### 3. Запустить Telegram-бота

```bash
python -m app.main
```

### 4. Или запустить конвейер из командной строки

```bash
python -m app.services.template_filler "путь/к/чертежам.pdf" "путь/к/шаблону.xlsx" "выход.xlsx"
```

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
│   ├── main.py                    # FastAPI + запуск бота
│   ├── config.py                  # Pydantic Settings (.env)
│   ├── bot/                       # Telegram-бот (aiogram 3)
│   │   ├── handlers/              # /start, проекты, материалы, скидки, КП
│   │   ├── keyboards/             # inline + reply клавиатуры
│   │   └── states/                # FSM состояния (aiogram)
│   ├── services/                  # Бизнес-логика
│   │   ├── image_analyzer.py      # Vision-распознавание (GLM-5V, Qwen3, Gemini)
│   │   ├── pdf_parser.py          # GLM-OCR — парсинг PDF
│   │   ├── pdf_renderer.py        # Рендеринг страниц PDF → JPEG
│   │   ├── full_pipeline.py       # Сборка конвейера OCR + Vision
│   │   ├── quantity_calc.py       # Расчёт количеств материалов
│   │   ├── calc_engine.py         # Главный расчётный движок
│   │   ├── cost_calc.py           # Итоговая смета + скидки/бонусы
│   │   ├── calculation_excel.py   # Генерация Excel с нуля
│   │   ├── template_filler.py     # Заполнение шаблона Excel
│   │   ├── glass_calc.py          # Расчёт стекла
│   │   ├── edge_calc.py           # Расчёт кромки
│   │   ├── sheet_calc.py          # Расчёт листов ЛДСП/МДФ
│   │   ├── hardware_calc.py       # Расчёт фурнитуры
│   │   ├── price_manager.py       # Импорт/экспорт прайса
│   │   ├── kp_generator.py        # Генерация PDF КП
│   │   └── excel_writer.py        # Запись в Excel
│   ├── models/                    # SQLAlchemy модели
│   ├── schemas/                   # Pydantic схемы
│   └── db/                        # Сессии БД + seed
├── templates/
│   ├── row_mapping.json           # Маппинг материалов → строки шаблона
│   └── kp_template.html           # Шаблон КП
├── ROADMAP_IMPROVEMENTS.md        # План улучшений (5 фаз)
├── SETUP_COMPLETE.md              # Статус настройки
└── requirements.txt
```

## 🔧 Последние улучшения (Фазы 1–5)

- ✅ **JSON-format** — модель гарантированно возвращает валидный JSON
- ✅ **Валидация размеров** — фильтр нереалистичных модулей (ширина 150–2400 мм и т.д.)
- ✅ **Предобработка изображений** — контраст +30%, повышение резкости
- ✅ **Ансамбль моделей** — кросс-валидация двумя моделями, overlap ≥70% → confidence high
- ✅ **Few-shot промпты** — единый промпт с примерами прямой и угловой кухни
- ✅ **Учёт смежных стенок** — соседние модули делят боковину, экономия ЛДСП
- ✅ **Fuzzy matching** — нечёткий поиск строк в шаблоне (SequenceMatcher)
- ✅ **Лист «⚠ Проблемы»** — все незаполненные строки в отдельном листе Excel
- ✅ **Лист «✅ Контроль качества»** — цветовая индикация: зелёный / жёлтый / красный
- ✅ **JSON-конфиг маппинга** — `templates/row_mapping.json` вместо хардкода

Подробнее: [`ROADMAP_IMPROVEMENTS.md`](ROADMAP_IMPROVEMENTS.md)

## 📦 Стек

| Компонент | Технология |
|-----------|-----------|
| Язык | Python 3.12 |
| Telegram Bot | aiogram 3 |
| Vision AI | GLM-5V-Turbo, GLM-4.6V, Qwen3-VL (OpenRouter), Gemini Flash |
| OCR | GLM-OCR (Z.ai) |
| База данных | SQLite + aiosqlite (→ PostgreSQL) |
| ORM | SQLAlchemy 2.0 (async) |
| Excel | openpyxl |
| PDF | ReportLab, pdf2image |
| Web (API) | FastAPI |

## 📝 Лицензия

Проприетарное ПО. © PRO Мебель, Хабаровск.
