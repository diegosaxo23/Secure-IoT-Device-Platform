from __future__ import annotations

import hashlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .config import get_settings
from .database import init_db
from .mqtt_service import MqttService
from .routers import admin, dashboard, manufacturing, provisioning, simulation


settings = get_settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    mqtt_service = MqttService(settings)
    app.state.mqtt_service = mqtt_service
    mqtt_service.start()
    logger.info("API started")
    try:
        yield
    finally:
        mqtt_service.stop()
        logger.info("API stopped")


app = FastAPI(
    title=settings.app_name,
    version="1.1.0",
    description=(
        "Secure device bootstrapping with challenge/HMAC, CSR enrollment, "
        "X.509 issuance, and MQTT supervision over mTLS."
    ),
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

session_secret = hashlib.sha256(settings.bootstrap_master_key.encode("utf-8")).hexdigest()
app.add_middleware(
    SessionMiddleware,
    secret_key=session_secret,
    same_site="strict",
    https_only=True,
    max_age=3600,
)


@app.middleware("http")
async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "img-src 'self' data: https://fastapi.tiangolo.com; "
        "connect-src 'self'; frame-ancestors 'none'"
    )
    if settings.environment.lower() == "production":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


static_dir = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
app.include_router(provisioning.router)
app.include_router(admin.router)
app.include_router(dashboard.router)
app.include_router(simulation.router)
app.include_router(manufacturing.router)


@app.get("/health", tags=["system"])
def health(request: Request) -> JSONResponse:
    mqtt_service = request.app.state.mqtt_service
    return JSONResponse(
        {
            "status": "ok",
            "mqtt_connected": mqtt_service.connected.is_set(),
            "environment": settings.environment,
        }
    )
