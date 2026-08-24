from __future__ import annotations

import base64
import logging
import secrets
import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..models import BootstrapSession, Device, RevokedCertificate
from ..pki import (
    issue_device_certificate,
    load_and_validate_csr,
    read_ca_pem,
)
from ..registry import rebuild_crl_from_db
from ..schemas import (
    ChallengeRequest,
    ChallengeResponse,
    EnrollmentRequest,
    EnrollmentResponse,
    MqttProvisioningData,
)
from ..security import SecretBox, SecurityError, csr_sha256, verify_proof_hex
from ..time_utils import ensure_utc, utcnow


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/bootstrap", tags=["bootstrapping"])
MAX_ENROLLMENT_ATTEMPTS = 5


def _generic_device_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="device is not registered, enabled, or authorized",
    )


@router.post("/challenge", response_model=ChallengeResponse, status_code=status.HTTP_201_CREATED)
def create_challenge(
    payload: ChallengeRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> ChallengeResponse:
    settings = get_settings()
    device = db.get(Device, payload.device_id)
    if device is None or not device.enabled or device.lifecycle_status == "revoked":
        raise _generic_device_error()

    if (
        device.lifecycle_status == "provisioned"
        and not settings.allow_reprovisioning
        and not device.allow_reprovisioning
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="device already has operational credentials",
        )

    now = utcnow()
    # Keep only the most recent challenge active to reduce the attack window.
    db.execute(
        update(BootstrapSession)
        .where(
            BootstrapSession.device_id == device.device_id,
            BootstrapSession.consumed_at.is_(None),
        )
        .values(consumed_at=now, result="superseded")
    )

    session_id = uuid.uuid4().hex
    nonce = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")
    expires_at = now + timedelta(seconds=settings.challenge_ttl_seconds)
    source_ip = request.client.host if request.client else None

    session = BootstrapSession(
        session_id=session_id,
        device_id=device.device_id,
        nonce_b64=nonce,
        created_at=now,
        expires_at=expires_at,
        source_ip=source_ip,
    )
    db.add(session)
    db.commit()

    return ChallengeResponse(
        device_id=device.device_id,
        session_id=session_id,
        nonce=nonce,
        expires_at=expires_at,
    )


@router.post("/enroll", response_model=EnrollmentResponse)
def enroll_device(payload: EnrollmentRequest, request: Request, db: Session = Depends(get_db)) -> EnrollmentResponse:
    settings = get_settings()
    now = utcnow()

    session = db.get(BootstrapSession, payload.session_id)
    device = db.get(Device, payload.device_id)
    if (
        session is None
        or device is None
        or session.device_id != payload.device_id
        or not device.enabled
        or device.lifecycle_status == "revoked"
    ):
        raise _generic_device_error()

    if session.consumed_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="challenge has already been used or invalidated",
        )

    expires_at = ensure_utc(session.expires_at)
    if expires_at is None or expires_at <= now:
        session.consumed_at = now
        session.result = "expired"
        db.commit()
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="challenge has expired")

    if session.attempts >= MAX_ENROLLMENT_ATTEMPTS:
        session.consumed_at = now
        session.result = "blocked"
        db.commit()
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="session blocked")

    session.attempts += 1
    session.last_attempt_at = now
    db.add(session)
    db.commit()

    try:
        csr = load_and_validate_csr(payload.csr_pem, payload.device_id)
        digest = csr_sha256(csr)
        secret_box = SecretBox.from_master_key(settings.bootstrap_master_key)
        bootstrap_secret = secret_box.decrypt(device.bootstrap_secret_encrypted)
        proof_ok = verify_proof_hex(
            received_proof=payload.proof,
            secret_b64=bootstrap_secret,
            device_id=device.device_id,
            session_id=session.session_id,
            nonce_b64=session.nonce_b64,
            csr_digest=digest,
        )
    except SecurityError as exc:
        if session.attempts >= MAX_ENROLLMENT_ATTEMPTS:
            session.consumed_at = now
            session.result = "invalid"
        db.add(session)
        db.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if not proof_ok:
        if session.attempts >= MAX_ENROLLMENT_ATTEMPTS:
            session.consumed_at = now
            session.result = "invalid"
        db.add(session)
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid cryptographic proof",
        )

    reprovisioning_allowed = settings.allow_reprovisioning or device.allow_reprovisioning
    if device.lifecycle_status == "provisioned" and not reprovisioning_allowed:
        session.consumed_at = now
        session.result = "already-provisioned"
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="device has already been provisioned",
        )

    # Preserve the previous certificate until the replacement is issued successfully.
    # This prevents a CA failure from leaving the device without a valid operational
    # credential.
    previous_serial = device.certificate_serial

    try:
        issued = issue_device_certificate(
            csr=csr,
            device_id=device.device_id,
            ca_cert_path=settings.ca_cert_path,
            ca_key_path=settings.ca_key_path,
            validity_days=settings.cert_validity_days,
        )
        ca_pem = read_ca_pem(settings.ca_cert_path)
    except SecurityError as exc:
        logger.exception(
            "Error issuing certificate for %s: %s",
            device.device_id,
            exc,
        )
        session.result = "server-error"
        db.add(session)
        db.commit()
        detail = "could not issue the certificate"
        if settings.environment.lower() in {"development", "dev", "test"}:
            detail = f"{detail}: {exc}"
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=detail,
        ) from exc

    old_certificate_revoked = False
    if previous_serial and db.get(RevokedCertificate, previous_serial) is None:
        db.add(
            RevokedCertificate(
                serial_hex=previous_serial,
                device_id=device.device_id,
                revoked_at=now,
                reason="superseded",
            )
        )
        old_certificate_revoked = True

    session.csr_sha256 = digest
    session.consumed_at = now
    session.result = "issued"

    device.lifecycle_status = "provisioned"
    device.provisioned_at = now
    device.revoked_at = None
    device.certificate_serial = issued.serial_hex
    device.certificate_not_after = issued.not_after
    device.certificate_pem = issued.pem

    db.add_all([session, device])
    db.commit()
    if old_certificate_revoked:
        rebuild_crl_from_db(db)
        request.app.state.mqtt_service.evict_device(device.device_id)

    mqtt = MqttProvisioningData(
        host=settings.mqtt_public_host,
        port=settings.mqtt_public_port,
        client_id=device.device_id,
        status_topic=f"devices/{device.device_id}/status",
        telemetry_topic=f"devices/{device.device_id}/telemetry",
        command_topic=f"devices/{device.device_id}/command",
        response_topic=f"devices/{device.device_id}/response",
    )

    return EnrollmentResponse(
        device_id=device.device_id,
        certificate_pem=issued.pem,
        ca_certificate_pem=ca_pem,
        certificate_serial=issued.serial_hex,
        certificate_not_after=issued.not_after,
        mqtt=mqtt,
    )


@router.get("/ca", response_class=PlainTextResponse)
def get_ca_certificate() -> PlainTextResponse:
    try:
        pem = read_ca_pem(get_settings().ca_cert_path)
    except SecurityError as exc:
        raise HTTPException(status_code=500, detail="CA unavailable") from exc
    return PlainTextResponse(pem, media_type="application/x-pem-file")
