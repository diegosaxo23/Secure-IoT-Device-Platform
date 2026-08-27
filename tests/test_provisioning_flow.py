from __future__ import annotations

import hashlib
from pathlib import Path

from cryptography import x509
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient


def test_complete_bootstrap_flow(
    tmp_path: Path,
    monkeypatch,
    test_ca: tuple[Path, Path, x509.Certificate],
) -> None:
    ca_cert_path, ca_key_path, _ca_cert = test_ca
    crl_path = tmp_path / "ca.crl"

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("BOOTSTRAP_MASTER_KEY", Fernet.generate_key().decode("ascii"))
    monkeypatch.setenv("DASHBOARD_USERNAME", "admin")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "test-password")
    monkeypatch.setenv("MQTT_ENABLED", "false")
    monkeypatch.setenv("CA_CERT_PATH", str(ca_cert_path))
    monkeypatch.setenv("CA_KEY_PATH", str(ca_key_path))
    monkeypatch.setenv("CRL_PATH", str(crl_path))
    monkeypatch.setenv("MQTT_PUBLIC_HOST", "broker.example.test")

    # Import these modules after setting the environment because they create the global engine.
    from app.config import get_settings

    get_settings.cache_clear()
    from app.main import app
    from app.security import calculate_proof_hex, csr_sha256, generate_bootstrap_secret

    auth = ("admin", "test-password")
    device_id = "CROMALED-0001"

    with TestClient(app, base_url="https://testserver") as client:
        registration = client.post(
            "/api/v1/admin/devices",
            auth=auth,
            json={"device_id": device_id, "family": "CromaLED"},
        )
        assert registration.status_code == 201, registration.text
        bootstrap_secret = registration.json()["bootstrap_secret"]

        private_key = ec.generate_private_key(ec.SECP256R1())
        csr = (
            x509.CertificateSigningRequestBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "UNTRUSTED-CSR-SUBJECT")]))
            .sign(private_key, hashes.SHA256())
        )
        csr_pem = csr.public_bytes(serialization.Encoding.PEM).decode("ascii")

        challenge_response = client.post(
            "/api/v1/bootstrap/challenge",
            json={"device_id": device_id},
        )
        assert challenge_response.status_code == 201, challenge_response.text
        first_challenge = challenge_response.json()

        # Requesting a newer challenge invalidates the previous pending one.
        replacement_response = client.post(
            "/api/v1/bootstrap/challenge",
            json={"device_id": device_id},
        )
        assert replacement_response.status_code == 201, replacement_response.text
        challenge = replacement_response.json()

        digest = csr_sha256(csr)
        first_proof = calculate_proof_hex(
            secret_b64=bootstrap_secret,
            device_id=device_id,
            session_id=first_challenge["session_id"],
            nonce_b64=first_challenge["nonce"],
            csr_digest=digest,
        )
        superseded = client.post(
            "/api/v1/bootstrap/enroll",
            json={
                "device_id": device_id,
                "session_id": first_challenge["session_id"],
                "csr_pem": csr_pem,
                "proof": first_proof,
            },
        )
        assert superseded.status_code == 409

        # A valid Device ID is insufficient without possession of its bootstrap secret.
        wrong_secret_proof = calculate_proof_hex(
            secret_b64=generate_bootstrap_secret(),
            device_id=device_id,
            session_id=challenge["session_id"],
            nonce_b64=challenge["nonce"],
            csr_digest=digest,
        )
        wrong_secret = client.post(
            "/api/v1/bootstrap/enroll",
            json={
                "device_id": device_id,
                "session_id": challenge["session_id"],
                "csr_pem": csr_pem,
                "proof": wrong_secret_proof,
            },
        )
        assert wrong_secret.status_code == 401
        assert wrong_secret.json()["detail"] == "invalid cryptographic proof"

        # A proof calculated for CSR-A must not authorize a different CSR-B.
        replacement_key = ec.generate_private_key(ec.SECP256R1())
        replacement_csr = (
            x509.CertificateSigningRequestBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ATTACKER-CSR")]))
            .sign(replacement_key, hashes.SHA256())
        )
        replacement_csr_pem = replacement_csr.public_bytes(serialization.Encoding.PEM).decode("ascii")
        proof_for_original_csr = calculate_proof_hex(
            secret_b64=bootstrap_secret,
            device_id=device_id,
            session_id=challenge["session_id"],
            nonce_b64=challenge["nonce"],
            csr_digest=digest,
        )
        substituted = client.post(
            "/api/v1/bootstrap/enroll",
            json={
                "device_id": device_id,
                "session_id": challenge["session_id"],
                "csr_pem": replacement_csr_pem,
                "proof": proof_for_original_csr,
            },
        )
        assert substituted.status_code == 401
        assert substituted.json()["detail"] == "invalid cryptographic proof"

        proof = calculate_proof_hex(
            secret_b64=bootstrap_secret,
            device_id=device_id,
            session_id=challenge["session_id"],
            nonce_b64=challenge["nonce"],
            csr_digest=digest,
        )
        enrollment = client.post(
            "/api/v1/bootstrap/enroll",
            json={
                "device_id": device_id,
                "session_id": challenge["session_id"],
                "csr_pem": csr_pem,
                "proof": proof,
            },
        )
        assert enrollment.status_code == 200, enrollment.text
        result = enrollment.json()
        assert result["mqtt"]["host"] == "broker.example.test"
        assert result["mqtt"]["command_topic"] == f"devices/{device_id}/command"

        cert = x509.load_pem_x509_certificate(result["certificate_pem"].encode("ascii"))
        assert cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == device_id
        assert hashlib.sha256(
            cert.public_key().public_bytes(
                serialization.Encoding.DER,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        ).digest() == hashlib.sha256(
            private_key.public_key().public_bytes(
                serialization.Encoding.DER,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        ).digest()

        replay = client.post(
            "/api/v1/bootstrap/enroll",
            json={
                "device_id": device_id,
                "session_id": challenge["session_id"],
                "csr_pem": csr_pem,
                "proof": proof,
            },
        )
        assert replay.status_code == 409

        second_challenge = client.post(
            "/api/v1/bootstrap/challenge", json={"device_id": device_id}
        )
        assert second_challenge.status_code == 409

        dashboard = client.get("/", auth=auth)
        assert dashboard.status_code == 200
        assert device_id in dashboard.text
        detail = client.get(f"/devices/{device_id}", auth=auth)
        assert detail.status_code == 200
        assert "Identity and credentials" in detail.text
        assert "CromaLED" in detail.text
        assert "Royal Blue" in detail.text
        assert "Deep Red" in detail.text
        assert "CH 11" in detail.text

        runtime_state = client.get(f"/api/v1/admin/devices/{device_id}/state", auth=auth)
        assert runtime_state.status_code == 200
        assert runtime_state.json()["device_id"] == device_id
        assert runtime_state.json()["family"] == "CromaLED"
        assert runtime_state.json()["deployment_type"] == "physical"
