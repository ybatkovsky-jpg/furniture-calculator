# Переход на PostgreSQL (S5-04)

Документ описывает, как поднять PostgreSQL и перевести приложение.

**Статус 2026-11-15:** миграции и перенос данных **проверены** на переносном
PostgreSQL 16.2 (Windows-бинарники, порт 55432, без Docker):
- `alembic upgrade head` — 3 миграции, ошибок нет; `alembic check` —
  «No new upgrade operations»;
- схема создана, 7 таблиц приложения + `alembic_version`;
- `scripts/migrate_sqlite_to_postgres.py` перенёс данные и сверил counts:
  projects 53, calculations 18, price_items 525, price_history 122,
  commercial_offers 2 (совпало с SQLite);
- asyncpg-подключение через `app.db.session` читает проекты/расчёты/прайс.

Docker-путь на этой машине **не работает**: Docker Desktop запускается, но
движок отвечает `500` на `/version` и `/images/json`; причина — WSL без
установленного дистрибутива (`wsl --status`: «Windows Subsystem for Linux has
no installed distributions»). Для проверки использовался portable PostgreSQL,
а не контейнер. После починки Docker/WSL можно повторить раздел 2.

## 1. Что уже готово в коде

- `app/config.py` читает `DATABASE_URL`; SQLAlchemy 2.0 понимает
  `postgresql+asyncpg://…`.
- `app/db/urls.py::sync_database_url` переводит URL для Alembic:
  `postgresql+asyncpg` → `postgresql+psycopg` (psycopg 3).
- `alembic/env.py` использует эту функцию, `render_as_batch` для SQLite
  не мешает PostgreSQL.
- `docker-compose.postgres.yml` — PostgreSQL 16 + приложение, healthcheck,
  отдельный volume.
- Зависимости: `asyncpg`, `psycopg[binary]` в `requirements.txt`.

## 2. Запуск

```powershell
# .env: задать пароль (не хранить в Git)
$env:POSTGRES_PASSWORD = "..."
docker compose -f docker-compose.postgres.yml up -d db

# миграции — с URL для psycopg (sync)
$env:DATABASE_URL = "postgresql+psycopg://furniture:...@127.0.0.1:5432/furniture"
.\.venv\Scripts\alembic.exe upgrade head

# приложение — с asyncpg
$env:DATABASE_URL = "postgresql+asyncpg://furniture:...@127.0.0.1:5432/furniture"
.\.venv\Scripts\python.exe -m app.main
```

## 3. Обязательная проверка

Сделано 2026-11-15:
1. ✅ `alembic upgrade head` на пустом PostgreSQL; `alembic check` — чисто.
2. ✅ Схема и asyncpg-подключение проверены вручную (проекты/расчёты/прайс).
3. ✅ Данные SQLite → PostgreSQL перенесены `scripts/migrate_sqlite_to_postgres.py`;
   counts совпали (53/18/525/122/2).
4. ✅ WAL-слушатель не срабатывает для PostgreSQL (`startswith("sqlite")`),
   подключение прошло без `PRAGMA`-ошибок.

Осталось:
5. ⏳ `pytest` на PostgreSQL-URL (тесты с БД — через временную схему/транзакцию).
6. ⏳ Нагрузка: два параллельных расчёта, импорт прайса, генерация КП.
7. ⏳ Роли/доступ: отдельный пользователь БД без DDL-прав; миграции — отдельной ролью.
8. ⏳ Бэкап PostgreSQL (`pg_dump` + проверка восстановления).

## 4. Что ещё не сделано

- Очередь фоновых задач (сейчас всё в процессе бота).
- Роли приложения (оператор/технолог/администратор) на уровне БД.
- Партиционирование/индексы под большие объёмы.
- Регламент бэкапа PostgreSQL (`pg_dump` + проверка восстановления) —
  аналог `docs/runbook.md`, но для PostgreSQL.

## 5. Откат

Рабочей остаётся SQLite. Переключение `DATABASE_URL` обратимо; данные SQLite не
удаляются. Перед миграцией — `scripts/backup_db.py`. Перенос в PostgreSQL
идемпотентен (`ON CONFLICT DO NOTHING`), повторный запуск не дублирует строки.
