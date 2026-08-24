from __future__ import annotations

import base64
import sys
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

from app.signed_time import PROTOCOL_ID, canonical_time_message, sign_time  # noqa: E402


def test_signed_time_token_verifies_with_public_key() -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    nonce = "00112233445566778899aabbccddeeff"
    unix_time = 1787330000
    encoded = sign_time(key, nonce, unix_time)
    padding = "=" * (-len(encoded) % 4)
    signature = base64.urlsafe_b64decode(encoded + padding)

    key.public_key().verify(
        signature,
        canonical_time_message(nonce, unix_time),
        ec.ECDSA(hashes.SHA256()),
    )
    assert canonical_time_message(nonce, unix_time) == (
        f"{PROTOCOL_ID}\n{nonce}\n{unix_time}\n".encode("ascii")
    )


def test_esp32_projects_use_signed_local_time_instead_of_public_ntp() -> None:
    for project in ("CromaLED_Gateway", "AREA_LZ7_Gateway", "AS7341_Gateway"):
        src = ROOT / "firmware" / "esp32" / project / "src"
        config = (src / "AgentConfig.h").read_text(encoding="utf-8")
        agent = (src / "BootstrapAgent.cpp").read_text(encoding="utf-8")
        crypto = (src / "CryptoHelpers.cpp").read_text(encoding="utf-8")
        assert "IOT_TIME_SERVICE_PORT" in config
        assert "pool.ntp.org" not in config
        assert "time.google.com" not in config
        assert "IOT-SIGNED-TIME-V1" in agent
        assert "settimeofday" in agent
        assert "verifySignedMessage" in agent
        assert "mbedtls_pk_verify" in crypto
