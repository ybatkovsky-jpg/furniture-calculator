"""Проверка структуры БД."""

import sqlite3

# Подключиться к БД и посмотреть таблицы
conn = sqlite3.connect("data/furniture.db")
cursor = conn.cursor()

# Получить таблицы
cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
tables = cursor.fetchall()

print("✓ Таблицы в БД:")
for table in tables:
    table_name = table[0]
    cursor.execute(f"PRAGMA table_info({table_name});")
    columns = cursor.fetchall()
    print(f"\n  📋 {table_name} ({len(columns)} полей):")
    for col in columns:
        col_name = col[1]
        col_type = col[2]
        print(f"     - {col_name}: {col_type}")

print(f"\n✓ Всего таблиц: {len(tables)}")
conn.close()
