#!/usr/bin/env python3
"""Adversarial bootstrap checks against the deployed HTTPS API.

Unlike pytest, this utility talks to the running platform through its real TLS
endpoint. It creates a temporary simulated registry identity, exercises wrong
bootstrap secret, CSR substitution, malicious CSR subject, and replay, then
revokes the certificate it created.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import secrets
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from validation_config import parse_env, resolve_api_url
from validation_reports import platform_version, timestamped_output_dir, write_csv, write_metadata

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV = PROJECT_ROOT / ".env"
DEFAULT_CA = PROJECT_ROOT / "pki" / "ca" / "ca.crt"
PROTOCOL_ID = "IOT-BOOTSTRAP-V1"


class HttpResult:
    def __init__(self, status: int, payload: dict[str, Any] | None, text: str) -> None:
        self.status = status
        self.payload = payload or {}
        self.text = text


def request_json(
    *,
    method: str,
    url: str,
    ca: Path,
    body: dict[str, Any] | None = None,
    username: str | None = None,
    password: str | None = None,
    timeout: float = 20.0,
) -> HttpResult:
    context = ssl.create_default_context(cafile=str(ca))
    headers = {"Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if username is not None and password is not None:
        token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {token}"
    req = urllib.request.Request(url=url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, context=context, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            payload = json.loads(raw) if raw else {}
            return HttpResult(response.status, payload if isinstance(payload, dict) else {}, raw)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {}
        return HttpResult(exc.code, payload if isinstance(payload, dict) else {}, raw)


def generate_secret() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")


def decode_secret(secret: str) -> bytes:
    value = secret.encode("ascii") + b"=" * (-len(secret) % 4)
    return base64.urlsafe_b64decode(value)


def make_csr(common_name: str) -> tuple[ec.EllipticCurvePrivateKey, x509.CertificateSigningRequest, str, str]:
    key = ec.generate_private_key(ec.SECP256R1())
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)]))
        .sign(key, hashes.SHA256())
    )
    pem = csr.public_bytes(serialization.Encoding.PEM).decode("ascii")
    digest = hashlib.sha256(csr.public_bytes(serialization.Encoding.DER)).hexdigest()
    return key, csr, pem, digest


def proof(secret: str, device_id: str, challenge: dict[str, Any], csr_digest: str) -> str:
    message = (
        "\n".join(
            (
                PROTOCOL_ID,
                device_id,
                str(challenge["session_id"]),
                str(challenge["nonce"]),
                csr_digest.lower(),
            )
        )
        + "\n"
    ).encode("utf-8")
    return hmac.new(decode_secret(secret), message, hashlib.sha256).hexdigest()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run live adversarial bootstrap checks against the deployed API.")
    parser.add_argument("--api-url", help="Defaults to API_PUBLIC_HOST/PORT in .env")
    parser.add_argument("--ca", type=Path, default=DEFAULT_CA)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--admin-username")
    parser.add_argument("--admin-password")
    parser.add_argument("--device-id", help="Optional test Device ID; default is generated uniquely")
    parser.add_argument("--leave-active", action="store_true", help="Do not revoke the test certificate at the end")
    parser.add_argument("--output-dir", type=Path, help="Optional explicit CSV report directory")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    output_dir = timestamped_output_dir("live-bootstrap", args.output_dir)
    rows: list[dict[str, object]] = []
    write_metadata(output_dir / "metadata.csv", {"runner": "validate_live_bootstrap_security.py"})
    # Create the report files immediately. They will be overwritten with the
    # actual rows at the end of a successful/partial live validation.
    write_csv(output_dir / "live-bootstrap-security.csv", rows, ["platform_version", "test", "status", "detail"])
    write_csv(output_dir / "live-bootstrap-summary.csv", [], ["platform_version", "device_id", "passed", "failed", "total"])
    env = parse_env(args.env_file)
    try:
        api_url = resolve_api_url(args.api_url, env)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    username = args.admin_username or env.get("DASHBOARD_USERNAME") or os.getenv("DASHBOARD_USERNAME")
    password = args.admin_password or env.get("DASHBOARD_PASSWORD") or os.getenv("DASHBOARD_PASSWORD")
    if not username or not password:
        print("ERROR: dashboard credentials not found", file=sys.stderr)
        return 2
    if not args.ca.is_file():
        print(f"ERROR: CA file not found: {args.ca}", file=sys.stderr)
        return 2

    device_id = args.device_id or f"SEC-TEST-{int(time.time())}"
    admin_base = urljoin(api_url.rstrip("/") + "/", "api/v1/admin/")
    bootstrap_base = urljoin(api_url.rstrip("/") + "/", "api/v1/bootstrap/")

    print("=" * 72)
    print("       VALIDACION ADVERSA EN API REAL - SECURE IOT PLATFORM")
    print("=" * 72)
    print(f"Version   : {platform_version()}")
    print(f"API       : {api_url}")
    print(f"Device ID : {device_id}")
    print()

    registration = request_json(
        method="POST",
        url=urljoin(admin_base, "devices"),
        ca=args.ca,
        username=username,
        password=password,
        body={
            "device_id": device_id,
            "family": "CromaLED",
            "display_name": "[SECURITY TEST] live bootstrap validation",
            "deployment_type": "simulated",
            "allow_reprovisioning": False,
        },
    )
    if registration.status == 409:
        registration = request_json(
            method="POST",
            url=urljoin(admin_base, f"devices/{device_id}/reset-bootstrap"),
            ca=args.ca,
            username=username,
            password=password,
            body={},
        )
    if registration.status != 201 and registration.status != 200:
        print(f"[FAIL] Could not register/reset test device: HTTP {registration.status} {registration.text}")
        return 2
    bootstrap_secret = str(registration.payload.get("bootstrap_secret", ""))
    if not bootstrap_secret:
        print("[FAIL] Server did not return a bootstrap secret for the temporary test device")
        return 2

    passed = 0
    total = 4

    def add_result(name: str, ok: bool, detail: str) -> None:
        rows.append({"platform_version": platform_version(), "test": name, "status": "PASS" if ok else "FAIL", "detail": detail})

    # 1) Valid Device ID + wrong secret.
    _key_a, _csr_a, csr_a_pem, digest_a = make_csr(device_id)
    challenge = request_json(
        method="POST", url=urljoin(bootstrap_base, "challenge"), ca=args.ca, body={"device_id": device_id}
    )
    if challenge.status != 201:
        print(f"[FAIL] Challenge setup failed: HTTP {challenge.status}")
        return 2
    wrong = request_json(
        method="POST",
        url=urljoin(bootstrap_base, "enroll"),
        ca=args.ca,
        body={
            "device_id": device_id,
            "session_id": challenge.payload["session_id"],
            "csr_pem": csr_a_pem,
            "proof": proof(generate_secret(), device_id, challenge.payload, digest_a),
        },
    )
    ok = wrong.status == 401
    print(f"[{'PASS' if ok else 'FAIL'}] Secreto bootstrap incorrecto -> HTTP {wrong.status}")
    add_result("wrong_bootstrap_secret", ok, f"HTTP {wrong.status}")
    passed += int(ok)

    # 2) Proof for CSR-A used with CSR-B.
    challenge = request_json(
        method="POST", url=urljoin(bootstrap_base, "challenge"), ca=args.ca, body={"device_id": device_id}
    )
    _key_b, _csr_b, csr_b_pem, _digest_b = make_csr("ATTACKER-CSR")
    substituted = request_json(
        method="POST",
        url=urljoin(bootstrap_base, "enroll"),
        ca=args.ca,
        body={
            "device_id": device_id,
            "session_id": challenge.payload["session_id"],
            "csr_pem": csr_b_pem,
            "proof": proof(bootstrap_secret, device_id, challenge.payload, digest_a),
        },
    )
    ok = substituted.status == 401
    print(f"[{'PASS' if ok else 'FAIL'}] Sustitucion de CSR -> HTTP {substituted.status}")
    add_result("csr_substitution", ok, f"HTTP {substituted.status}")
    passed += int(ok)

    # 3) Correctly authenticated CSR deliberately asks for someone else's CN.
    challenge = request_json(
        method="POST", url=urljoin(bootstrap_base, "challenge"), ca=args.ca, body={"device_id": device_id}
    )
    malicious_key, _malicious_csr, malicious_pem, malicious_digest = make_csr("OTHER-DEVICE-IDENTITY")
    enrollment_body = {
        "device_id": device_id,
        "session_id": challenge.payload["session_id"],
        "csr_pem": malicious_pem,
        "proof": proof(bootstrap_secret, device_id, challenge.payload, malicious_digest),
    }
    enrollment = request_json(
        method="POST", url=urljoin(bootstrap_base, "enroll"), ca=args.ca, body=enrollment_body
    )
    identity_ok = False
    if enrollment.status == 200 and enrollment.payload.get("certificate_pem"):
        certificate = x509.load_pem_x509_certificate(str(enrollment.payload["certificate_pem"]).encode("ascii"))
        common_names = certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        cert_cn = common_names[0].value if len(common_names) == 1 else ""
        cert_pub = certificate.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        key_pub = malicious_key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        identity_ok = cert_cn == device_id and hmac.compare_digest(cert_pub, key_pub)
    print(
        f"[{'PASS' if identity_ok else 'FAIL'}] CN solicitado en CSR no controla identidad -> "
        f"certificado CN={device_id if identity_ok else 'inesperado'}"
    )
    add_result("malicious_csr_identity", identity_ok, f"issued_cn={device_id if identity_ok else 'unexpected'}")
    passed += int(identity_ok)

    # 4) Reuse the already consumed successful enrollment request.
    replay = request_json(
        method="POST", url=urljoin(bootstrap_base, "enroll"), ca=args.ca, body=enrollment_body
    )
    ok = replay.status == 409
    print(f"[{'PASS' if ok else 'FAIL'}] Replay de sesion consumida -> HTTP {replay.status}")
    add_result("consumed_session_replay", ok, f"HTTP {replay.status}")
    passed += int(ok)

    if enrollment.status == 200 and not args.leave_active:
        revoked = request_json(
            method="POST",
            url=urljoin(admin_base, f"devices/{device_id}/revoke"),
            ca=args.ca,
            username=username,
            password=password,
            body={},
        )
        if revoked.status == 200:
            print("[INFO] Test certificate revoked after validation.")
        else:
            print(f"[WARN] Could not revoke temporary test certificate: HTTP {revoked.status}")

    write_csv(output_dir / "live-bootstrap-security.csv", rows, ["platform_version", "test", "status", "detail"])
    write_csv(output_dir / "live-bootstrap-summary.csv", [{"platform_version": platform_version(), "device_id": device_id, "passed": passed, "failed": total - passed, "total": total}], ["platform_version", "device_id", "passed", "failed", "total"])

    print()
    print("-" * 72)
    print(f"RESULTADO: {passed}/{total} PRUEBAS REALES SUPERADAS")
    print(f"CSV: {output_dir / 'live-bootstrap-security.csv'}")
    print("-" * 72)
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
