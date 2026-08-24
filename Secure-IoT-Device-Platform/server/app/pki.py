from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from .security import SecurityError, validate_device_id
from .time_utils import ensure_utc, utcnow


MAX_CSR_BYTES = 16 * 1024


@dataclass(frozen=True)
class IssuedCertificate:
    pem: str
    serial_hex: str
    not_before: datetime
    not_after: datetime


def load_ca(ca_cert_path: Path, ca_key_path: Path):  # type: ignore[no-untyped-def]
    if not ca_cert_path.is_file():
        raise SecurityError(f"CA certificate does not exist: {ca_cert_path}")
    if not ca_key_path.is_file():
        raise SecurityError(f"CA private key does not exist: {ca_key_path}")

    try:
        ca_cert_bytes = ca_cert_path.read_bytes()
        ca_key_bytes = ca_key_path.read_bytes()
    except PermissionError as exc:
        raise SecurityError(f"permission denied while reading PKI material: {exc.filename}") from exc
    except OSError as exc:
        raise SecurityError(f"could not read PKI material: {exc}") from exc

    try:
        ca_cert = x509.load_pem_x509_certificate(ca_cert_bytes)
    except ValueError as exc:
        raise SecurityError(f"invalid PEM CA certificate: {ca_cert_path}") from exc

    try:
        ca_key = serialization.load_pem_private_key(ca_key_bytes, password=None)
    except (TypeError, ValueError) as exc:
        raise SecurityError(f"invalid or encrypted PEM CA private key: {ca_key_path}") from exc

    cert_public = ca_cert.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    key_public = ca_key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    if cert_public != key_public:
        raise SecurityError("CA certificate and CA private key do not match")

    return ca_cert, ca_key


def load_and_validate_csr(
    csr_pem: str, expected_device_id: str | None = None
) -> x509.CertificateSigningRequest:
    """Parse and validate the CSR without trusting its subject as identity.

    The authenticated device identity comes from the bootstrap session/HMAC. The CSR
    subject is therefore treated as untrusted metadata and is intentionally ignored
    when the operational certificate is issued. ``expected_device_id`` is accepted
    for backward-compatible callers and validated syntactically only.
    """
    if expected_device_id is not None:
        validate_device_id(expected_device_id)
    csr_bytes = csr_pem.encode("utf-8")
    if len(csr_bytes) > MAX_CSR_BYTES:
        raise SecurityError("CSR exceeds the maximum allowed size")

    try:
        csr = x509.load_pem_x509_csr(csr_bytes)
    except ValueError as exc:
        raise SecurityError("invalid PEM CSR") from exc

    if not csr.is_signature_valid:
        raise SecurityError("CSR internal signature is invalid")

    public_key = csr.public_key()
    if isinstance(public_key, rsa.RSAPublicKey):
        if public_key.key_size < 2048:
            raise SecurityError("RSA keys must be at least 2048 bits")
    elif isinstance(public_key, ec.EllipticCurvePublicKey):
        if not isinstance(public_key.curve, (ec.SECP256R1, ec.SECP384R1)):
            raise SecurityError("only P-256 and P-384 curves are supported")
    elif isinstance(public_key, ed25519.Ed25519PublicKey):
        pass
    else:
        raise SecurityError("unsupported public key type")

    return csr


def issue_device_certificate(
    *,
    csr: x509.CertificateSigningRequest,
    device_id: str,
    ca_cert_path: Path,
    ca_key_path: Path,
    validity_days: int,
) -> IssuedCertificate:
    device_id = validate_device_id(device_id)
    ca_cert, ca_key = load_ca(ca_cert_path, ca_key_path)

    now = utcnow()
    not_before = now
    not_after = now + timedelta(days=validity_days)
    serial = x509.random_serial_number()
    public_key = csr.public_key()

    # Never copy the CSR subject into the issued certificate. The certificate
    # identity is derived exclusively from the already authenticated registry
    # Device ID, so a CSR requesting CN=<another-device> cannot impersonate it.
    builder = (
        x509.CertificateBuilder()
        .subject_name(
            x509.Name(
                [
                    x509.NameAttribute(NameOID.ORGANIZATION_NAME, "IoT Device Platform Devices"),
                    x509.NameAttribute(NameOID.COMMON_NAME, device_id),
                ]
            )
        )
        .issuer_name(ca_cert.subject)
        .public_key(public_key)
        .serial_number(serial)
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=isinstance(public_key, rsa.RSAPublicKey),
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=None,
                decipher_only=None,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]),
            critical=False,
        )
        .add_extension(
            x509.SubjectAlternativeName([x509.UniformResourceIdentifier(f"urn:iot-device:{device_id}")]),
            critical=False,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(public_key),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_cert.public_key()),
            critical=False,
        )
    )

    certificate = builder.sign(private_key=ca_key, algorithm=hashes.SHA256())
    pem = certificate.public_bytes(serialization.Encoding.PEM).decode("ascii")
    return IssuedCertificate(
        pem=pem,
        serial_hex=format(serial, "X"),
        not_before=not_before,
        not_after=not_after,
    )


def read_ca_pem(ca_cert_path: Path) -> str:
    try:
        return ca_cert_path.read_text(encoding="ascii")
    except OSError as exc:
        raise SecurityError(f"could not read the CA certificate: {exc}") from exc


def rebuild_crl(
    *,
    revoked_certificates: Iterable[tuple[str, datetime, str]],
    ca_cert_path: Path,
    ca_key_path: Path,
    output_path: Path,
    validity_days: int = 7,
) -> None:
    ca_cert, ca_key = load_ca(ca_cert_path, ca_key_path)
    now = utcnow()
    builder = (
        x509.CertificateRevocationListBuilder()
        .issuer_name(ca_cert.subject)
        .last_update(now)
        .next_update(now + timedelta(days=validity_days))
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_cert.public_key()), False)
    )

    reason_flags = {
        "key_compromise": x509.ReasonFlags.key_compromise,
        "superseded": x509.ReasonFlags.superseded,
        "cessation_of_operation": x509.ReasonFlags.cessation_of_operation,
        "affiliation_changed": x509.ReasonFlags.affiliation_changed,
    }
    for serial_hex, revoked_at, reason_name in revoked_certificates:
        revoked_at_utc = ensure_utc(revoked_at) or now
        reason = reason_flags.get(reason_name, x509.ReasonFlags.unspecified)
        revoked = (
            x509.RevokedCertificateBuilder()
            .serial_number(int(serial_hex, 16))
            .revocation_date(revoked_at_utc)
            .add_extension(x509.CRLReason(reason), critical=False)
            .build()
        )
        builder = builder.add_revoked_certificate(revoked)

    crl = builder.sign(private_key=ca_key, algorithm=hashes.SHA256())
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = crl.public_bytes(serialization.Encoding.PEM)

    fd, temporary_name = tempfile.mkstemp(prefix="crl-", suffix=".pem", dir=output_path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        # mkstemp() creates files as 0600. The CRL contains no private key
        # material and must remain readable by the unprivileged Mosquitto
        # process through the read-only PKI bind mount. Set the mode before
        # the atomic replace so every regenerated CRL is immediately usable.
        try:
            os.chmod(temporary_name, 0o644)
        except OSError:
            # Windows does not provide full POSIX mode semantics. Docker
            # Desktop exposes bind-mounted files readably in that case.
            pass
        os.replace(temporary_name, output_path)
        try:
            output_path.chmod(0o644)
        except OSError:
            pass
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
