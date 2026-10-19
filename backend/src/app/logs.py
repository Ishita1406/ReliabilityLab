"""Structured (JSON-lines) logging.

    log_event(logger, "run.completed", experiment_id=..., run_id=..., status="completed")
    log_at(logger, logging.WARNING, "job.retry", experiment_id=..., error_type="...")

Each record is one JSON object with ``ts``, ``level``, ``logger``, ``event`` and the given
fields. Field values are restricted to scalars; strings pass through the trace redaction policy
(``app.tracing.policy.sanitize``) and are cut to 200 characters, and values under
sensitive-looking keys are dropped. Callers log identifiers, statuses, counts, and error types,
never tool payloads, answers, or connection strings.
"""

import json
import logging
import sys
from datetime import UTC, datetime

from app.tracing.policy import TracePayloadPolicy, is_sensitive_key, sanitize

MAX_FIELD_CHARS = 200
_POLICY = TracePayloadPolicy(max_string_chars=MAX_FIELD_CHARS, max_payload_bytes=1024)
Scalar = str | int | float | bool | None


def clean_fields(fields: dict[str, object]) -> dict[str, Scalar]:
    cleaned: dict[str, Scalar] = {}
    for key, value in fields.items():
        if is_sensitive_key(key):
            cleaned[key] = "[REDACTED]"
        elif value is None or isinstance(value, bool | int | float):
            cleaned[key] = value
        else:
            text = sanitize(str(value), _POLICY).value
            cleaned[key] = text if isinstance(text, str) else str(text)
    return cleaned


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "event": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            payload.update(fields)
        if record.exc_info and record.exc_info[0] is not None:
            payload["exception_type"] = record.exc_info[0].__name__
        return json.dumps(payload, default=str)


def log_event(logger: logging.Logger, event: str, /, **fields: object) -> None:
    """Log ``event`` at INFO. Logger and event are positional-only so any field name is safe."""
    log_at(logger, logging.INFO, event, **fields)


def log_at(logger: logging.Logger, level: int, event: str, /, **fields: object) -> None:
    logger.log(level, event, extra={"fields": clean_fields(fields)})


def configure_logging(level: str = "INFO") -> None:
    """Send JSON lines to stderr from the ``app`` logger tree (idempotent)."""
    logger = logging.getLogger("app")
    logger.setLevel(level)
    if not any(isinstance(h.formatter, JsonFormatter) for h in logger.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
    logger.propagate = False
