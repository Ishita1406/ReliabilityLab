"""FastAPI application entry point."""

from fastapi import FastAPI

from app.api.dashboard import router as dashboard_router
from app.api.deps import register_error_handlers
from app.api.health import router as health_router
from app.api.jobs import router as jobs_router
from app.api.reviews import router as reviews_router
from app.config import get_settings
from app.logs import configure_logging


def create_app() -> FastAPI:
    """Build the FastAPI application with all routers registered."""
    settings = get_settings()
    configure_logging()
    app = FastAPI(title=settings.service_name)
    app.include_router(health_router)
    app.include_router(dashboard_router)
    app.include_router(reviews_router)
    app.include_router(jobs_router)
    register_error_handlers(app)
    return app


app = create_app()
