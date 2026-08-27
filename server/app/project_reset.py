from __future__ import annotations

import json
import logging
import secrets
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .config import get_settings
from .models import BootstrapSession, Command, Device, MqttEvent, RevokedCertificate
from .registry import rebuild_crl_from_db
from .time_utils import utcnow


logger = logging.getLogger(__name__)


class ProjectResetError(RuntimeError):
    pass


class OptionalServiceUnavailable(ProjectResetError):
    pass


def verify_dashboard_password(password: str) -> bool:
    expected = get_settings().dashboard_password
    return secrets.compare_digest(password.encode("utf-8"), expected.encode("utf-8"))


def _json_request(
    base_url: str,
    path: str,
    *,
    method: str = "POST",
    headers: dict[str, str] | None = None,
    timeout: float = 8.0,
) -> dict[str, Any]:
    request_headers = {"Accept": "application/json"}
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=b"{}" if method == "POST" else None,
        headers={**request_headers, "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ProjectResetError(detail or f"HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise OptionalServiceUnavailable(str(exc)) from exc
    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        raise ProjectResetError("Service returned invalid JSON") from exc
    return payload if isinstance(payload, dict) else {}


def stop_and_purge_simulation() -> dict[str, Any]:
    settings = get_settings()
    return _json_request(settings.simulator_manager_url, "/control/reset")


def verify_manufacturing_idle() -> dict[str, Any]:
    """Keep Manufacturing enabled while ensuring a reset cannot race a programming job."""
    settings = get_settings()
    if not settings.manufacturing_enabled or not settings.manufacturing_agent_token:
        return {"available": False, "enabled": settings.manufacturing_enabled}
    headers = {"Authorization": f"Bearer {settings.manufacturing_agent_token}"}
    try:
        health = _json_request(
            settings.manufacturing_agent_url,
            "/health",
            method="GET",
            headers=headers,
        )
    except OptionalServiceUnavailable as exc:
        logger.warning("Manufacturing Agent is offline during reset: %s", exc)
        return {"available": False, "enabled": True, "warning": str(exc)}

    if bool(health.get("busy", False)):
        raise ProjectResetError(
            "A manufacturing operation is currently in progress. Wait for it to finish before resetting project data."
        )
    return {"available": True, "enabled": True, "busy": False}


def _revoke_current_device_certificates(db: Session) -> int:
    """Keep certificate serial tombstones so pre-reset device credentials remain blocked."""
    devices = db.scalars(select(Device)).all()
    revoked = 0
    for device in devices:
        if device.certificate_serial and db.get(RevokedCertificate, device.certificate_serial) is None:
            db.add(
                RevokedCertificate(
                    serial_hex=device.certificate_serial,
                    device_id=device.device_id,
                    revoked_at=utcnow(),
                    reason="cessation_of_operation",
                )
            )
            revoked += 1
    db.flush()
    return revoked


def clear_runtime_database(db: Session) -> dict[str, int]:
    """Delete user/runtime records while retaining revocation tombstones for old certificates."""
    device_count = len(db.scalars(select(Device.device_id)).all())
    event_count = len(db.scalars(select(MqttEvent.id)).all())
    command_count = len(db.scalars(select(Command.command_id)).all())
    session_count = len(db.scalars(select(BootstrapSession.session_id)).all())
    newly_revoked = _revoke_current_device_certificates(db)

    # Foreign-key children are deleted explicitly for portability, then devices.
    db.execute(delete(MqttEvent))
    db.execute(delete(Command))
    db.execute(delete(BootstrapSession))
    db.execute(delete(Device))
    db.commit()
    rebuild_crl_from_db(db)
    return {
        "devices": device_count,
        "events": event_count,
        "commands": command_count,
        "bootstrap_sessions": session_count,
        "new_revocations": newly_revoked,
    }



def clear_simulated_devices(db: Session) -> dict[str, int]:
    """Remove only simulated devices while preserving physical fleet records.

    Existing simulated certificates are retained as revocation tombstones so a
    credential copied before cleanup cannot be used again after the benchmark
    resets the broker. Child rows are removed explicitly for SQLite portability.
    """
    devices = db.scalars(
        select(Device).where(Device.deployment_type == "simulated").order_by(Device.device_id)
    ).all()
    device_ids = [device.device_id for device in devices]
    if not device_ids:
        rebuild_crl_from_db(db)
        return {
            "devices": 0,
            "events": 0,
            "commands": 0,
            "bootstrap_sessions": 0,
            "new_revocations": 0,
        }

    event_count = len(
        db.scalars(select(MqttEvent.id).where(MqttEvent.device_id.in_(device_ids))).all()
    )
    command_count = len(
        db.scalars(select(Command.command_id).where(Command.device_id.in_(device_ids))).all()
    )
    session_count = len(
        db.scalars(
            select(BootstrapSession.session_id).where(BootstrapSession.device_id.in_(device_ids))
        ).all()
    )

    newly_revoked = 0
    for device in devices:
        if device.certificate_serial and db.get(RevokedCertificate, device.certificate_serial) is None:
            db.add(
                RevokedCertificate(
                    serial_hex=device.certificate_serial,
                    device_id=device.device_id,
                    revoked_at=utcnow(),
                    reason="cessation_of_operation",
                )
            )
            newly_revoked += 1
    db.flush()

    db.execute(delete(MqttEvent).where(MqttEvent.device_id.in_(device_ids)))
    db.execute(delete(Command).where(Command.device_id.in_(device_ids)))
    db.execute(delete(BootstrapSession).where(BootstrapSession.device_id.in_(device_ids)))
    db.execute(delete(Device).where(Device.device_id.in_(device_ids)))
    db.commit()
    rebuild_crl_from_db(db)

    return {
        "devices": len(device_ids),
        "events": event_count,
        "commands": command_count,
        "bootstrap_sessions": session_count,
        "new_revocations": newly_revoked,
    }

def clear_safe_runtime_files() -> dict[str, int]:
    """Report file cleanup handled by service-specific reset endpoints.

    The live SQLite WAL/SHM files are intentionally not unlinked while the API owns
    an open database connection. Simulator credential directories are purged by the
    Simulation Manager before the database transaction begins.
    """
    return {"runtime_files_removed": 0}


def reset_project(db: Session) -> dict[str, Any]:
    try:
        simulation = stop_and_purge_simulation()
    except ProjectResetError as exc:
        # A running simulator could immediately re-register devices, so a reset must stop here.
        raise ProjectResetError(f"Simulation Manager must be reachable before reset: {exc}") from exc

    manufacturing = verify_manufacturing_idle()
    database = clear_runtime_database(db)
    files = clear_safe_runtime_files()
    return {
        "simulation": simulation,
        "manufacturing": manufacturing,
        "database": database,
        "files": files,
        "preserved": [
            ".env",
            "DASHBOARD_USERNAME",
            "DASHBOARD_PASSWORD",
            "BOOTSTRAP_MASTER_KEY",
            "Root CA and private key",
            "API/broker/control service certificates",
            "Revocation tombstones for certificates issued before reset",
        ],
    }
