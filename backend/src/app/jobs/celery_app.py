"""Celery application. Redis is the broker only: no result backend, nothing durable.

Start a worker (from backend/):

    uv run celery -A app.jobs.celery_app worker --loglevel=INFO

Delivery semantics: ``acks_late`` + ``reject_on_worker_lost`` mean a message is acknowledged
only after the task returns, so a crashed worker's message is redelivered (after the Redis
``visibility_timeout``). Redelivery is safe: the PostgreSQL lease admits one executor at a time
and stored runs are skipped. ``worker_prefetch_multiplier=1`` keeps a worker from reserving
messages it cannot start soon.
"""

from celery import Celery

from app.config import Settings, get_settings

QUEUE = "experiments"


def configure(app: Celery, settings: Settings) -> None:
    soft = settings.job_soft_time_limit_seconds
    app.conf.update(
        broker_url=settings.redis_url.get_secret_value(),
        result_backend=None,
        task_ignore_result=True,
        task_default_queue=QUEUE,
        task_serializer="json",
        accept_content=["json"],
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        worker_concurrency=settings.worker_concurrency,
        worker_hijack_root_logger=False,
        # Our JSON lines go to stderr as-is; Celery's redirection would wrap each one in its
        # own "[time: WARNING/ForkPoolWorker-1]" prefix and re-label it as a warning.
        worker_redirect_stdouts=False,
        task_soft_time_limit=soft,
        task_time_limit=soft + 60,
        broker_connection_retry_on_startup=True,
        broker_connection_timeout=settings.enqueue_timeout_seconds,
        broker_transport_options={
            "visibility_timeout": settings.broker_visibility_timeout_seconds,
            "socket_connect_timeout": settings.enqueue_timeout_seconds,
            "socket_timeout": max(settings.enqueue_timeout_seconds, 5.0),
        },
    )


celery_app = Celery("reliabilitylab", include=["app.jobs.tasks"])
configure(celery_app, get_settings())
