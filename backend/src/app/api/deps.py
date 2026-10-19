"""Shared API dependencies and error mapping.

The API has no authentication or authorization (Milestone 9 scope): every endpoint, including
review submission, is open to anyone who can reach the server. Do not expose it publicly.
"""

from functools import lru_cache
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.db.benchmarks import NotFoundError
from app.db.human_reviews import ReviewConsistencyError
from app.db.session import (
    DatabaseNotConfiguredError,
    create_db_engine,
    database_url,
    session_factory,
)
from app.experiments.comparison import ComparisonError
from app.review.models import ReviewHistoryError
from app.review.service import ReviewWorkflowError


class ApiModel(BaseModel):
    """Base for API request/response models (see ``DomainModel`` for the schema setting)."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)


@lru_cache
def _engine() -> Engine:
    return create_db_engine(database_url())


def get_sessions() -> sessionmaker[Session]:
    return session_factory(_engine())


Sessions = Annotated[sessionmaker[Session], Depends(get_sessions)]


def _error(status: int, kind: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": kind, "detail": message})


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(NotFoundError)
    async def not_found(_: Request, exc: NotFoundError) -> JSONResponse:
        return _error(404, "not_found", str(exc))

    @app.exception_handler(DatabaseNotConfiguredError)
    async def no_database(_: Request, exc: DatabaseNotConfiguredError) -> JSONResponse:
        return _error(503, "database_not_configured", str(exc))

    @app.exception_handler(ComparisonError)
    async def not_comparable(_: Request, exc: ComparisonError) -> JSONResponse:
        return _error(409, "not_comparable", str(exc))

    @app.exception_handler(ReviewWorkflowError)
    @app.exception_handler(ReviewConsistencyError)
    @app.exception_handler(ReviewHistoryError)
    async def review_conflict(_: Request, exc: ValueError) -> JSONResponse:
        return _error(409, "review_conflict", str(exc))

    @app.exception_handler(ValidationError)
    async def invalid_domain_object(_: Request, exc: ValidationError) -> JSONResponse:
        # Domain-level rules (e.g. an 'ambiguous' review needs a rationale) checked after the
        # request body itself parsed; reported like request validation errors.
        messages = "; ".join(str(e["msg"]) for e in exc.errors()[:5])
        return _error(422, "invalid", messages)

    @app.exception_handler(IntegrityError)
    async def integrity(_: Request, exc: IntegrityError) -> JSONResponse:
        # The constraint name is useful; the SQL and parameters are not returned.
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        return _error(409, "constraint_violation", f"violates {constraint or 'a constraint'}")
