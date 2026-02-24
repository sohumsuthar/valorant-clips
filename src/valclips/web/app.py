"""FastAPI app factory."""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from ..config import STATIC_DIR, THUMBNAIL_DIR
from ..db import init_db
from .routes import router


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    init_db()

    app = FastAPI(title="Valorant Clip Manager", version="0.1.0")

    app.include_router(router)

    # Serve static assets
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # Serve thumbnails
    THUMBNAIL_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/thumbnails", StaticFiles(directory=str(THUMBNAIL_DIR)), name="thumbnails")

    return app
