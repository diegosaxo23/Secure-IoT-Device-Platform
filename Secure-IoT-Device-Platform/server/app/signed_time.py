from __future__ import annotations

import base64
import hashlib
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

PROTOCOL_ID = "IOT-SIGNED-TIME-V1"


def canonical_time_message(nonce: str, unix_time: int) -> bytes:
    return f"{PROTOCOL_ID}\n{nonce}\n{unix_time}\n".encode("ascii")


def load_signing_key(path: Path) -> ec.EllipticCurvePrivateKey:
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        raise TypeError("time signing key is not an EC private key")
    return key


def public_key_fingerprint(key: ec.EllipticCurvePrivateKey) -> str:
    public_der = key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return hashlib.sha256(public_der).hexdigest()


def sign_time(key: ec.EllipticCurvePrivateKey, nonce: str, unix_time: int) -> str:
    signature = key.sign(canonical_time_message(nonce, unix_time), ec.ECDSA(hashes.SHA256()))
    return base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
