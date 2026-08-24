from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from ..auth import require_admin
from ..database import get_db
from ..models import Command, Device, MqttEvent
from ..registry import RegistryError, register_device, reset_bootstrap_secret, revoke_current_certificate
from ..schemas import (
    ApiMessage,
    CommandActivity,
    CommandRequest,
    CommandResponse,
    DeviceCreateRequest,
    DeviceCreateResponse,
    DeviceRuntimeState,
    DeviceSummary,
)


router = APIRouter(
    prefix="/api/v1/admin",
    tags=["administration"],
    dependencies=[Depends(require_admin)],
)


@router.get("/devices", response_model=list[DeviceSummary])
def list_devices(db: Session = Depends(get_db)) -> list[DeviceSummary]:
    devices = db.scalars(select(Device).order_by(Device.device_id)).all()
    return [DeviceSummary.model_validate(device) for device in devices]


@router.get("/devices/{device_id}/state", response_model=DeviceRuntimeState)
def get_device_state(device_id: str, db: Session = Depends(get_db)) -> DeviceRuntimeState:
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="device not found")

    latest_state = None
    if device.application_state_json:
        try:
            latest_state = json.loads(device.application_state_json)
        except json.JSONDecodeError:
            latest_state = {"raw": device.application_state_json}

    return DeviceRuntimeState(
        device_id=device.device_id,
        family=device.family,
        deployment_type=device.deployment_type,
        online=device.online,
        lifecycle_status=device.lifecycle_status,
        last_seen=device.last_seen,
        firmware_version=device.firmware_version,
        latest_state=latest_state,
    )


@router.get("/devices/{device_id}/commands/recent", response_model=list[CommandActivity])
def recent_command_activity(device_id: str, db: Session = Depends(get_db)) -> list[CommandActivity]:
    """Return live TX/RX command activity for the device.

    TX rows come from commands published by the control service. RX rows come from
    actual MQTT response messages, so the UI shows them only after they are received.
    """
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="device not found")

    commands = db.scalars(
        select(Command)
        .where(Command.device_id == device_id)
        .order_by(desc(Command.created_at))
        .limit(30)
    ).all()
    command_names = {command.command_id: command.command_name for command in commands}

    responses = db.scalars(
        select(MqttEvent)
        .where(MqttEvent.device_id == device_id, MqttEvent.kind == "response")
        .order_by(desc(MqttEvent.received_at))
        .limit(30)
    ).all()

    activity: list[CommandActivity] = []
    for command in commands:
        tx_status = "sent" if command.sent_at is not None else command.status
        activity.append(
            CommandActivity(
                direction="TX",
                command_id=command.command_id,
                command_name=command.command_name,
                status=tx_status,
                timestamp=command.sent_at or command.created_at,
                details=command.parameters_json[:2048],
            )
        )

    for event in responses:
        command_id: str | None = None
        command_name = "response"
        response_status = "received"
        try:
            payload = json.loads(event.payload)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            raw_id = payload.get("command_id")
            if isinstance(raw_id, str):
                command_id = raw_id
                command_name = command_names.get(raw_id, command_name)
                if command_name == "response":
                    matched = db.get(Command, raw_id)
                    if matched is not None and matched.device_id == device_id:
                        command_name = matched.command_name
            raw_command = payload.get("command")
            if command_name == "response" and isinstance(raw_command, str) and raw_command:
                command_name = raw_command[:64]
            raw_status = payload.get("status")
            if isinstance(raw_status, str) and raw_status:
                response_status = raw_status[:24]
        activity.append(
            CommandActivity(
                direction="RX",
                command_id=command_id,
                command_name=command_name,
                status=response_status,
                timestamp=event.received_at,
                details=event.payload[:2048],
            )
        )

    activity.sort(key=lambda item: item.timestamp, reverse=True)
    return activity[:50]


@router.post("/devices", response_model=DeviceCreateResponse, status_code=status.HTTP_201_CREATED)
def create_device(payload: DeviceCreateRequest, db: Session = Depends(get_db)) -> DeviceCreateResponse:
    try:
        device, secret = register_device(
            db,
            device_id=payload.device_id,
            family=payload.family,
            display_name=payload.display_name,
            deployment_type=payload.deployment_type,
            allow_reprovisioning=payload.allow_reprovisioning,
        )
    except RegistryError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return DeviceCreateResponse(
        device_id=device.device_id,
        family=device.family,
        deployment_type=device.deployment_type,
        bootstrap_secret=secret,
    )


@router.post("/devices/{device_id}/reset-bootstrap", response_model=DeviceCreateResponse)
def reset_device(device_id: str, request: Request, db: Session = Depends(get_db)) -> DeviceCreateResponse:
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="device not found")
    had_certificate = bool(device.certificate_serial)
    secret = reset_bootstrap_secret(db, device)
    if had_certificate:
        request.app.state.mqtt_service.evict_device(device_id)
    return DeviceCreateResponse(
        device_id=device.device_id,
        family=device.family,
        deployment_type=device.deployment_type,
        bootstrap_secret=secret,
    )


@router.post("/devices/{device_id}/revoke", response_model=ApiMessage)
def revoke_device(device_id: str, request: Request, db: Session = Depends(get_db)) -> ApiMessage:
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="device not found")
    if not device.certificate_serial:
        raise HTTPException(status_code=409, detail="device does not have an operational certificate")
    revoke_current_certificate(db, device)
    request.app.state.mqtt_service.evict_device(device_id)
    return ApiMessage(
        message=(
            "Certificate added to the CRL. The broker will reload the CRL automatically "
            "and the device MQTT session will be disconnected."
        )
    )


@router.post("/devices/{device_id}/commands", response_model=CommandResponse)
def send_command(
    device_id: str,
    payload: CommandRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> CommandResponse:
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="device not found")
    mqtt_service = request.app.state.mqtt_service
    try:
        command, topic, mqtt_payload = mqtt_service.publish_command(
            device_id=device_id,
            command_name=payload.command,
            parameters=payload.parameters,
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return CommandResponse(
        command_id=command.command_id,
        device_id=device_id,
        status=command.status,
        topic=topic,
        payload=mqtt_payload,
    )
