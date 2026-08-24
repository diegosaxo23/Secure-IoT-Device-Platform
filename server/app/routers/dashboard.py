from __future__ import annotations

import json
import secrets
import urllib.parse
from collections import Counter
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from ..auth import require_admin
from ..database import get_db
from ..device_profiles import family_slug, profile_for
from ..models import Command, Device, MqttEvent
from ..project_reset import ProjectResetError, reset_project, verify_dashboard_password
from ..registry import RegistryError, register_device, revoke_current_certificate
from ..time_utils import isoformat_utc


BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.filters["iso_utc"] = isoformat_utc
templates.env.filters["family_slug"] = family_slug

router = APIRouter(dependencies=[Depends(require_admin)])


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


@router.get("/", response_class=HTMLResponse)
def dashboard_home(
    request: Request,
    q: str = Query(default="", max_length=80),
    family: str = Query(default="all", max_length=64),
    deployment: str = Query(default="all", pattern=r"^(all|physical|simulated)$"),
    connection: str = Query(default="all", pattern=r"^(all|online|offline)$"),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    all_devices = db.scalars(select(Device).order_by(Device.device_id)).all()
    q_norm = q.strip().lower()
    filtered: list[Device] = []
    for device in all_devices:
        if q_norm:
            searchable = " ".join(
                value for value in (device.device_id, device.display_name or "", device.family) if value
            ).lower()
            if q_norm not in searchable:
                continue
        if family != "all" and device.family != family:
            continue
        if deployment != "all" and device.deployment_type != deployment:
            continue
        if connection == "online" and not device.online:
            continue
        if connection == "offline" and device.online:
            continue
        filtered.append(device)

    family_counter = Counter(device.family for device in all_devices)
    family_online = Counter(device.family for device in all_devices if device.online)
    family_rows = [
        {
            "name": name,
            "slug": family_slug(name),
            "total": total,
            "online": family_online.get(name, 0),
            "offline": total - family_online.get(name, 0),
        }
        for name, total in sorted(family_counter.items(), key=lambda item: item[0].lower())
    ]
    counts = {
        "total": len(all_devices),
        "online": sum(1 for device in all_devices if device.online),
        "physical": sum(1 for device in all_devices if device.deployment_type == "physical"),
        "simulated": sum(1 for device in all_devices if device.deployment_type == "simulated"),
        "pending": sum(1 for device in all_devices if device.lifecycle_status == "pending"),
        "revoked": sum(1 for device in all_devices if device.lifecycle_status == "revoked"),
    }
    counts["alerts"] = counts["revoked"] + sum(
        1 for device in all_devices if device.lifecycle_status == "provisioned" and not device.online
    )
    families = sorted({device.family for device in all_devices}, key=str.lower)
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "devices": filtered,
            "counts": counts,
            "families": families,
            "family_rows": family_rows,
            "filters": {
                "q": q,
                "family": family,
                "deployment": deployment,
                "connection": connection,
            },
            "csrf_token": _csrf_token(request),
            "reset_done": request.query_params.get("reset") == "done",
            "reset_error": request.query_params.get("reset_error", ""),
        },
    )


@router.post("/project/reset")
def project_reset(
    request: Request,
    csrf_token: str = Form(...),
    dashboard_password: str = Form(...),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    _check_csrf(request, csrf_token)
    if not verify_dashboard_password(dashboard_password):
        return RedirectResponse(url="/?reset_error=Invalid+dashboard+password", status_code=303)

    device_ids = [device.device_id for device in db.scalars(select(Device)).all()]
    try:
        reset_project(db)
    except ProjectResetError as exc:
        return RedirectResponse(
            url="/?reset_error=" + urllib.parse.quote(str(exc), safe=""),
            status_code=303,
        )

    # Disconnect pre-reset MQTT sessions after their certificate serials were added to the CRL.
    for device_id in device_ids:
        request.app.state.mqtt_service.evict_device(device_id)
    request.session["csrf_token"] = secrets.token_urlsafe(32)
    return RedirectResponse(url="/?reset=done", status_code=303)


@router.get("/devices/{device_id}", response_class=HTMLResponse)
def device_detail(device_id: str, request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="device not found")
    events = db.scalars(
        select(MqttEvent)
        .where(MqttEvent.device_id == device_id)
        .order_by(desc(MqttEvent.received_at))
        .limit(50)
    ).all()
    commands = db.scalars(
        select(Command)
        .where(Command.device_id == device_id)
        .order_by(desc(Command.created_at))
        .limit(25)
    ).all()

    latest_state = None
    if device.application_state_json:
        try:
            latest_state = json.loads(device.application_state_json)
        except json.JSONDecodeError:
            latest_state = device.application_state_json

    profile = profile_for(device.family)
    fleet_ids = list(db.scalars(select(Device.device_id).order_by(Device.device_id)).all())
    fleet_index = fleet_ids.index(device_id)
    previous_device_id = fleet_ids[(fleet_index - 1) % len(fleet_ids)] if len(fleet_ids) > 1 else None
    next_device_id = fleet_ids[(fleet_index + 1) % len(fleet_ids)] if len(fleet_ids) > 1 else None
    return templates.TemplateResponse(
        request=request,
        name="device.html",
        context={
            "device": device,
            "events": events,
            "commands": commands,
            "latest_state": latest_state,
            "latest_state_json": json.dumps(latest_state, indent=2, ensure_ascii=False),
            "profile": profile,
            "csrf_token": _csrf_token(request),
            "revoked": request.query_params.get("revoked") == "1",
            "revoke_error": request.query_params.get("revoke_error", ""),
            "previous_device_id": previous_device_id,
            "next_device_id": next_device_id,
            "fleet_position": fleet_index + 1,
            "fleet_total": len(fleet_ids),
        },
    )


@router.get("/register", response_class=HTMLResponse)
def register_form(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="register.html",
        context={"csrf_token": _csrf_token(request), "result": None, "error": None},
    )


@router.post("/register", response_class=HTMLResponse)
def register_form_submit(
    request: Request,
    device_id: str = Form(...),
    family: str = Form("generic"),
    display_name: str = Form(""),
    deployment_type: str = Form("physical"),
    allow_reprovisioning: bool = Form(False),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    _check_csrf(request, csrf_token)
    try:
        device, secret = register_device(
            db,
            device_id=device_id,
            family=family,
            display_name=display_name or None,
            deployment_type=deployment_type,
            allow_reprovisioning=allow_reprovisioning,
        )
        result = {"device": device, "secret": secret}
        error = None
    except (RegistryError, ValueError) as exc:
        result = None
        error = str(exc)

    return templates.TemplateResponse(
        request=request,
        name="register.html",
        context={
            "csrf_token": _csrf_token(request),
            "result": result,
            "error": error,
        },
        status_code=201 if result else 409,
    )


@router.post("/devices/{device_id}/revoke")
def revoke_from_dashboard(
    device_id: str,
    request: Request,
    csrf_token: str = Form(...),
    dashboard_password: str = Form(...),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    _check_csrf(request, csrf_token)
    if not verify_dashboard_password(dashboard_password):
        return RedirectResponse(
            url=f"/devices/{urllib.parse.quote(device_id, safe='')}?revoke_error=Invalid+dashboard+password",
            status_code=303,
        )
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="device not found")
    if not device.certificate_serial:
        raise HTTPException(status_code=409, detail="device does not have an operational certificate")
    revoke_current_certificate(db, device)
    request.app.state.mqtt_service.evict_device(device_id)
    return RedirectResponse(url=f"/devices/{device_id}?revoked=1", status_code=303)
