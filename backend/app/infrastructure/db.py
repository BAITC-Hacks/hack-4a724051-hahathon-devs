"""Подключение к PostgreSQL и применение миграций.

Миграции это обычные SQL-файлы в backend/migrations, применяются по порядку имени
и записываются в schema_migrations. Каждый файл выполняется в своей транзакции.
"""

from pathlib import Path

import psycopg
from psycopg_pool import ConnectionPool

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


def create_pool(database_url: str, min_size: int = 1, max_size: int = 5) -> ConnectionPool:
    pool = ConnectionPool(database_url, min_size=min_size, max_size=max_size, open=False,
                          kwargs={"autocommit": False})
    try:
        pool.open(wait=True, timeout=5)
    except BaseException:
        pool.close()
        raise
    return pool


def migrate(database_url: str, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    """Применяет новые миграции, возвращает имена применённых файлов."""
    applied_now = []
    with psycopg.connect(database_url) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations "
                     "(name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
        # Две параллельные миграции не должны столкнуться.
        conn.execute("SELECT pg_advisory_xact_lock(4715001)")
        done = {row[0] for row in conn.execute("SELECT name FROM schema_migrations")}
        conn.commit()
        for path in sorted(migrations_dir.glob("*.sql")):
            if path.name in done:
                continue
            with conn.transaction():
                conn.execute("SELECT pg_advisory_xact_lock(4715001)")
                if conn.execute("SELECT 1 FROM schema_migrations WHERE name = %s", (path.name,)).fetchone():
                    continue
                conn.execute(path.read_text(encoding="utf-8"))
                conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (path.name,))
            applied_now.append(path.name)
    return applied_now
