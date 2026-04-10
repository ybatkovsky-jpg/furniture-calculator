# CURSOR INSTRUCTIONS — Мебельный калькулятор (Telegram Bot)
# Версия 3.0 — все неопределённости разрешены, готово к разработке

---

## ЧАСТЬ 0. ГАЙД ПО ВАЙБКОДИНГУ В CURSOR

### Что такое вайбкодинг

Вайбкодинг — это когда ты описываешь задачу человеческим языком, а AI-ассистент (Cursor) пишет код. Твоя роль — режиссёр: ты говоришь ЧТО делать, Cursor делает КАК. Ты не пишешь код руками, но ты должен понимать что происходит и уметь направлять процесс.

### Как установить и настроить Cursor

1. Скачай Cursor с https://cursor.com (есть бесплатный тариф)
2. Установи, открой
3. Создай папку проекта: `mkdir furniture-calc && cd furniture-calc`
4. Открой её в Cursor: `cursor .` или File → Open Folder
5. В настройках Cursor (Cmd/Ctrl + ,): включи "Composer" — это главный инструмент для вайбкодинга

### Пошаговый процесс работы

**Шаг 1. Загрузи эту инструкцию в проект**

Создай файл `.cursorrules` в корне проекта и скопируй туда основные правила:

```
# .cursorrules — Cursor читает этот файл автоматически

Проект: Telegram-бот для расчёта стоимости корпусной мебели.
Стек: Python 3.12, FastAPI, aiogram 3, SQLite, openpyxl, ReportLab.

Правила:
- Пиши на Python 3.12 с type hints
- Используй async/await везде (aiogram 3 и SQLAlchemy async)
- Модели данных — SQLAlchemy 2.0 (mapped_column или Column)
- FSM для Telegram — через aiogram StatesGroup
- Все цены в рублях, хранятся как Float
- JSON-поля в SQLite — через Column(JSON)
- Комментарии на русском языке
- Форматирование: black, isort
- Не используй print() — используй logging
```

Также положи `CURSOR_INSTRUCTIONS.md` (этот файл) в корень проекта — Cursor сможет ссылаться на него.

**Шаг 2. Открой Composer (Cmd/Ctrl + I) и начни с каркаса**

Вводи промпты последовательно. Не пытайся создать всё за один запрос — это главная ошибка новичков. Разбивай на маленькие шаги.

### Промпты для Cursor — пошагово

Копируй эти промпты один за другим в Composer (Cmd+I). После каждого — проверяй результат, запускай, фиксишь.

---

**Промпт 1 — Каркас проекта:**
```
Создай структуру проекта Python для Telegram-бота.
Используй файл CURSOR_INSTRUCTIONS.md как спецификацию — там полная структура папок.

Создай:
1. Все папки и __init__.py по дереву из инструкции
2. requirements.txt (скопируй из инструкции)
3. .env.example
4. Dockerfile и docker-compose.yml
5. app/config.py — Pydantic Settings, загрузка из .env
6. app/main.py — FastAPI + aiogram запуск (пустые заглушки)

Не пиши логику — только скелет. Каждый файл-обработчик содержит пустой Router.
```

**Промпт 2 — Модели данных:**
```
Создай модели SQLAlchemy в app/models/ по спецификации из CURSOR_INSTRUCTIONS.md.

Нужны модели:
- PriceItem, PriceHistory (app/models/price.py)
- GlassType (app/models/glass.py)  
- Project (app/models/project.py)
- Calculation, CommercialOffer (app/models/calculation.py)
- CalcSettings (app/models/settings.py)

Также создай:
- app/models/base.py с DeclarativeBase
- app/db/session.py с async sessionmaker для SQLite
- Alembic конфигурацию (alembic.ini + alembic/env.py)
- Начальную миграцию

Важно: CalcSettings должен включать поля для скидки (discount_percent), 
наценки (markup_percent), бонуса дизайнера (designer_bonus_rate = 0.10),
и ставки дизайнера/менеджера (designer_rate, manager_rate).
Формулы расчёта — в CURSOR_INSTRUCTIONS.md.
```

**Промпт 3 — Бот отвечает на /start:**
```
Сделай чтобы бот запускался и отвечал на /start.

В app/bot/bot.py:
- Инициализируй aiogram Bot и Dispatcher
- Подключи router из app/bot/handlers/start.py

В app/bot/handlers/start.py:
- /start — приветствие + главное меню (reply keyboard):
  [📁 Новый проект] [📋 Мои проекты]
  [💰 Прайс] [⚙️ Настройки]

В app/main.py:
- При старте: создать таблицы в БД, запустить бот через polling

Я должен скопировать .env.example в .env, вставить TELEGRAM_BOT_TOKEN,
запустить `python -m app.main` и увидеть работающего бота.
```

**Промпт 4 — Импорт прайса:**
```
Реализуй импорт прайса из XLSX.

В app/services/price_manager.py:
- Функция import_price_from_xlsx() по спецификации из CURSOR_INSTRUCTIONS.md
- Маппинг категорий CATEGORY_MARKERS — скопируй из инструкции
- История изменений цен в PriceHistory

В scripts/import_price.py:
- CLI-скрипт: python scripts/import_price.py path/to/file.xlsx

В app/bot/handlers/price_admin.py:
- /price_upload — загрузка XLSX через Telegram
- /price_search <запрос> — поиск по имени
- /price_edit <id> <цена> — изменение цены
- /price_list — категории inline-кнопками

Тестирую: отправляю боту файл Расчет_кухня_1.xlsx, он импортирует ~350 позиций.
```

**Промпт 5 — Создание проекта (FSM):**
```
Реализуй создание проекта через Telegram FSM.

Когда пользователь нажимает [📁 Новый проект]:
1. Бот спрашивает ФИО клиента → сохраняет
2. Спрашивает телефон → сохраняет  
3. Спрашивает адрес → сохраняет
4. Проект создан, показывает:
   [📷 Отправить фото чертежа] [✏️ Ввести вручную]

Используй aiogram FSM (StatesGroup).
Сохраняй в БД таблицу projects.
```

**Промпт 6 — Ручной ввод модулей:**
```
Реализуй ручной ввод модулей мебели через FSM.

Когда пользователь нажимает [✏️ Ввести вручную]:
1. Спрашивает тип модуля (inline-кнопки): 
   Нижняя база | Верхняя база | Пенал | Колонна | Тумба
2. Спрашивает ширину (мм), предлагая стандартные: 300|400|450|500|600|800|900
3. Глубину: 320 (верхние) | 560 (нижние) — по умолчанию
4. Высоту: 720 (верхние) | 820 (нижние) | 2100 (пенал)
5. Наполнение: Двери | Ящики | Полки | Подъёмник | Бутылочница
6. Количество дверей/ящиков/полок
7. Есть ли стекло в фасаде? Да/Нет

После ввода:
- Показать резюме модуля
- [➕ Добавить ещё модуль] [✅ Готово — выбрать материалы]

Все модули сохраняются в JSON-поле calculation.modules
```

**Промпт 7 — Выбор материалов:**
```
Реализуй выбор материалов из прайса.

Последовательно показывай inline-кнопки:

1. ЛДСП для корпуса:
   Загрузи из БД все PriceItem с category="ЛДСП", покажи как кнопки:
   "EGGER 6000₽" | "EXTRAVERT однотон 5650₽" | ...

2. Фасады:
   Категории: МДФ ПЛИТНЫЙ | ЭФТРИ ПВХ | ЛАКОКРАСКА | АЛЮМИНИЕВЫЙ ФАСАД
   После выбора категории — конкретные позиции

3. Кромка:
   Автоматически подбирается по производителю ЛДСП
   (EGGER → EGGER кромка, EXTRAVERT → EXTRAVERT кромка)

4. Петли: BOYARD | BLUM | HETTICH — потом конкретные модели

5. Ящики (если есть): BOYARD СТАРТ | HETTICH ATIRA | BLUM TANDEMBOX | BLUM LEGRABOX

Сохраняй выбор в calculation.selected_materials и selected_hardware (JSON).
```

**Промпт 8 — Расчёт стекла:**
```
Реализуй ввод и расчёт стекла.

Если при вводе модулей отмечено "есть стекло":
1. Спросить тип стекла (inline):
   Обычное (2500₽/м²) | Рифленое MORU (4250₽/лист) | Графит | Бронза | Для алюм. профиля (2230₽/м²)
2. Спросить размеры стекла: ширина × высота (мм)
3. Нужна ли тонировка? (+2000₽)
4. Показать расчёт: площадь, стоимость

Используй модуль glass_calc.py из CURSOR_INSTRUCTIONS.md — там полная логика.
```

**Промпт 9 — Расчётный движок и смета:**
```
Реализуй расчётный движок, который собирает все данные и считает итог.

В app/services/calc_engine.py:
- Берёт modules, selected_materials, selected_hardware, glass_items из Calculation
- Считает деталировку (детали корпуса для каждого модуля)
- Считает кромку (edge_calc.py)
- Считает количество листов ЛДСП (sheet_calc.py)  
- Считает фурнитуру (hardware_calc.py)
- Считает стекло (glass_calc.py)
- Передаёт всё в cost_calc.py для итоговой сметы

ВАЖНО — формулы из cost_calc.py (в CURSOR_INSTRUCTIONS.md):
- Себестоимость → Изготовление (×0.80) → Монтаж (×0.55)
- Проектировка = Изготовление × 0.10
- Прибыль = Итого × 0.30
- Тех. директор = (Итого + Прибыль) × 0.015
- Безнал = Наличка × 1.13

ДОПОЛНИТЕЛЬНО — скидка/наценка/бонус:
- После расчёта наличной цены применяется скидка ИЛИ наценка (оператор выбирает)
- Если включён бонус дизайнера 10%: финальная_цена = цена_после_скидки / (1 - 0.10)
  Это значит дизайнер получает 10% от финальной цены
- Формула верифицирована: в прайсе РАСЧЕТ ЗЕРКАЛ 26500 / 0.9 = 29444.44 ✓

Покажи результат в Telegram:
━━━━━━━━━━━━━━━━━
ЛДСП EXTRAVERT: 4 листа = 25 600 ₽
Кромка 0,8×19: 120 м = 3 840 ₽
...
━━━━━━━━━━━━━━━━━
Себестоимость: 72 000 ₽
Итого наличка: 152 300 ₽
Скидка 5%: −7 615 ₽
С бонусом дизайнера (10%): 160 761 ₽
Безнал: 181 660 ₽
━━━━━━━━━━━━━━━━━
[📄 Скачать КП] [✏️ Скорректировать]
[💰 Скидка/наценка] [➕ Ещё вариант]
```

**Промпт 10 — Скидка/наценка/бонус:**
```
Реализуй модуль скидок, наценок и бонусов.

Когда оператор нажимает [💰 Скидка/наценка]:
1. Показать текущую цену
2. Inline-кнопки:
   [Скидка %] [Скидка ₽] [Наценка %] [Наценка ₽]
   [Бонус дизайнера ВКЛ/ВЫКЛ]
3. Оператор вводит значение, бот пересчитывает и показывает новую смету

Логика:
- Скидка/наценка применяется к "Итого наличка" (до безнала)
- Бонус дизайнера: финал = цена_со_скидкой / (1 - designer_rate)
  По умолчанию designer_rate = 0.10 (10%)
- Безнал считается уже от финальной суммы с бонусом

Сохранять в calculation:
  discount_type: "percent" | "fixed" | null
  discount_value: float
  markup_type: "percent" | "fixed" | null  
  markup_value: float
  designer_bonus_enabled: bool
  designer_bonus_rate: 0.10

НЕЛЬЗЯ одновременно иметь скидку и наценку — одно из двух.
```

**Промпт 11 — Gemini Flash (распознавание фото):**
```
Реализуй распознавание чертежа через Gemini Flash.

В app/services/image_analyzer.py:
- Скопируй класс GeminiImageAnalyzer из CURSOR_INSTRUCTIONS.md
- pip install google-genai
- Модель: gemini-2.5-flash-preview-05-20
- response_mime_type="application/json" для гарантированного JSON

В app/bot/handlers/image.py:
- Пользователь отправляет фото → бот скачивает → отправляет в Gemini
- Gemini возвращает JSON с модулями
- Бот показывает распознанные модули для подтверждения:
  "Найдено: Нижняя база 600×560 — 3 шт, Верхняя база 600×320 — 2 шт..."
  [✅ Подтвердить] [✏️ Скорректировать]
- Если confidence = "low" → автоматически переключить на ручной ввод

Ключ API берём из .env: GEMINI_API_KEY
```

**Промпт 12 — Генерация PDF КП:**
```
Реализуй генерацию PDF коммерческого предложения.

В app/services/kp_generator.py:
- Используй ReportLab для генерации PDF
- Шаблон КП содержит:
  • Шапка: название компании, дата
  • Данные клиента: ФИО, телефон, адрес
  • Таблица позиций: название, кол-во, цена
  • Итого (без детальной разбивки себестоимости — клиент не видит наценки)
  • Показывать клиенту только: позиция, количество, стоимость, ИТОГО
  • Если скидка — показать: "Скидка 5%: -XX ₽"
  • Итого наличка / безнал

В app/bot/handlers/kp.py:
- [📄 Скачать КП] → генерирует PDF → отправляет как документ в Telegram
- [📤 Отправить клиенту] — (будущее) отправка через WhatsApp API или просто скачивание

Несколько вариантов КП: оператор может создать "Вариант 2" с другими материалами.
```

---

### Советы по работе с Cursor

**Как задавать вопросы правильно:**

Плохо: `сделай бота`

Хорошо: `Реализуй обработчик /start в app/bot/handlers/start.py. Бот должен отвечать приветствием и показывать reply-клавиатуру с кнопками: [📁 Новый проект] [📋 Мои проекты] [💰 Прайс] [⚙️ Настройки]. Используй aiogram 3, Router.`

**Правило маленьких шагов:**
- Один промпт = одна функция или один файл
- После каждого промпта: запусти код, проверь, исправь ошибки
- Если Cursor сломал что-то — скажи: `Ты сломал файл X. Верни его к рабочему состоянию и сделай только Y`

**Когда код не работает:**
1. Скопируй ошибку из терминала
2. Вставь в Cursor Chat (Cmd+L): `Вот ошибка при запуске. Исправь:`
3. Cursor обычно точно находит и исправляет баги

**Используй @-mentions:**
- В Composer пиши `@file app/services/cost_calc.py` чтобы Cursor видел контекст конкретного файла
- `@folder app/models/` — показать все модели
- `@CURSOR_INSTRUCTIONS.md` — сослаться на эту спецификацию

**Горячие клавиши Cursor:**
- `Cmd/Ctrl + I` — открыть Composer (главный инструмент)
- `Cmd/Ctrl + L` — Chat (для вопросов и отладки)
- `Cmd/Ctrl + K` — редактирование выделенного кода
- `Tab` — принять подсказку автокомплита

**Когда застрял:**
Скажи Cursor: `Прочитай файл CURSOR_INSTRUCTIONS.md и реализуй секцию [название]. Следуй спецификации точно.`

**Тестирование:**
После каждой фазы проверяй:
```bash
# Запуск бота
python -m app.main

# В другом терминале — проверка БД
python -c "from app.db.session import ...; # проверка"

# Отправь боту команду и проверь ответ
```

---

## ЧАСТЬ 1. ОБЩЕЕ ОПИСАНИЕ

Telegram-бот для расчёта стоимости корпусной мебели. Менеджер фотографирует чертёж → бот распознаёт размеры через Gemini Flash → менеджер выбирает материалы → бот формирует смету и PDF-КП.

**Стек:** Python 3.12, FastAPI, aiogram 3, Google Gemini Flash API, SQLite (→ PostgreSQL), ReportLab, openpyxl.

---

## ЧАСТЬ 2. СТРУКТУРА ПРОЕКТА

```
furniture-calc/
├── .cursorrules                   # Правила для Cursor AI
├── CURSOR_INSTRUCTIONS.md         # Этот файл — полная спецификация
├── docker-compose.yml
├── Dockerfile
├── .env.example
├── .env                           # НЕ КОММИТИТЬ — в .gitignore
├── requirements.txt
├── alembic.ini
├── alembic/
│   └── versions/
│
├── app/
│   ├── __init__.py
│   ├── main.py                    # FastAPI + запуск бота
│   ├── config.py                  # Pydantic Settings (.env)
│   │
│   ├── bot/
│   │   ├── __init__.py
│   │   ├── bot.py                 # aiogram Bot + Dispatcher
│   │   ├── middlewares.py         # Логирование, сессии БД
│   │   ├── handlers/
│   │   │   ├── __init__.py
│   │   │   ├── start.py           # /start, /help
│   │   │   ├── project.py         # Создание проекта (FSM)
│   │   │   ├── image.py           # Фото → Gemini → подтверждение
│   │   │   ├── manual_input.py    # Ручной ввод модулей (FSM)
│   │   │   ├── materials.py       # Выбор материалов (inline)
│   │   │   ├── hardware.py        # Выбор фурнитуры (inline)
│   │   │   ├── glass.py           # Расчёт стекла
│   │   │   ├── calculation.py     # Запуск расчёта, показ сметы
│   │   │   ├── discounts.py       # Скидки, наценки, бонус дизайнера
│   │   │   ├── kp.py              # Генерация и отправка КП
│   │   │   └── price_admin.py     # Управление прайсом
│   │   ├── keyboards/
│   │   │   ├── __init__.py
│   │   │   ├── inline.py
│   │   │   └── reply.py
│   │   └── states/
│   │       ├── __init__.py
│   │       ├── project.py
│   │       ├── manual_input.py
│   │       ├── discounts.py
│   │       └── price_admin.py
│   │
│   ├── services/
│   │   ├── __init__.py
│   │   ├── image_analyzer.py      # Gemini Flash API
│   │   ├── calc_engine.py         # Главный расчётный движок
│   │   ├── glass_calc.py          # Расчёт стекла
│   │   ├── edge_calc.py           # Расчёт кромки
│   │   ├── sheet_calc.py          # Расчёт листов ЛДСП/МДФ
│   │   ├── hardware_calc.py       # Расчёт фурнитуры
│   │   ├── cost_calc.py           # Итоговая смета + скидки/бонусы
│   │   ├── price_manager.py       # Импорт/экспорт прайса
│   │   └── kp_generator.py        # Генерация PDF КП
│   │
│   ├── models/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── project.py
│   │   ├── calculation.py
│   │   ├── price.py
│   │   ├── glass.py
│   │   └── settings.py
│   │
│   ├── schemas/
│   │   ├── __init__.py
│   │   ├── furniture.py           # Module, Facade, Drawer
│   │   ├── price.py
│   │   ├── glass.py
│   │   └── calculation.py         # CostBreakdown, DiscountInfo
│   │
│   └── db/
│       ├── __init__.py
│       ├── session.py
│       └── seed_price.py
│
├── scripts/
│   └── import_price.py
│
├── templates/
│   └── kp_template.html
│
└── tests/
    ├── test_calc_engine.py
    ├── test_glass_calc.py
    ├── test_cost_calc.py
    └── test_discounts.py
```

---

## ЧАСТЬ 3. МОДУЛЬ СКИДОК, НАЦЕНОК И БОНУСОВ

### Бизнес-логика (извлечена из прайса)

**Формула бонуса дизайнера** (верифицирована из листа "РАСЧЕТ ЗЕРКАЛ"):
```
Итого (наличка): 26 500 ₽
С бонусом дизайнера: 29 444.44 ₽
Формула: 26500 / (1 - 0.10) = 29444.44 ✓

Дизайнер получает 10% от ФИНАЛЬНОЙ цены (не от базовой).
Это значит: финальная_цена = базовая_цена / 0.9
```

**Формула бонуса из листа "СТОЛЕШНИЦЫ":**
```
Итого: 7 359 ₽
С бонусом дизайнера: 8 176.67 ₽
7359 / 0.9 = 8176.67 ✓ — та же формула
```

**Ставки дизайнера и менеджера** (из СТОЛЕШНИЦЫ):
```
Тех. директор: 7250 × 0.015 = 108.75 ≈ 109 ₽
Дизайнер: 7250 × 0.014 ≈ 102 ₽ (ставка ~1.4%)
Менеджер: 7250 × 0.028 ≈ 203 ₽ (ставка ~2.8%)
```

### Порядок применения

```
1. Рассчитать базовую цену (cost_calc.py)
   → Себестоимость → Изготовление → Монтаж → ... → Итого с прибылью

2. Начислить тех. директора и менеджера
   → total_with_salaries = total_with_profit + tech_director + manager

3. Применить скидку ИЛИ наценку (ВЗАИМОИСКЛЮЧАЮЩИЕ)
   → если скидка %: discounted = total_with_salaries × (1 - discount/100)
   → если скидка ₽: discounted = total_with_salaries - discount_amount
   → если наценка %: marked_up = total_with_salaries × (1 + markup/100)
   → если наценка ₽: marked_up = total_with_salaries + markup_amount

4. Применить бонус дизайнера (если включён)
   → final_cash = price_after_discount / (1 - designer_bonus_rate)
   → designer_gets = final_cash - price_after_discount

5. Рассчитать безнал
   → final_noncash = final_cash × noncash_markup (1.13)
```

### Модель данных — дополнение к Calculation

```python
# Добавить в app/models/calculation.py в класс Calculation:

    # Скидка/наценка
    discount_type = Column(String(20), nullable=True)
    # "percent" | "fixed" | None
    discount_value = Column(Float, default=0)
    # Значение скидки (5 = 5% или 5000 = 5000₽)

    markup_type = Column(String(20), nullable=True)
    # "percent" | "fixed" | None  
    markup_value = Column(Float, default=0)

    # Бонус дизайнера
    designer_bonus_enabled = Column(Integer, default=0)  # 0/1
    designer_bonus_rate = Column(Float, default=0.10)     # 10%

    # Итоговые цены (пересчитываются)
    price_after_discount = Column(Float, nullable=True)
    designer_bonus_amount = Column(Float, nullable=True)
    final_price_cash = Column(Float, nullable=True)
    final_price_noncash = Column(Float, nullable=True)
```

### Сервис — app/services/cost_calc.py (обновлённый)

```python
"""
ПОЛНАЯ формула расчёта с скидками/наценками/бонусами.

Верифицировано на данных из прайса:
- Лист "Влад и Кристина": коэффициенты 0.80, 0.55, 0.10, 0.05, 0.30, 0.015, 1.13 ✓
- Лист "РАСЧЕТ ЗЕРКАЛ": бонус дизайнера = цена / (1 - 0.10) ✓
- Лист "СТОЛЕШНИЦЫ": та же формула бонуса ✓
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DiscountInfo:
    """Информация о скидке/наценке."""
    discount_type: Optional[str] = None   # "percent" | "fixed" | None
    discount_value: float = 0
    markup_type: Optional[str] = None     # "percent" | "fixed" | None
    markup_value: float = 0
    designer_bonus_enabled: bool = False
    designer_bonus_rate: float = 0.10     # 10% по умолчанию


@dataclass
class CostBreakdown:
    """Полная разбивка стоимости."""
    # === Входные данные (стоимость позиций) ===
    material_cost: float = 0        # ЛДСП, МДФ, ХДФ
    edge_cost: float = 0            # кромка
    facade_cost: float = 0          # фасады
    hardware_cost: float = 0        # фурнитура (петли, ящики, подъёмники, направляющие)
    glass_cost: float = 0           # стекло
    lighting_cost: float = 0        # подсветка
    countertop_cost: float = 0      # столешница + комплектующие
    accessories_cost: float = 0     # ручки, сушки, лотки, цоколь и т.д.
    subcontractor_cost: float = 0   # от подрядчиков

    # === Рассчитываемые поля ===
    cost_price: float = 0           # себестоимость (сумма всего)
    manufacturing: float = 0        # изготовление = себестоимость × 0.80
    installation: float = 0         # монтаж = себестоимость × 0.55
    measurement: float = 0          # замер (фикс.)
    delivery_internal: float = 0    # доставка (себестоимость)
    delivery_client: float = 0      # доставка (клиенту)
    design: float = 0               # проектировка = изготовление × 0.10
    overhead: float = 0             # прочие = себестоимость × 0.05
    subtotal: float = 0             # промежуточный итог
    profit: float = 0               # прибыль = итого × 0.30
    total_with_profit: float = 0    # итого с прибылью
    tech_director: float = 0        # тех. директор = (итого + прибыль) × 0.015
    manager_salary: float = 0       # менеджер
    designer_salary: float = 0      # дизайнер (фикс. ставка, не бонус)
    total_base_cash: float = 0      # итого наличка (до скидок)

    # === Скидка / наценка ===
    discount_amount: float = 0      # сумма скидки (отрицательная = наценка)
    price_after_discount: float = 0 # цена после скидки/наценки

    # === Бонус дизайнера ===
    designer_bonus_amount: float = 0  # сколько получит дизайнер
    final_cash: float = 0             # итого наличка ФИНАЛ

    # === Безнал ===
    final_noncash: float = 0          # итого безнал


def calculate_cost(
    # Стоимость позиций
    material_cost: float = 0,
    edge_cost: float = 0,
    facade_cost: float = 0,
    hardware_cost: float = 0,
    glass_cost: float = 0,
    lighting_cost: float = 0,
    countertop_cost: float = 0,
    accessories_cost: float = 0,
    subcontractor_cost: float = 0,
    # Коэффициенты
    manufacturing_rate: float = 0.80,
    installation_rate: float = 0.55,
    design_rate: float = 0.10,
    overhead_rate: float = 0.05,
    profit_rate: float = 0.30,
    tech_director_rate: float = 0.015,
    manager_rate: float = 0.0,
    designer_rate: float = 0.0,       # ставка дизайнера (не бонус!)
    measurement_fee: float = 2500,
    delivery_fee: float = 6500,
    delivery_fee_client: float = 10000,
    noncash_markup: float = 1.13,
    # Скидка / наценка / бонус
    discount: Optional[DiscountInfo] = None,
) -> CostBreakdown:
    """
    Полный расчёт стоимости заказа.

    Порядок:
    1. Себестоимость = сумма всех позиций
    2. Производные: изготовление, монтаж, проектировка, прочие
    3. Итого + прибыль + зарплаты
    4. Скидка или наценка
    5. Бонус дизайнера (если включён)
    6. Безнал
    """
    r = CostBreakdown(
        material_cost=material_cost,
        edge_cost=edge_cost,
        facade_cost=facade_cost,
        hardware_cost=hardware_cost,
        glass_cost=glass_cost,
        lighting_cost=lighting_cost,
        countertop_cost=countertop_cost,
        accessories_cost=accessories_cost,
        subcontractor_cost=subcontractor_cost,
    )

    disc = discount or DiscountInfo()

    # 1. Себестоимость
    r.cost_price = (
        material_cost + edge_cost + facade_cost + hardware_cost +
        glass_cost + lighting_cost + countertop_cost +
        accessories_cost + subcontractor_cost
    )

    # 2. Производные
    r.manufacturing = round(r.cost_price * manufacturing_rate)
    r.installation = round(r.cost_price * installation_rate)
    r.measurement = measurement_fee
    r.delivery_internal = delivery_fee
    r.delivery_client = delivery_fee_client
    r.design = round(r.manufacturing * design_rate)
    r.overhead = round(r.cost_price * overhead_rate)

    # 3. Итого
    r.subtotal = (
        r.cost_price + r.manufacturing + r.installation +
        r.measurement + r.delivery_internal +
        r.design + r.overhead
    )
    r.profit = round(r.subtotal * profit_rate)
    r.total_with_profit = r.subtotal + r.profit

    # Зарплаты
    r.tech_director = round(r.total_with_profit * tech_director_rate)
    r.manager_salary = round(r.total_with_profit * manager_rate)
    r.designer_salary = round(r.total_with_profit * designer_rate)

    r.total_base_cash = (
        r.total_with_profit + r.tech_director +
        r.manager_salary + r.designer_salary
    )

    # 4. Скидка или наценка (взаимоисключающие)
    r.price_after_discount = r.total_base_cash

    if disc.discount_type == "percent" and disc.discount_value > 0:
        r.discount_amount = round(r.total_base_cash * disc.discount_value / 100)
        r.price_after_discount = r.total_base_cash - r.discount_amount

    elif disc.discount_type == "fixed" and disc.discount_value > 0:
        r.discount_amount = disc.discount_value
        r.price_after_discount = r.total_base_cash - r.discount_amount

    elif disc.markup_type == "percent" and disc.markup_value > 0:
        markup = round(r.total_base_cash * disc.markup_value / 100)
        r.discount_amount = -markup  # отрицательная скидка = наценка
        r.price_after_discount = r.total_base_cash + markup

    elif disc.markup_type == "fixed" and disc.markup_value > 0:
        r.discount_amount = -disc.markup_value
        r.price_after_discount = r.total_base_cash + disc.markup_value

    # 5. Бонус дизайнера
    if disc.designer_bonus_enabled and disc.designer_bonus_rate > 0:
        # Формула: финал = цена / (1 - ставка)
        # Верифицировано: 26500 / 0.9 = 29444.44
        r.final_cash = round(
            r.price_after_discount / (1 - disc.designer_bonus_rate), 2
        )
        r.designer_bonus_amount = round(r.final_cash - r.price_after_discount, 2)
    else:
        r.final_cash = r.price_after_discount
        r.designer_bonus_amount = 0

    # 6. Безнал
    r.final_noncash = round(r.final_cash * noncash_markup, 2)

    return r
```

### Обработчик — app/bot/handlers/discounts.py

```python
"""
Обработчик скидок/наценок/бонуса дизайнера.

Сценарий:
1. Оператор нажимает [💰 Скидка/наценка] в смете
2. Показываются кнопки:
   [Скидка %] [Скидка ₽] [Наценка %] [Наценка ₽]
   [Бонус дизайнера: ВКЛ ✅ / ВЫКЛ ❌]
3. Оператор вводит значение
4. Бот пересчитывает и показывает обновлённую смету
"""

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from app.bot.keyboards.inline import discount_keyboard

router = Router()


class DiscountState(StatesGroup):
    choosing_type = State()
    entering_value = State()


@router.callback_query(F.data == "discount_menu")
async def show_discount_menu(callback: CallbackQuery, state: FSMContext):
    """Показать меню скидок/наценок."""
    data = await state.get_data()
    calc_id = data.get("current_calc_id")

    # Загрузить текущий расчёт из БД
    # ... (получить calculation)

    text = (
        "💰 Скидка / наценка / бонус\n\n"
        f"Текущая цена наличка: {calculation.total_base_cash:,.0f} ₽\n"
        f"Скидка: {_format_discount(calculation)}\n"
        f"Бонус дизайнера: {'✅ ВКЛ' if calculation.designer_bonus_enabled else '❌ ВЫКЛ'}\n"
        f"Финальная цена: {calculation.final_price_cash:,.0f} ₽"
    )

    await callback.message.edit_text(
        text,
        reply_markup=discount_keyboard(calculation),
    )


# discount_keyboard возвращает InlineKeyboardMarkup:
# [Скидка %]  [Скидка ₽]
# [Наценка %] [Наценка ₽]
# [Бонус дизайнера ВКЛ/ВЫКЛ]
# [Убрать скидку] [← Назад к смете]


@router.callback_query(F.data == "toggle_designer_bonus")
async def toggle_designer_bonus(callback: CallbackQuery, state: FSMContext, session):
    """Включить/выключить бонус дизайнера."""
    data = await state.get_data()
    calc_id = data["current_calc_id"]

    # Переключить designer_bonus_enabled
    # Пересчитать final_price
    # Показать обновлённую смету

    await callback.answer("Бонус дизайнера переключён")
    await show_discount_menu(callback, state)


@router.callback_query(F.data.startswith("set_discount_"))
async def start_discount_input(callback: CallbackQuery, state: FSMContext):
    """Начать ввод значения скидки/наценки."""
    discount_type = callback.data.replace("set_discount_", "")
    # discount_type: "percent", "fixed", "markup_percent", "markup_fixed"

    await state.update_data(discount_type=discount_type)
    await state.set_state(DiscountState.entering_value)

    labels = {
        "percent": "Введите процент скидки (например: 5)",
        "fixed": "Введите сумму скидки в рублях (например: 10000)",
        "markup_percent": "Введите процент наценки (например: 10)",
        "markup_fixed": "Введите сумму наценки в рублях (например: 5000)",
    }
    await callback.message.answer(labels.get(discount_type, "Введите значение:"))


@router.message(DiscountState.entering_value)
async def process_discount_value(message: Message, state: FSMContext, session):
    """Обработать введённое значение скидки/наценки."""
    try:
        value = float(message.text.replace(",", ".").replace(" ", ""))
    except ValueError:
        await message.answer("Введите число. Попробуйте ещё раз:")
        return

    data = await state.get_data()
    discount_type = data["discount_type"]
    calc_id = data["current_calc_id"]

    # Обновить Calculation в БД
    # Пересчитать через cost_calc.calculate_cost()
    # Показать обновлённую смету

    await state.set_state(None)
    # ... показать результат
```

---

## ЧАСТЬ 4. GEMINI FLASH ИНТЕГРАЦИЯ

```python
# app/services/image_analyzer.py
# Полный код — см. предыдущую версию инструкции
# Ключевые моменты:

# pip install google-genai
# Модель: gemini-2.5-flash-preview-05-20
# response_mime_type="application/json" — гарантирует JSON без обёрток
# temperature=0.1 — минимальная креативность для точности
# Системный промпт на русском — описывает мебельную терминологию
```

---

## ЧАСТЬ 5. РАСЧЁТ СТЕКЛА

```python
# app/services/glass_calc.py
# Полный код — см. предыдущую версию инструкции
# Ключевые моменты:

# Типы с фиксированной ценой:
#   clear (2500₽/м²), moru_riflenoe (4250₽/лист 2000×3300), 
#   for_aluminum (2230₽/м²), тонировка (+2000₽)
#
# Типы БЕЗ цены (оператор вводит вручную, запрашивая у поставщика):
#   graphite, bronze, tempered
#   → при выборе бот спрашивает: "Введите цену за м² для стекла графит:"
#   → предлагает сохранить в прайс на будущее
#
# Фасады на заказ: цена_за_м² × площадь (подтверждено)
# MORU: считается листами, с оптимизацией укладки деталей
# Коэффициент использования листа ЛДСП: 0.85 (подтверждено)
```

---

## ЧАСТЬ 6. УПРАВЛЕНИЕ ПРАЙСОМ

```python
# app/services/price_manager.py
# Полный код — см. предыдущую версию инструкции
# Ключевые моменты:

# import_price_from_xlsx() — парсит XLSX с маппингом категорий
# update_price() — обновление цены с журналированием в PriceHistory
# Telegram-команды: /price_upload, /price_search, /price_edit, /price_list
```

---

## ЧАСТЬ 7. КОНФИГУРАЦИЯ

### .env.example

```env
# Telegram
TELEGRAM_BOT_TOKEN=123456:ABC-DEF

# Gemini Flash
GEMINI_API_KEY=AIza...

# Database
DATABASE_URL=sqlite+aiosqlite:///./data/furniture.db

# Коэффициенты расчёта
MANUFACTURING_RATE=0.80
INSTALLATION_RATE=0.55
DESIGN_RATE=0.10
OVERHEAD_RATE=0.05
PROFIT_RATE=0.30
TECH_DIRECTOR_RATE=0.015
MANAGER_RATE=0.0
DESIGNER_RATE=0.0
MEASUREMENT_FEE=2500
DELIVERY_FEE=6500
DELIVERY_FEE_CLIENT=10000
NONCASH_MARKUP=1.13
SHEET_UTILIZATION=0.85

# Бонус дизайнера (по умолчанию)
DESIGNER_BONUS_RATE=0.10
```

### requirements.txt

```
aiogram==3.15.0
fastapi==0.115.0
uvicorn==0.32.0
sqlalchemy==2.0.36
aiosqlite==0.20.0
alembic==1.14.0
openpyxl==3.1.5
reportlab==4.2.5
google-genai==1.14.0
pydantic==2.10.0
pydantic-settings==2.7.0
httpx==0.28.0
python-dotenv==1.0.1
Pillow==11.1.0
```

---

## ЧАСТЬ 8. ПОРЯДОК РАЗРАБОТКИ

| Фаза | Дни | Что делать | Промпт в Cursor |
|------|-----|-----------|-----------------|
| 1. Каркас | 1-2 | Структура, модели, /start | Промпты 1-3 |
| 2. Прайс | 3-4 | Импорт XLSX, поиск, редактирование | Промпт 4 |
| 3. Проекты | 5-7 | FSM создания, ввод модулей, выбор материалов | Промпты 5-7 |
| 4. Стекло | 8 | Ввод и расчёт стекла | Промпт 8 |
| 5. Расчёт | 9-11 | Движок, смета, скидки/бонусы | Промпты 9-10 |
| 6. Gemini | 12-14 | Распознавание фото | Промпт 11 |
| 7. КП | 15-16 | Генерация PDF | Промпт 12 |
| 8. Тест | 17-20 | Сверка с Excel, баг-фиксы, деплой | Вручную |

---

## ЧАСТЬ 9. ПРИНЯТЫЕ РЕШЕНИЯ (бывшие неопределённости)

Все вопросы уточнены с заказчиком. Ниже — финальные решения, которые ОБЯЗАТЕЛЬНО учитывать при разработке.

### 9.1. Позиции с пустыми ценами — ручной ввод оператором

Следующие позиции не имеют цен в прайсе. Решение: **оператор вводит цену вручную при расчёте**, запрашивая её у поставщика. Система должна позволять это.

Позиции без цен:
- Стекло графит
- Стекло бронза
- Стекло каленное
- Столешницы КЕДР (1-5)
- Комплектующие к столешнице (кромки SLOTEX/FORA&STYLE/EGGER, планки угловые/торцевые/соединительные/щелевые)
- Кромка LAMARTY (0.4×19, 0.8×19, 2×19, 2×35)
- Ящики Boyard PUSH (h=86/116/167 мм)
- Трубка синхронизации для СТАРТ PUSH 1500мм
- GOLA профили с LED подсветкой 4м и 6м (для нижних и верхних баз)

**Реализация в коде:**

```python
# В модели PriceItem добавить флаг:
class PriceItem(Base):
    # ... существующие поля ...
    requires_manual_price = Column(Integer, default=0)
    # 1 = цена не установлена, оператор ДОЛЖЕН ввести при использовании

# При импорте прайса: если unit_price is None или 0, 
# и позиция не является заголовком категории → 
# создать PriceItem с requires_manual_price=1

# В обработчике выбора материалов:
# Если оператор выбирает позицию с requires_manual_price=1:
#   Бот: "Цена для «Стекло графит» не установлена. 
#          Введите цену за м² (запросите у поставщика):"
#   Оператор: 3200
#   Бот: "Стекло графит: 3 200 ₽/м². Сохранить в прайс на будущее? [Да] [Нет, только для этого заказа]"
```

### 9.2. Коэффициент использования листа ЛДСП

**Решение: 0.85 (85%) — подтверждено.**

Это значит: при расчёте количества листов ЛДСП используется формула:
```
кол-во_листов = ceil(суммарная_площадь_деталей / (площадь_листа × 0.85))
площадь_листа = 2800 × 2070 = 5 796 000 мм² = 5.796 м²
полезная_площадь = 5.796 × 0.85 = 4.927 м²
```

### 9.3. Правила кромления — уточняет оператор

**Решение: бот спрашивает оператора для каждого модуля.**

Реализация — при расчёте каждого модуля бот предлагает выбор:
```
Кромка для деталей модуля "Нижняя база 600×560":
Выберите профиль кромления:

[Стандартный кухонный]
  • Лицевые торцы: 0.8 мм
  • Скрытые торцы: без кромки
  
[Улучшенный]
  • Лицевые торцы: 2 мм
  • Скрытые торцы: 0.4 мм

[Ручной выбор]
  • Оператор указывает для каждой стороны
```

Пресеты кромления (реализовать в коде):
```python
EDGE_PRESETS = {
    "kitchen_standard": {
        "name": "Стандартный кухонный",
        "visible_edge": "0.8",      # толщина кромки для видимых торцов
        "hidden_edge": None,         # без кромки на скрытых
        "description": "Лицевые: 0.8мм, скрытые: без"
    },
    "improved": {
        "name": "Улучшенный",
        "visible_edge": "2",
        "hidden_edge": "0.4",
        "description": "Лицевые: 2мм, скрытые: 0.4мм"
    },
    "premium": {
        "name": "Премиум",
        "visible_edge": "2",
        "hidden_edge": "0.8",
        "description": "Лицевые: 2мм, скрытые: 0.8мм"
    },
}
```

### 9.4. Фасады на заказ — цена за м² × площадь

**Решение: ДА, подтверждено.**

Формула для фасадов IVEGO, ЭФТРИ, крашенный МДФ и др.:
```
стоимость_фасада = цена_за_м² × (ширина_мм × высота_мм / 1_000_000)
```

Пример: фасад 596×716 мм из EVOGLOSS 18мм (6800 ₽/м²):
```
площадь = 0.596 × 0.716 = 0.4267 м²
стоимость = 6800 × 0.4267 = 2 902 ₽
```

### 9.5. Коэффициенты — единые для всех типов изделий

**Решение: единый набор коэффициентов, столешницы НЕ требуют отдельного модуля.**

Используются стандартные коэффициенты из CalcSettings:
- Изготовление: ×0.80
- Монтаж: ×0.55
- Замер: 2 500 ₽ (фикс.)
- Доставка: 6 500 ₽ / 10 000 ₽
- Проектировка: ×0.10 от изготовления
- Прочие: ×0.05
- Прибыль: ×0.30
- Тех. директор: ×0.015
- Безнал: ×1.13

Если для конкретного заказа нужны другие коэффициенты — оператор меняет их через [⚙️ Настройки] перед расчётом.

---

## ЧАСТЬ 10. СВОДКА ВСЕХ БИЗНЕС-ПРАВИЛ (для Cursor)

Этот раздел содержит все бизнес-правила в одном месте. При реализации любого модуля сверяйся с ним.

### Расчёт количества листов ЛДСП/МДФ
```
площадь_деталей = Σ(ширина_i × высота_i) для всех деталей из данного материала
площадь_листа = 2800 × 2070 мм = 5.796 м²
кол-во_листов = ceil(площадь_деталей / (площадь_листа × 0.85))
```

### Расчёт кромки
```
Для каждой детали:
  определить видимые торцы → кромка выбранной толщины
  определить скрытые торцы → кромка 0.4 мм (или без кромки)
  
Метраж = Σ длин торцов (в метрах) для каждой толщины кромки
Стоимость = метраж × цена_за_метр (из прайса)
```

### Расчёт фасадов
```
Если фасад из листового материала (ЛДСП, МДФ плитный):
  считается как часть деталировки листового материала

Если фасад на заказ (IVEGO, ЭФТРИ, крашенный, алюминиевый):
  стоимость = цена_за_м² × (ширина × высота / 1_000_000)
  
Алюминиевый фасад: фиксированная цена 11 000 ₽/шт
```

### Расчёт стекла
```
Обычное/графит/бронза: площадь_м² × цена_за_м²
Для алюм. профиля: площадь_м² × 2230 ₽
Рифлёное MORU: количество_листов × 4250 ₽
  (лист 2000×3300, с оптимизацией укладки)
Тонировка: +2000 ₽ за единицу
Если цена не установлена → запросить у оператора
```

### Расчёт фурнитуры (петли)
```
Количество петель на фасад:
  высота < 900 мм → 2 петли
  900-1200 мм → 3 петли
  1200-1600 мм → 4 петли
  > 1600 мм → 5 петель

Стоимость = количество × цена_петли (из прайса)
```

### Расчёт ящиков
```
Тип ящика = выбор оператора (Boyard/Hettich/Blum)
Глубина направляющей = глубина_модуля - 50 мм
  → округление до стандартных: 250/300/350/400/450/500/550

Стоимость = цена_ящика (из прайса) × количество
Цена ящика зависит от: бренд + высота (для низких/средних/высоких фасадов)
```

### Итоговая смета
```
1. Себестоимость = Σ(материалы + кромка + фасады + фурнитура + стекло + подсветка + столешница + аксессуары + подрядчики)
2. Изготовление = Себестоимость × 0.80
3. Монтаж = Себестоимость × 0.55
4. Замер = 2 500 ₽
5. Доставка = 6 500 ₽ (внутр.) / 10 000 ₽ (клиенту)
6. Проектировка = Изготовление × 0.10
7. Прочие расходы = Себестоимость × 0.05
8. ИТОГО = сумма пп.1-7
9. Прибыль = ИТОГО × 0.30
10. Итого с прибылью = ИТОГО + Прибыль
11. Тех. директор = Итого_с_прибылью × 0.015
12. Менеджер = Итого_с_прибылью × manager_rate
13. Дизайнер = Итого_с_прибылью × designer_rate
14. Итого наличка (база) = пп.10 + 11 + 12 + 13
15. Скидка ИЛИ наценка (% или ₽)
16. Бонус дизайнера (если вкл.): финал = цена / (1 - 0.10)
17. Безнал = финальная_наличка × 1.13
```

### Позиции без цен
```
При выборе позиции с requires_manual_price=1:
  → запросить цену у оператора
  → предложить сохранить в прайс
  → использовать в расчёте
```
