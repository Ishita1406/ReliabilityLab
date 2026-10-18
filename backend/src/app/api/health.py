"""Liveness endpoint."""

from importlib.metadata import version
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.config import Environment, Settings, get_settings

router = APIRouter(tags=["health"])

PACKAGE_NAME = "ai-reliability-lab-backend"


class HealthResponse(BaseModel):
    """Structured health status for the service."""

    status: Literal["ok"]
    service: str
    version: str
    environment: Environment


@router.get("/health", response_model=HealthResponse)
def get_health(settings: Annotated[Settings, Depends(get_settings)]) -> HealthResponse:
    """Report that the process is up and able to serve requests."""
    return HealthResponse(
        status="ok",
        service=settings.service_name,
        version=version(PACKAGE_NAME),
        environment=settings.environment,
    )
