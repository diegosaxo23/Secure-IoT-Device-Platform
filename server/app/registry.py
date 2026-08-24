from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import get_settings
from .models import Device, RevokedCertificate
from .pki import rebuild_crl
from .security import SecretBox, generate_bootstrap_secret, validate_device_id
from .time_utils import utcnow


class RegistryError(ValueError):
    pass


def _secret_box() -> SecretBox:
    return SecretBox.from_master_key(get_settings().bootstrap_master_key)


def register_device(
    db: Session,
    *,
    device_id: str,
    family: str,
    display_name: str | None = None,
    deployment_type: str = "physical",
    allow_reprovisioning: bool = False,
) -> tuple[Device, str]:
    device_id = validate_device_id(device_id)
    family = family.strip() or "generic"
    deployment_type = deployment_type.strip().lower()
    if deployment_type not in {"physical", "simulated"}:
        raise RegistryError("deployment_type must be physical or simulated")
    if db.get(Device, device_id) is not None:
        raise RegistryError(f"device {device_id} already exists")

    secret = generate_bootstrap_secret()
    device = Device(
        device_id=device_id,
        family=family,
        display_name=(display_name.strip() if display_name else None),
        deployment_type=deployment_type,
        bootstrap_secret_encrypted=_secret_box().encrypt(secret),
        allow_reprovisioning=allow_reprovisioning,
        lifecycle_status="pending",
    )
    db.add(device)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise RegistryError(f"could not register {device_id}") from exc
    db.refresh(device)
    return device, secret


def reset_bootstrap_secret(db: Session, device: Device) -> str:
    if device.certificate_serial:
        revoke_current_certificate(db, device, reason="superseded")

    secret = generate_bootstrap_secret()
    device.bootstrap_secret_encrypted = _secret_box().encrypt(secret)
    device.lifecycle_status = "pending"
    device.provisioned_at = None
    device.certificate_serial = None
    device.certificate_not_after = None
    device.certificate_pem = None
    device.revoked_at = None
    device.online = False
    db.add(device)
    db.commit()
    rebuild_crl_from_db(db)
    return secret


def revoke_current_certificate(db: Session, device: Device, *, reason: str = "key_compromise") -> None:
    if device.certificate_serial and db.get(RevokedCertificate, device.certificate_serial) is None:
        db.add(
            RevokedCertificate(
                serial_hex=device.certificate_serial,
                device_id=device.device_id,
                revoked_at=utcnow(),
                reason=reason,
            )
        )
    device.lifecycle_status = "revoked"
    device.revoked_at = utcnow()
    device.online = False
    db.add(device)
    db.commit()
    rebuild_crl_from_db(db)


def rebuild_crl_from_db(db: Session) -> None:
    settings = get_settings()
    revoked = db.scalars(select(RevokedCertificate).order_by(RevokedCertificate.revoked_at)).all()
    rebuild_crl(
        revoked_certificates=[(item.serial_hex, item.revoked_at, item.reason) for item in revoked],
        ca_cert_path=settings.ca_cert_path,
        ca_key_path=settings.ca_key_path,
        output_path=settings.crl_path,
    )
