from __future__ import annotations

import json
import secrets
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..auth import require_admin
from ..config import get_settings

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
router = APIRouter(dependencies=[Depends(require_admin)])
ALLOWED_PROFILES = {
    "cromaled": {"label": "CromaLED", "source_project": "firmware/esp32/CromaLED_Gateway"},
    "area_lz7": {"label": "AREA LZ7", "source_project": "firmware/esp32/AREA_LZ7_Gateway"},
    "as7341": {"label": "AS7341", "source_project": "firmware/esp32/AS7341_Gateway"},
}


def _csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not isinstance(token, str):
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


def _check_csrf(request: Request, submitted: str) -> None:
    expected = request.session.get("csrf_token")
    if not isinstance(expected, str) or not secrets.compare_digest(expected, submitted):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")


def _json_request(
    url: str,
    token: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    timeout: float = 10.0,
    component_name: str,
) -> dict[str, Any]:
    data = None
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
    if body is not None:
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(raw).get("detail", raw)
        except json.JSONDecodeError:
            detail = raw
        raise RuntimeError(f"{component_name} rejected the request: {detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"{component_name} is not reachable") from exc
    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{component_name} returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{component_name} returned an unexpected response")
    return payload


def _agent_request(
    path: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    if not settings.manufacturing_enabled:
        raise RuntimeError("Manufacturing integration is not configured")
    if not settings.manufacturing_agent_token:
        raise RuntimeError("Manufacturing Agent token is not configured")
    return _json_request(
        settings.manufacturing_agent_url.rstrip("/") + path,
        settings.manufacturing_agent_token,
        method=method,
        body=body,
        timeout=timeout or min(settings.manufacturing_agent_timeout_seconds, 15),
        component_name="Manufacturing Agent",
    )


def _station_state() -> dict[str, Any]:
    settings = get_settings()
    state: dict[str, Any] = {
        "configured": settings.manufacturing_enabled,
        "available": False,
        "busy": False,
        "ports": [],
        "station_error": None,
        "job": None,
    }
    if not settings.manufacturing_enabled:
        state["station_error"] = "Manufacturing integration is not configured."
        return state

    try:
        health = _agent_request("/health", timeout=2.5)
        state["available"] = True
        state["busy"] = bool(health.get("busy", False))

        ports_payload = _agent_request("/ports", timeout=2.5)
        ports = ports_payload.get("ports", [])
        if not isinstance(ports, list):
            ports = []
        state["ports"] = [
            {"device": str(item.get("device", "")), "description": str(item.get("description", ""))}
            for item in ports
            if isinstance(item, dict) and item.get("device")
        ]

        job_payload = _agent_request("/job", timeout=2.5)
        job = job_payload.get("job")
        state["job"] = job if isinstance(job, dict) else None
    except RuntimeError as exc:
        state["station_error"] = str(exc)
    return state


def _render(request: Request, *, error: str | None = None, status_code: int = 200) -> HTMLResponse:
    station = _station_state()
    return templates.TemplateResponse(
        request=request,
        name="manufacturing.html",
        context={
            **station,
            "profiles": ALLOWED_PROFILES,
            "csrf_token": _csrf_token(request),
            "error": error or station["station_error"],
        },
        status_code=status_code,
    )


def _start_program(
    *,
    profile: str,
    port: str,
    display_name: str,
    reset_existing: bool,
) -> dict[str, Any]:
    if profile not in ALLOWED_PROFILES:
        raise RuntimeError("Invalid device profile")
    if not port or len(port) > 128:
        raise RuntimeError("Invalid serial port")
    return _agent_request(
        "/program",
        method="POST",
        body={
            "profile": profile,
            "port": port,
            "display_name": display_name.strip() or None,
            "reset_existing": reset_existing,
        },
        timeout=10.0,
    )


@router.get("/manufacturing", response_class=HTMLResponse)
def manufacturing_page(request: Request) -> HTMLResponse:
    return _render(request)


@router.get("/api/v1/admin/manufacturing/status")
def manufacturing_status() -> JSONResponse:
    station = _station_state()
    return JSONResponse(station, status_code=200 if station["available"] else 503)


@router.post("/api/v1/admin/manufacturing/program")
def program_device_api(
    request: Request,
    csrf_token: str = Form(...),
    profile: str = Form(...),
    port: str = Form(...),
    display_name: str = Form(default=""),
    reset_existing: bool = Form(default=False),
) -> JSONResponse:
    _check_csrf(request, csrf_token)
    try:
        result = _start_program(
            profile=profile,
            port=port,
            display_name=display_name,
            reset_existing=reset_existing,
        )
    except RuntimeError as exc:
        return JSONResponse({"ok": False, "detail": str(exc)}, status_code=409)
    return JSONResponse(result, status_code=202)


@router.post("/manufacturing/program", response_class=HTMLResponse)
def program_device_fallback(
    request: Request,
    csrf_token: str = Form(...),
    profile: str = Form(...),
    port: str = Form(...),
    display_name: str = Form(default=""),
    reset_existing: bool = Form(default=False),
) -> HTMLResponse:
    _check_csrf(request, csrf_token)
    try:
        _start_program(
            profile=profile,
            port=port,
            display_name=display_name,
            reset_existing=reset_existing,
        )
    except RuntimeError as exc:
        return _render(request, error=str(exc), status_code=409)
    return RedirectResponse(url="/manufacturing", status_code=303)
