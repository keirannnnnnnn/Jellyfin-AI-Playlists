import os
import sys
import logging
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import uvicorn

from app.config import (
    BASE_DIR,
    DATA_DIR,
    DEFAULT_PORT,
    get_bind_host,
)
from app.database import init_db
from app.scheduler import start_scheduler, stop_scheduler
from app.web.routes import router as web_router
from app.web.api_routes import router as api_router

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("jellyfin_playlists.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Initialize SQLite Database & default seeds
    logger.info("Initializing SQLite database...")
    init_db()

    # 2. Start in-process scheduler
    logger.info("Starting background scheduler...")
    start_scheduler()

    yield

    # 3. Shutdown scheduler
    logger.info("Shutting down background scheduler...")
    stop_scheduler()


app = FastAPI(
    title="Jellyfin Smart Playlist Generator",
    description="Automated smart playlist generator tailored per user for Jellyfin",
    version="1.0.0",
    lifespan=lifespan,
)

# Static file mounts
static_dir = BASE_DIR / "app" / "web" / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# Mount data directory for uploaded icons
if DATA_DIR.exists():
    app.mount("/data", StaticFiles(directory=str(DATA_DIR)), name="data")

# Register routes
app.include_router(web_router)
app.include_router(api_router)


def main():
    """CLI / container entry point."""
    host = get_bind_host()
    port = DEFAULT_PORT

    logger.info("=" * 60)
    logger.info(" Starting Jellyfin Smart Playlist Generator")
    logger.info(f" Binding to Interface: {host}:{port}")
    logger.info(f" Data Directory: {DATA_DIR}")
    logger.info("=" * 60)

    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
