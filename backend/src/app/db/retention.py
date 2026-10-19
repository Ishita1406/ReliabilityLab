"""Apply the trace retention policy to the configured database.

    APP_DATABASE_URL=... uv run python -m app.db.retention

Purges trace events of runs that finished more than ``APP_TRACE_RETENTION_DAYS`` days ago, in
one transaction. Run rows are kept; traces referenced by human reviews or failure evidence are
retained.
"""

from app.config import get_settings
from app.db.runs import purge_expired_traces
from app.db.session import create_db_engine, database_url, session_factory
from app.domain.timestamps import utc_now


def main() -> int:
    settings = get_settings()
    engine = create_db_engine(database_url(settings))
    try:
        with session_factory(engine).begin() as session:
            purged = purge_expired_traces(
                session, retention_days=settings.trace_retention_days, now=utc_now()
            )
    finally:
        engine.dispose()
    print(f"purged traces of {purged} run(s) older than {settings.trace_retention_days} days")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
