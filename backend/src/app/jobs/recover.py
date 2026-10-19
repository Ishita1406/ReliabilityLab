"""Re-publish jobs whose queue messages may be lost.

    APP_DATABASE_URL=... APP_REDIS_URL=... uv run python -m app.jobs.recover

Also runs when a worker starts. Candidates come from PostgreSQL (``app.db.jobs.recoverable``):
experiments queued longer than ``job_lease_seconds`` and running ones whose lease expired.
Publishing a duplicate message is harmless: only one executor can hold the lease, and an
attempt that finds nothing to do acknowledges its message and exits.
"""

import logging

from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.db import jobs
from app.db.session import create_db_engine, database_url, session_factory
from app.domain.timestamps import utc_now
from app.jobs.submission import Publisher, publish
from app.logs import configure_logging, log_event

logger = logging.getLogger("app.jobs.recover")


def recover(
    sessions: sessionmaker[Session], settings: Settings, publisher: Publisher = publish
) -> list[str]:
    with sessions() as session:
        candidates = jobs.recoverable(session, utc_now(), settings.job_lease_seconds)
    for experiment_id in candidates:
        task_id = publisher(experiment_id, settings)
        log_event(logger, "job.republished", experiment_id=experiment_id, task_id=task_id)
    return candidates


def main() -> int:
    configure_logging()
    settings = get_settings()
    engine = create_db_engine(database_url(settings))
    try:
        recovered = recover(session_factory(engine), settings)
    finally:
        engine.dispose()
    print(f"re-published {len(recovered)} job(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
