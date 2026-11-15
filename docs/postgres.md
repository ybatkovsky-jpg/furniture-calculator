# Переход на PostgreSQL (S5-04, заготовка)

Документ описывает, как поднять PostgreSQL и перевести приложение. Это **не
выполненная миграция**: на машине разработчика Docker-демон выключен, поэтому
прогон миграций и тестов на PostgreSQL не проводился. Не считать готовым, пока
не выполнены пункты проверки ниже.

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

## 3. Обязательная проверка (без неё S5-04 не закрыт)

1. `alembic upgrade head` на пустой PostgreSQL — без ошибок; `alembic check` —
   «No new upgrade operations».
2. `pytest` на PostgreSQL-URL (тесты, где есть БД, должны использовать
   временную схему/транзакцию).
3. Перенести данные SQLite → PostgreSQL (скрипт/`pgloader`), сверить counts:
   projects, calculations, price_items, price_history.
4. Проверить WAL-специфику: `PRAGMA`-слушатель не должен выполняться для
   PostgreSQL (в `db/session.py` стоит проверка `startswith("sqlite")`).
5. Нагрузка: два параллельных расчёта, импорт прайса, генерация КП.
6. Роли/доступ: отдельный пользователь БД для приложения без DDL-прав;
   миграции — отдельной ролью.

## 4. Что ещё не сделано

- Очередь фоновых задач (сейчас всё в процессе бота).
- Роли приложения (оператор/технолог/администратор) на уровне БД.
- Партиционирование/индексы под большие объёмы.
- Регламент бэкапа PostgreSQL (`pg_dump` + проверка восстановления) —
  аналог `docs/runbook.md`, но для PostgreSQL.

## 5. Откат

Пока PostgreSQL не проверен, рабочей остаётся SQLite. Переключение
`DATABASE_URL` обратимо; данные SQLite не удаляются. Перед миграцией —
`scripts/backup_db.py`.
