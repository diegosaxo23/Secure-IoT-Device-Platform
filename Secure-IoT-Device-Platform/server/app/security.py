from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass

from cryptography import x509
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import serialization


DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{2,63}$")
PROTOCOL_ID = "IOT-BOOTSTRAP-V1"


class SecurityError(ValueError):
    """Cryptographic or input-format validation error."""


def validate_device_id(device_id: str) -> str:
    normalized = device_id.strip()
    if not DEVICE_ID_RE.fullmatch(normalized):
        raise SecurityError(
            "invalid device_id: use 3 to 64 alphanumeric characters plus '.', '_', ':', or '-'"
        )
    return normalized


def generate_bootstrap_secret() -> str:
    """Generate 256 random bits and encode them as unpadded base64url."""

    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")


def decode_bootstrap_secret(secret_b64: str) -> bytes:
    value = secret_b64.strip().encode("ascii")
    padding = b"=" * (-len(value) % 4)
    try:
        decoded = base64.urlsafe_b64decode(value + padding)
    except Exception as exc:  # pragma: no cover - exception type depends on Python
        raise SecurityError("bootstrap secret has invalid base64url encoding") from exc
    if len(decoded) < 32:
        raise SecurityError("bootstrap secret must contain at least 256 bits")
    return decoded


@dataclass(frozen=True)
class SecretBox:
    fernet: Fernet

    @classmethod
    def from_master_key(cls, master_key: str) -> "SecretBox":
        raw = master_key.strip().encode("ascii")
        try:
            return cls(Fernet(raw))
        except Exception as exc:
            raise SecurityError(
                "BOOTSTRAP_MASTER_KEY is not a valid Fernet key. Run scripts/setup.py."
            ) from exc

    def encrypt(self, plaintext: str) -> str:
        return self.fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str) -> str:
        try:
            return self.fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise SecurityError("could not decrypt the bootstrap secret") from exc


def csr_sha256(csr: x509.CertificateSigningRequest) -> str:
    der = csr.public_bytes(serialization.Encoding.DER)
    return hashlib.sha256(der).hexdigest()


def canonical_proof_message(
    *,
    device_id: str,
    session_id: str,
    nonce_b64: str,
    csr_digest: str,
) -> bytes:
    """Build the exact byte string covered by HMAC-SHA256.

    The CSR hash binds proof of possession of the bootstrap secret to the requested
    operational key and prevents CSR substitution during the exchange.
    """

    fields = (PROTOCOL_ID, device_id, session_id, nonce_b64, csr_digest.lower())
    return ("\n".join(fields) + "\n").encode("utf-8")


def calculate_proof_hex(
    *,
    secret_b64: str,
    device_id: str,
    session_id: str,
    nonce_b64: str,
    csr_digest: str,
) -> str:
    key = decode_bootstrap_secret(secret_b64)
    message = canonical_proof_message(
        device_id=device_id,
        session_id=session_id,
        nonce_b64=nonce_b64,
        csr_digest=csr_digest,
    )
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def verify_proof_hex(
    *,
    received_proof: str,
    secret_b64: str,
    device_id: str,
    session_id: str,
    nonce_b64: str,
    csr_digest: str,
) -> bool:
    expected = calculate_proof_hex(
        secret_b64=secret_b64,
        device_id=device_id,
        session_id=session_id,
        nonce_b64=nonce_b64,
        csr_digest=csr_digest,
    )
    candidate = received_proof.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", candidate):
        return False
    return hmac.compare_digest(expected, candidate)
