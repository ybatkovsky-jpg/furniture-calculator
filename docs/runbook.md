# Runbook: резервное копирование и восстановление

Документ для оператора/администратора. Относится к P1-15 (этап 0 — минимальная
часть: копия + проверка восстановления; Alembic/WAL — этап 4).

## 1. Что бэкапим

| Данные | Путь | Куда |
|---|---|---|
| SQLite-база | `data/furniture.db` | `..\backups\furniture-calculator\` (вне Git) |
| Сгенерированные КП и PDF | `pdfs/` | архив вне Git (zip, по регламенту) |
| Сканы/рендеры (эталоны) | `input_images/`, `output/`, `temp/` | не в Git; при необходимости — архив |

Правило: копия на том же физическом диске не защищает от отказа диска. Минимум
два носителя (внешний диск/сетевой каталог).

## 2. Ежедневная копия БД

```powershell
.\.venv\Scripts\python.exe scripts\backup_db.py --keep 14
```

Скрипт:

1. открывает БД в режиме `mode=ro` и делает **консистентную** копию через
   `sqlite3.Connection.backup` (не копирование файла «на живую»);
2. проверяет `PRAGMA integrity_check` у источника и у копии;
3. кладёт файл `furniture_ГГГГММДД_ЧЧММСС.db` в каталог вне репозитория;
4. удаляет копии старше `--keep` дней (по умолчанию 14).

Переопределение путей: `--db`, `--dest` или переменные `DB_PATH`, `BACKUP_DIR`,
`BACKUP_KEEP_DAYS`.

### Расписание (Windows Task Scheduler)

Создать задачу «Furniture DB backup», ежедневно 03:00:

```
Программа: D:\БИЗНЕС\ПРО МЕБЕЛЬ\furniture-calculator\.venv\Scripts\python.exe
Аргументы: scripts\backup_db.py --keep 14
Рабочая папка: D:\БИЗНЕС\ПРО МЕБЕЛЬ\furniture-calculator
```

## 3. Проверка восстановления (обязательно, не реже раза в месяц)

```powershell
# 1. Взять последнюю копию
$backup = Get-ChildItem ..\backups\furniture-calculator\furniture_*.db |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1

# 2. Восстановить в отдельный каталог (НЕ поверх рабочей БД)
New-Item -ItemType Directory -Force temp\restore_test | Out-Null
Copy-Item $backup.FullName temp\restore_test\furniture.db -Force

# 3. Сверить counts и integrity
.\.venv\Scripts\python.exe -c @"
import sqlite3
con = sqlite3.connect(r'temp\restore_test\furniture.db')
print('integrity:', con.execute('PRAGMA integrity_check').fetchone()[0])
for t in ('projects', 'calculations', 'price_items'):
    print(t, con.execute(f'SELECT count(*) FROM {t}').fetchone()[0])
"@
```

Ожидаемо: `integrity: ok`, counts не меньше значений на момент копии. Записать
дату проверки и counts в журнал ниже.

## 4. Восстановление рабочей БД (инцидент)

1. Остановить бота (закрыть процесс/контейнер).
2. Сохранить текущую (повреждённую) БД как `furniture.db.broken-ГГГГММДД`.
3. Скопировать выбранную копию в `data/furniture.db`.
4. Запустить проверку из п. 3 на восстановленной БД.
5. Запустить бота, проверить `/start`, открыть один проект, выполнить расчёт.
6. Зафиксировать инцидент: когда, какая копия, что потеряно (RPO ≤ 24 ч).

## 4a. Миграции схемы (Alembic, P1-12)

С 2026-11-17 схема обновляется **миграциями**, а не `create_all` (он не умеет
добавлять колонки в существующую таблицу). Бот применяет `alembic upgrade head`
при старте; ошибка миграции останавливает старт с понятным сообщением.

Порядок перед любым обновлением кода:

```powershell
# 1. Копия БД (обязательно)
.\.venv\Scripts\python.exe scripts\backup_db.py

# 2. Проверить, что копия открывается и integrity_check = ok (см. п. 3)

# 3. Применить миграции (то же сделает бот при старте)
.\.venv\Scripts\python.exe -m alembic upgrade head

# 4. Проверить текущую ревизию
.\.venv\Scripts\python.exe -m alembic current
```

Ожидаемо: `alembic current` показывает `b7e1c4a9d2f3 (head)`. Откат — только из
копии БД: миграции обратимы не везде, а данные важнее схемы.

## 5. Журнал проверок

| Дата | Копия | integrity | projects | calculations | price_items | Кто |
|---|---|---|---|---|---|---|
| 2026-11-15 | `furniture_20261115_204752.db` | ok | 53 | 18 | 525 | проверка восстановления: копия открылась, counts live/copy совпали (projects 53, calculations 18, price_items 525) |
| 2026-11-17 | `furniture_20261117_144800.db` | ok | 53 | 18 | 525 | восстановление проверено: копия развёрнута в temp, `integrity_check=ok`, counts по 8 таблицам совпали с рабочей БД (18/2/122/525/53/0/0/1) |

## 6. Ограничения

- `create_all` больше не используется для обновления рабочей БД (только тесты).
  Схема — `alembic upgrade head`, см. п. 4a.
- WAL включён для SQLite (`journal_mode=WAL`, `busy_timeout=5000`) — см.
  `app/db/session.py`; одного писателя это не отменяет.
- Копии `pdfs/` автоматически не делаются: это отдельная процедура архивации
  (ПДн, доступ по ролям).
