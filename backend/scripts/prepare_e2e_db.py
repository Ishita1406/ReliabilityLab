"""(Re)create the end-to-end test database and seed it with demo data.

    uv run python scripts/prepare_e2e_db.py [DATABASE_URL] [--redis-url REDIS_URL]

DESTRUCTIVE for the named database only: it is dropped and recreated. As a guard, the database
name must end with ``_e2e``. The URL defaults to the local compose server with database
``reliabilitylab_e2e``. With ``--redis-url``, that Redis database is flushed too (refused for
database 0). Prints the seeded experiment IDs as JSON.
"""

import json
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.db.session import session_factory
from app.seed_demo import seed

BACKEND = Path(__file__).resolve().parents[1]
DEFAULT_URL = (
    "postgresql+psycopg://reliabilitylab:reliabilitylab_local_dev@127.0.0.1:5433/reliabilitylab_e2e"
)


def flush_redis(redis_url: str) -> None:
    import redis

    client = redis.Redis.from_url(redis_url)
    try:
        if int(client.connection_pool.connection_kwargs.get("db", 0)) == 0:
            raise SystemExit("refusing to flush Redis database 0; use a dedicated e2e database")
        client.flushdb()
    finally:
        client.close()


def main(argv: list[str]) -> int:
    redis_url: str | None = None
    if "--redis-url" in argv:
        index = argv.index("--redis-url")
        redis_url = argv[index + 1]
        argv = argv[:index] + argv[index + 2 :]
    url = make_url(argv[0] if argv else DEFAULT_URL)
    name = url.database or ""
    if not name.endswith("_e2e"):
        print(
            f"refusing to recreate database '{name}': the name must end with _e2e", file=sys.stderr
        )
        return 2
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            connection.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        admin.dispose()
    if redis_url:
        flush_redis(redis_url)
    rendered = url.render_as_string(hide_password=False)
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    config.set_main_option("sqlalchemy.url", rendered.replace("%", "%%"))
    config.attributes["configure_logger"] = False
    command.upgrade(config, "head")
    engine = create_engine(rendered)
    try:
        created = seed(session_factory(engine))
    finally:
        engine.dispose()
    print(json.dumps(created))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
