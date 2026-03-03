"""FastAPI application for the NYC Apartment Tracker iOS backend."""

from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Add project root to path so we can import models.py, db.py, etc.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown: connect to MongoDB, ensure indexes."""
    import db as db_module

    log.info("Connecting to MongoDB...")
    db_module.get_db()
    db_module.ensure_indexes()

    # Ensure device_preferences indexes
    db = db_module.get_db()
    db.device_preferences.create_index("device_id", unique=True)

    log.info("API ready")
    yield

    log.info("Shutting down...")
    db_module.close()


app = FastAPI(
    title="NYC Apartment Tracker API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

from api.routes import router
app.include_router(router)


@app.get("/health")
def health_check():
    return {"status": "ok"}
