from __future__ import annotations

from cryptography.fernet import Fernet

from app.security import (
    SecretBox,
    calculate_proof_hex,
    generate_bootstrap_secret,
    verify_proof_hex,
)


def test_secret_box_round_trip() -> None:
    box = SecretBox.from_master_key(Fernet.generate_key().decode("ascii"))
    token = box.encrypt("individual-secret")
    assert token != "individual-secret"
    assert box.decrypt(token) == "individual-secret"


def test_hmac_is_bound_to_session_nonce_and_csr() -> None:
    secret = generate_bootstrap_secret()
    fields = {
        "secret_b64": secret,
        "device_id": "CROMALED-0001",
        "session_id": "0123456789abcdef0123456789abcdef",
        "nonce_b64": "nonce-demo",
        "csr_digest": "a" * 64,
    }
    proof = calculate_proof_hex(**fields)
    assert verify_proof_hex(received_proof=proof, **fields)
    assert not verify_proof_hex(
        received_proof=proof,
        **{**fields, "csr_digest": "b" * 64},
    )
    assert not verify_proof_hex(
        received_proof=proof,
        **{**fields, "nonce_b64": "otro-nonce"},
    )
