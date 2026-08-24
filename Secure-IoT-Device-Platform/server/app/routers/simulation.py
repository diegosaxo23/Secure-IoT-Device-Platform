from __future__ import annotations

import json
import secrets
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import require_admin
from ..config import get_settings
from ..database import get_db
from ..models import Device


BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
router = APIRouter(dependencies=[Depends(require_admin)])


class SimulationStartRequest(BaseModel):
    family: str = Field(pattern=r"^(cromaled|area_lz7|as7341)$")
    count: int = Field(default=1, ge=1, le=200)
    interval: float = Field(default=5.0, ge=0.5, le=300.0)


def _csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not isinstance(token, str):
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


def _check_csrf(request: Request, submitted: str | None) -> None:
    expected = request.session.get("csrf_token")
    if not isinstance(expected, str) or not submitted or not secrets.compare_digest(expected, submitted):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")


def _manager_request(path: str, *, method: str = "GET", body: dict[str, Any] | None = None) -> Any:
    settings = get_settings()
    url = settings.simulator_manager_url.rstrip("/") + path
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            payload = response.read()
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(raw).get("detail", raw)
        except json.JSONDecodeError:
            detail = raw
        raise RuntimeError(str(detail)) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError("Simulation Manager is unavailable. Start the Docker platform.") from exc
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Simulation Manager returned an invalid response") from exc


@router.get("/simulation", response_class=HTMLResponse)
def simulation_lab(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    devices = db.scalars(select(Device)).all()
    simulated = [device for device in devices if device.deployment_type == "simulated"]
    counts = {
        "total": len(simulated),
        "online": sum(1 for device in simulated if device.online),
        "cromaled": sum(1 for device in simulated if "cromaled" in device.family.lower()),
        "area_lz7": sum(1 for device in simulated if "area" in device.family.lower()),
        "as7341": sum(1 for device in simulated if "as7341" in device.family.lower()),
    }
    try:
        manager = _manager_request("/status")
    except RuntimeError:
        manager = {"available": False, "enabled": False, "running": 0, "instances": []}
    return templates.TemplateResponse(
        request=request,
        name="simulation.html",
        context={"counts": counts, "manager": manager, "csrf_token": _csrf_token(request)},
    )


@router.get("/api/v1/admin/simulation/status")
def simulation_status() -> JSONResponse:
    try:
        return JSONResponse(_manager_request("/status"))
    except RuntimeError as exc:
        return JSONResponse(
            {"available": False, "enabled": False, "running": 0, "instances": [], "detail": str(exc)},
            status_code=503,
        )


@router.post("/api/v1/admin/simulation/enable")
def simulation_enable(request: Request, x_csrf_token: str | None = Header(default=None)) -> JSONResponse:
    _check_csrf(request, x_csrf_token)
    try:
        result = _manager_request("/control/enable", method="POST", body={})
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return JSONResponse(result)


@router.post("/api/v1/admin/simulation/disable")
def simulation_disable(request: Request, x_csrf_token: str | None = Header(default=None)) -> JSONResponse:
    _check_csrf(request, x_csrf_token)
    try:
        result = _manager_request("/control/disable", method="POST", body={})
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return JSONResponse(result)


@router.post("/api/v1/admin/simulation/start")
def simulation_start(
    payload: SimulationStartRequest,
    request: Request,
    x_csrf_token: str | None = Header(default=None),
) -> JSONResponse:
    _check_csrf(request, x_csrf_token)
    try:
        result = _manager_request(
            "/fleets/start",
            method="POST",
            body=payload.model_dump(),
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return JSONResponse(result)


@router.post("/api/v1/admin/simulation/stop-all")
def simulation_stop_all(request: Request, x_csrf_token: str | None = Header(default=None)) -> JSONResponse:
    _check_csrf(request, x_csrf_token)
    try:
        result = _manager_request("/fleets/stop-all", method="POST", body={})
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return JSONResponse(result)
