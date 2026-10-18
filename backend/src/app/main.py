"""FastAPI application entry point."""

from fastapi import FastAPI

from app.api.health import router as health_router
from app.config import get_settings


def create_app() -> FastAPI:
    """Build the FastAPI application with all routers registered."""
    settings = get_settings()
    app = FastAPI(title=settings.service_name)
    app.include_router(health_router)
    return app


app = create_app()
