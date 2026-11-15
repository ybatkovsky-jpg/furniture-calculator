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

## 5. Журнал проверок

| Дата | Копия | integrity | projects | calculations | price_items | Кто |
|---|---|---|---|---|---|---|
| 2026-11-15 | — | — | — | — | — | первичная настройка: копия ещё не снята |

## 6. Ограничения этапа 0

- Миграций (Alembic) пока нет: схема создаётся `create_all`; изменение схемы
  через миграции — этап 4 (P1-15b/P2-11).
- WAL не включён: одновременная запись может блокироваться; включение и
  регламент — этап 4.
- Копии `pdfs/` автоматически не делаются: это отдельная процедура архивации
  (ПДн, доступ по ролям).
