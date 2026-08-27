#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import signal
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import paho.mqtt.client as mqtt
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from profiles import create_profile


PROTOCOL_ID = "IOT-BOOTSTRAP-V1"


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_write(path: Path, payload: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)
    try:
        path.chmod(mode)
    except OSError:
        pass


def decode_secret(secret_b64: str) -> bytes:
    encoded = secret_b64.strip().encode("ascii")
    encoded += b"=" * (-len(encoded) % 4)
    secret = base64.urlsafe_b64decode(encoded)
    if len(secret) < 32:
        raise ValueError("BOOTSTRAP_SECRET must contain at least 256 bits")
    return secret


def calculate_proof(*, secret_b64: str, device_id: str, session_id: str, nonce: str, csr_sha256: str) -> str:
    message = ("\n".join((PROTOCOL_ID, device_id, session_id, nonce, csr_sha256.lower())) + "\n").encode("utf-8")
    return hmac.new(decode_secret(secret_b64), message, hashlib.sha256).hexdigest()


def http_json(
    *,
    method: str,
    url: str,
    ca_path: Path,
    body: dict[str, Any] | None = None,
    username: str | None = None,
    password: str | None = None,
) -> dict[str, Any]:
    context = ssl.create_default_context(cafile=str(ca_path))
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if username is not None and password is not None:
        token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {token}"
    request = urllib.request.Request(url=url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, context=context, timeout=20) as response:
            payload = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} at {url}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"could not connect to {url}: {exc}") from exc
    parsed = json.loads(payload.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise RuntimeError("unexpected HTTP response")
    return parsed


@dataclass(frozen=True)
class Paths:
    root: Path
    factory: Path
    key: Path
    certificate: Path
    ca: Path
    provisioning: Path

    @classmethod
    def create(cls, state_root: Path, device_id: str) -> "Paths":
        root = state_root / device_id
        return cls(
            root=root,
            factory=root / "factory.json",
            key=root / "device.key",
            certificate=root / "device.crt",
            ca=root / "ca.crt",
            provisioning=root / "provisioning.json",
        )


class SimulatedDevice:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.paths = Paths.create(args.state_dir, args.device_id)
        self.profile = create_profile(args.family, args.device_id)
        self.stop_event = threading.Event()
        self.connected = threading.Event()
        self.started_monotonic = time.monotonic()
        self.sequence = 0
        self.provisioning: dict[str, Any] | None = None
        self.client: mqtt.Client | None = None
        self.recent_command_ids: deque[str] = deque(maxlen=128)
        self.recent_command_set: set[str] = set()

    def credentials_available(self) -> bool:
        return all(path.exists() for path in (self.paths.key, self.paths.certificate, self.paths.ca, self.paths.provisioning))

    def _remember_command(self, command_id: str) -> bool:
        if command_id in self.recent_command_set:
            return False
        if len(self.recent_command_ids) == self.recent_command_ids.maxlen:
            oldest = self.recent_command_ids.popleft()
            self.recent_command_set.discard(oldest)
        self.recent_command_ids.append(command_id)
        self.recent_command_set.add(command_id)
        return True

    def ensure_factory_identity(self) -> str | None:
        if self.credentials_available():
            return None
        if self.args.bootstrap_secret:
            return self.args.bootstrap_secret
        if self.paths.factory.exists():
            factory = json.loads(self.paths.factory.read_text(encoding="utf-8"))
            secret = factory.get("bootstrap_secret")
            if isinstance(secret, str):
                return secret
        if not self.args.auto_register:
            raise RuntimeError("credentials are missing and no bootstrap identity was provided")
        if not self.args.admin_username or not self.args.admin_password:
            raise RuntimeError("--auto-register requires administrator credentials")

        endpoint = urljoin(self.args.api_url.rstrip("/") + "/", "api/v1/admin/devices")
        body = {
            "device_id": self.args.device_id,
            "family": self.profile.family,
            "display_name": f"[SIM] {self.profile.family} {self.args.device_id}",
            "deployment_type": "simulated",
            "allow_reprovisioning": False,
        }
        try:
            result = http_json(
                method="POST",
                url=endpoint,
                ca_path=self.args.bootstrap_ca,
                body=body,
                username=self.args.admin_username,
                password=self.args.admin_password,
            )
        except RuntimeError as exc:
            if "HTTP 409" not in str(exc):
                raise
            reset_url = urljoin(
                self.args.api_url.rstrip("/") + "/",
                f"api/v1/admin/devices/{self.args.device_id}/reset-bootstrap",
            )
            result = http_json(
                method="POST",
                url=reset_url,
                ca_path=self.args.bootstrap_ca,
                body={},
                username=self.args.admin_username,
                password=self.args.admin_password,
            )
        secret = str(result["bootstrap_secret"])
        self.paths.root.mkdir(parents=True, exist_ok=True)
        atomic_write(
            self.paths.factory,
            json.dumps(
                {
                    "device_id": self.args.device_id,
                    "family": self.profile.family,
                    "deployment_type": "simulated",
                    "bootstrap_secret": secret,
                },
                indent=2,
            ).encode("utf-8"),
        )
        print(f"[{self.args.device_id}] registered as a simulated device")
        return secret

    def validate_local_credentials(self) -> None:
        certificate = x509.load_pem_x509_certificate(self.paths.certificate.read_bytes())
        private_key = serialization.load_pem_private_key(self.paths.key.read_bytes(), password=None)
        common_names = certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        if len(common_names) != 1 or common_names[0].value != self.args.device_id:
            raise RuntimeError("local certificate belongs to another unit")
        cert_public = certificate.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        key_public = private_key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        if not hmac.compare_digest(cert_public, key_public):
            raise RuntimeError("private key does not match the certificate")

    def provision_if_needed(self) -> None:
        if self.credentials_available():
            self.validate_local_credentials()
            self.provisioning = json.loads(self.paths.provisioning.read_text(encoding="utf-8"))
            print(f"[{self.args.device_id}] persistent credentials found: skipping bootstrap")
            return
        secret = self.ensure_factory_identity()
        if not secret:
            raise RuntimeError("no hay bootstrap secret")

        provisioning_started = time.perf_counter()
        crypto_started = time.perf_counter()
        private_key = ec.generate_private_key(ec.SECP256R1())
        csr = (
            x509.CertificateSigningRequestBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, self.args.device_id)]))
            .sign(private_key, hashes.SHA256())
        )
        csr_pem = csr.public_bytes(serialization.Encoding.PEM).decode("ascii")
        digest = hashlib.sha256(csr.public_bytes(serialization.Encoding.DER)).hexdigest()
        crypto_ms = (time.perf_counter() - crypto_started) * 1000.0
        challenge_started = time.perf_counter()
        challenge = http_json(
            method="POST",
            url=urljoin(self.args.api_url.rstrip("/") + "/", "api/v1/bootstrap/challenge"),
            ca_path=self.args.bootstrap_ca,
            body={"device_id": self.args.device_id},
        )
        challenge_ms = (time.perf_counter() - challenge_started) * 1000.0
        proof = calculate_proof(
            secret_b64=secret,
            device_id=self.args.device_id,
            session_id=str(challenge["session_id"]),
            nonce=str(challenge["nonce"]),
            csr_sha256=digest,
        )
        enroll_started = time.perf_counter()
        response = http_json(
            method="POST",
            url=urljoin(self.args.api_url.rstrip("/") + "/", "api/v1/bootstrap/enroll"),
            ca_path=self.args.bootstrap_ca,
            body={
                "device_id": self.args.device_id,
                "session_id": challenge["session_id"],
                "csr_pem": csr_pem,
                "proof": proof,
            },
        )
        enroll_ms = (time.perf_counter() - enroll_started) * 1000.0
        issued = x509.load_pem_x509_certificate(str(response["certificate_pem"]).encode("ascii"))
        issued_public = issued.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        requested_public = private_key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        if not hmac.compare_digest(issued_public, requested_public):
            raise RuntimeError("issued certificate does not contain the requested key")

        self.paths.root.mkdir(parents=True, exist_ok=True)
        atomic_write(
            self.paths.key,
            private_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()),
        )
        atomic_write(self.paths.certificate, str(response["certificate_pem"]).encode("ascii"), 0o644)
        atomic_write(self.paths.ca, str(response["ca_certificate_pem"]).encode("ascii"), 0o644)
        atomic_write(self.paths.provisioning, json.dumps(response, indent=2).encode("utf-8"))
        self.provisioning = response
        total_ms = (time.perf_counter() - provisioning_started) * 1000.0
        print(
            f"[{self.args.device_id}] [METRIC] "
            f"p256_csr_total_ms={crypto_ms:.3f} "
            f"challenge_http_ms={challenge_ms:.3f} "
            f"enroll_http_ms={enroll_ms:.3f} "
            f"provisioning_total_ms={total_ms:.3f}"
        )
        print(f"[{self.args.device_id}] provisioned; certificate {response.get('certificate_serial')}")

    def run(self) -> None:
        self.provision_if_needed()
        if self.provisioning is None:
            raise RuntimeError("operational configuration is missing")
        cfg = self.provisioning["mqtt"]
        host = self.args.mqtt_host or str(cfg["host"])
        port = self.args.mqtt_port or int(cfg["port"])
        status_topic = str(cfg["status_topic"])
        telemetry_topic = str(cfg["telemetry_topic"])
        command_topic = str(cfg["command_topic"])
        response_topic = str(cfg["response_topic"])

        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=self.args.device_id, protocol=mqtt.MQTTv5)
        self.client = client
        client.reconnect_delay_set(min_delay=1, max_delay=20)
        client.tls_set(
            ca_certs=str(self.paths.ca),
            certfile=str(self.paths.certificate),
            keyfile=str(self.paths.key),
            tls_version=ssl.PROTOCOL_TLS_CLIENT,
        )
        client.tls_insecure_set(False)
        client.will_set(
            status_topic,
            json.dumps({"online": False, "device_id": self.args.device_id, "family": self.profile.family, "deployment_type": "simulated", "reason": "unexpected-disconnect"}),
            qos=1,
            retain=True,
        )

        def publish_status(reason: str) -> None:
            client.publish(
                status_topic,
                json.dumps(
                    {
                        "online": True,
                        "device_id": self.args.device_id,
                        "family": self.profile.family,
                        "deployment_type": "simulated",
                        "firmware": self.profile.firmware,
                        "uptime_s": int(time.monotonic() - self.started_monotonic),
                        "timestamp": utc_iso(),
                        "reason": reason,
                    },
                    separators=(",", ":"),
                ),
                qos=1,
                retain=True,
            )

        def current_state() -> dict[str, Any]:
            return self.profile.telemetry(
                sequence=self.sequence,
                uptime_s=int(time.monotonic() - self.started_monotonic),
                timestamp=utc_iso(),
            )

        def publish_telemetry() -> None:
            client.publish(telemetry_topic, json.dumps(current_state(), separators=(",", ":")), qos=1)

        def publish_response(command_id: str, status: str, result: dict[str, Any]) -> None:
            client.publish(
                response_topic,
                json.dumps(
                    {
                        "command_id": command_id,
                        "status": status,
                        "device_id": self.args.device_id,
                        "timestamp": utc_iso(),
                        "result": result,
                    },
                    separators=(",", ":"),
                ),
                qos=1,
            )

        def on_connect(connected_client, _userdata, _flags, reason_code, _properties):  # type: ignore[no-untyped-def]
            if reason_code.is_failure:
                print(f"[{self.args.device_id}] MQTT connection rejected: {reason_code}", file=sys.stderr)
                return
            self.connected.set()
            connected_client.subscribe(command_topic, qos=1)
            publish_status("connected")
            print(f"[{self.args.device_id}] MQTT mTLS connected to {host}:{port}")

        def on_disconnect(_client, _userdata, _flags, reason_code, _properties):  # type: ignore[no-untyped-def]
            self.connected.clear()
            if not self.stop_event.is_set():
                print(f"[{self.args.device_id}] MQTT disconnected: {reason_code}")

        def on_message(_client, _userdata, message):  # type: ignore[no-untyped-def]
            try:
                payload = json.loads(message.payload.decode("utf-8"))
                command_id = str(payload.get("command_id", "unknown"))
                command = str(payload.get("command", ""))
                parameters = payload.get("parameters") if isinstance(payload.get("parameters"), dict) else {}
                if not self._remember_command(command_id):
                    publish_response(command_id, "duplicate", {"ignored": True})
                    return
                if command == "ping":
                    status, result = "completed", {"pong": True}
                elif command == "get_status":
                    status, result = "completed", current_state()
                elif command == "restart":
                    self.started_monotonic = time.monotonic()
                    self.sequence = 0
                    self.profile.reset()
                    status, result = "accepted", {"action": "simulated-restart"}
                else:
                    try:
                        status, result = self.profile.handle_command(command, parameters)
                    except ValueError as exc:
                        if str(exc) == "unsupported":
                            status, result = "unsupported", {"command": command}
                        else:
                            status, result = "rejected", {"error": str(exc)}
                publish_response(command_id, status, result)
                if status in {"completed", "accepted"}:
                    publish_telemetry()
                    if command in {"get_status", "restart"}:
                        publish_status("command")
            except Exception as exc:
                print(f"[{self.args.device_id}] invalid command: {exc}", file=sys.stderr)

        client.on_connect = on_connect
        client.on_disconnect = on_disconnect
        client.on_message = on_message
        # Use the asynchronous connection path so Paho can retry the initial
        # TCP/TLS/MQTT handshake if many simulated clients arrive at the broker
        # at the same time. With a synchronous connect() a transient backlog
        # could make one client exit after a single failed attempt, leaving a
        # large benchmark apparently stuck at X/N connected.
        client.connect_async(host, port, keepalive=45)
        client.loop_start()
        try:
            if not self.connected.wait(self.args.mqtt_connect_timeout):
                raise RuntimeError(
                    f"MQTT connection did not complete within {self.args.mqtt_connect_timeout:.0f}s"
                )
            while not self.stop_event.wait(self.args.interval):
                self.sequence += 1
                publish_telemetry()
                if self.sequence % max(1, int(30 / self.args.interval)) == 0:
                    publish_status("periodic")
        finally:
            if self.connected.is_set():
                client.publish(
                    status_topic,
                    json.dumps({"online": False, "device_id": self.args.device_id, "family": self.profile.family, "deployment_type": "simulated", "reason": "graceful-disconnect", "timestamp": utc_iso()}),
                    qos=1,
                    retain=True,
                ).wait_for_publish(timeout=2)
            client.disconnect()
            client.loop_stop()
            print(f"[{self.args.device_id}] stopped")

    def stop(self, *_args: Any) -> None:
        self.stop_event.set()


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="IoT simulator with real bootstrap and MQTT/mTLS")
    p.add_argument("--device-id", required=True)
    p.add_argument("--family", required=True, choices=["CromaLED", "AREA LZ7", "AS7341"])
    p.add_argument("--api-url", default="https://127.0.0.1:8443")
    p.add_argument("--bootstrap-ca", type=Path, default=Path("pki/ca/ca.crt"))
    p.add_argument("--state-dir", type=Path, default=Path("simulated_state"))
    p.add_argument("--bootstrap-secret")
    p.add_argument("--auto-register", action="store_true")
    p.add_argument("--admin-username", default=os.getenv("DASHBOARD_USERNAME"))
    p.add_argument("--admin-password", default=os.getenv("DASHBOARD_PASSWORD"))
    p.add_argument("--mqtt-host")
    p.add_argument("--mqtt-port", type=int)
    p.add_argument(
        "--mqtt-connect-timeout",
        type=float,
        default=90.0,
        help="Seconds allowed for the initial MQTT/mTLS connection, including automatic reconnects",
    )
    p.add_argument("--interval", type=float, default=5.0)
    return p


def main() -> int:
    args = parser().parse_args()
    if not args.bootstrap_ca.exists():
        print(f"ERROR: bootstrap CA does not exist: {args.bootstrap_ca}", file=sys.stderr)
        return 2
    device = SimulatedDevice(args)
    signal.signal(signal.SIGINT, device.stop)
    signal.signal(signal.SIGTERM, device.stop)
    try:
        device.run()
    except (OSError, ValueError, RuntimeError, KeyError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
