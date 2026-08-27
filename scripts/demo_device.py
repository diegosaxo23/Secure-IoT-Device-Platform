#!/usr/bin/env python3
"""Demo IoT device for the complete platform flow.

The program reproduces the behavior expected from the embedded agent:

1. Generate a local P-256 key and CSR when operational credentials are absent.
2. Request an HTTPS challenge from the bootstrap server.
3. Calculate HMAC-SHA256 over the challenge and CSR hash.
4. Receive and store the operational X.509 certificate.
5. Connect to Mosquitto using MQTT over mTLS.
6. Publish status/telemetry and process dashboard commands.

It does not replace production firmware. It provides end-to-end validation of the
server, broker, ACLs, dashboard, and protocol before porting the agent to C/C++
for each product family.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import signal
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
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


PROTOCOL_ID = "IOT-BOOTSTRAP-V1"
DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{2,63}$")
CROMALED_CHANNELS = ["ROYAL_BLUE", "BLUE", "CYAN", "GREEN", "LIME", "LIME2", "AMBER", "AMBER2", "RED_ORANGE", "RED", "DEEP_RED"]


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_write(path: Path, payload: bytes, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)
    if mode is not None:
        try:
            path.chmod(mode)
        except OSError:
            pass


def decode_secret(secret_b64: str) -> bytes:
    encoded = secret_b64.strip().encode("ascii")
    encoded += b"=" * (-len(encoded) % 4)
    try:
        secret = base64.urlsafe_b64decode(encoded)
    except Exception as exc:
        raise ValueError("BOOTSTRAP_SECRET is not valid base64url") from exc
    if len(secret) < 32:
        raise ValueError("BOOTSTRAP_SECRET must contain at least 256 bits")
    return secret


def canonical_message(
    *, device_id: str, session_id: str, nonce: str, csr_sha256: str
) -> bytes:
    fields = (PROTOCOL_ID, device_id, session_id, nonce, csr_sha256.lower())
    return ("\n".join(fields) + "\n").encode("utf-8")


def calculate_proof(
    *, secret_b64: str, device_id: str, session_id: str, nonce: str, csr_sha256: str
) -> str:
    return hmac.new(
        decode_secret(secret_b64),
        canonical_message(
            device_id=device_id,
            session_id=session_id,
            nonce=nonce,
            csr_sha256=csr_sha256,
        ),
        hashlib.sha256,
    ).hexdigest()


def https_json(
    *, method: str, url: str, ca_path: Path, body: dict[str, Any] | None = None
) -> dict[str, Any]:
    context = ssl.create_default_context(cafile=str(ca_path))
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url=url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, context=context, timeout=15) as response:
            payload = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} en {url}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"could not connect to {url}: {exc}") from exc

    try:
        parsed = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid JSON response from {url}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(f"unexpected response from {url}")
    return parsed


@dataclass(frozen=True)
class DevicePaths:
    root: Path
    key: Path
    certificate: Path
    ca: Path
    config: Path

    @classmethod
    def create(cls, state_root: Path, device_id: str) -> "DevicePaths":
        root = state_root / device_id
        return cls(
            root=root,
            key=root / "device.key",
            certificate=root / "device.crt",
            ca=root / "ca.crt",
            config=root / "provisioning.json",
        )


class DemoDevice:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.paths = DevicePaths.create(args.state_dir, args.device_id)
        self.stop_event = threading.Event()
        self.connected = threading.Event()
        self.started_monotonic = time.monotonic()
        self.sequence = 0
        self.demo_value = 0
        family_key = args.family.strip().lower().replace(" ", "").replace("-", "")
        self.is_cromaled = "cromaled" in family_key
        self.channels = [0 for _ in CROMALED_CHANNELS]
        self.provisioning: dict[str, Any] | None = None
        self.client: mqtt.Client | None = None

    def credentials_available(self) -> bool:
        return all(
            path.exists()
            for path in (self.paths.key, self.paths.certificate, self.paths.ca, self.paths.config)
        )

    def validate_local_credentials(self) -> None:
        try:
            certificate = x509.load_pem_x509_certificate(self.paths.certificate.read_bytes())
            private_key = serialization.load_pem_private_key(
                self.paths.key.read_bytes(), password=None
            )
            configuration = json.loads(self.paths.config.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("local credentials are corrupted") from exc

        common_names = certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        if len(common_names) != 1 or common_names[0].value != self.args.device_id:
            raise RuntimeError("local certificate does not match device_id")
        if certificate.not_valid_after_utc <= datetime.now(timezone.utc):
            raise RuntimeError(
                "operational certificate has expired; reset the unit on the server"
            )
        certificate_public = certificate.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        private_public = private_key.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        if not hmac.compare_digest(certificate_public, private_public):
            raise RuntimeError("local private key does not match the certificate")
        if configuration.get("device_id") != self.args.device_id:
            raise RuntimeError("persistent configuration belongs to another unit")

    def provision_if_needed(self) -> None:
        if self.args.force_provision:
            for path in (
                self.paths.key,
                self.paths.certificate,
                self.paths.ca,
                self.paths.config,
            ):
                path.unlink(missing_ok=True)

        if self.credentials_available():
            self.validate_local_credentials()
            self.provisioning = json.loads(self.paths.config.read_text(encoding="utf-8"))
            print(f"[{self.args.device_id}] Existing credentials found: skipping bootstrap")
            return

        if not self.args.bootstrap_secret:
            raise RuntimeError(
                "credentials are missing and --bootstrap-secret was not provided"
            )

        self.paths.root.mkdir(parents=True, exist_ok=True)
        private_key = ec.generate_private_key(ec.SECP256R1())
        csr = (
            x509.CertificateSigningRequestBuilder()
            .subject_name(
                x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, self.args.device_id)])
            )
            .sign(private_key, hashes.SHA256())
        )
        csr_pem = csr.public_bytes(serialization.Encoding.PEM).decode("ascii")
        csr_der = csr.public_bytes(serialization.Encoding.DER)
        csr_digest = hashlib.sha256(csr_der).hexdigest()

        challenge_url = urljoin(self.args.api_url.rstrip("/") + "/", "api/v1/bootstrap/challenge")
        print(f"[{self.args.device_id}] Solicitando challenge a {challenge_url}")
        challenge = https_json(
            method="POST",
            url=challenge_url,
            ca_path=self.args.bootstrap_ca,
            body={"device_id": self.args.device_id},
        )
        if challenge.get("protocol") != PROTOCOL_ID:
            raise RuntimeError("server returned an unsupported protocol version")

        session_id = str(challenge["session_id"])
        nonce = str(challenge["nonce"])
        proof = calculate_proof(
            secret_b64=self.args.bootstrap_secret,
            device_id=self.args.device_id,
            session_id=session_id,
            nonce=nonce,
            csr_sha256=csr_digest,
        )

        enroll_url = urljoin(self.args.api_url.rstrip("/") + "/", "api/v1/bootstrap/enroll")
        print(f"[{self.args.device_id}] Sending CSR and HMAC proof")
        response = https_json(
            method="POST",
            url=enroll_url,
            ca_path=self.args.bootstrap_ca,
            body={
                "device_id": self.args.device_id,
                "session_id": session_id,
                "csr_pem": csr_pem,
                "proof": proof,
            },
        )

        certificate_pem = str(response["certificate_pem"]).encode("ascii")
        ca_pem = str(response["ca_certificate_pem"]).encode("ascii")
        key_pem = private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )

        # Local checks before accepting and persisting the operational identity.
        issued = x509.load_pem_x509_certificate(certificate_pem)
        if issued.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value != self.args.device_id:
            raise RuntimeError("issued certificate does not match device_id")
        if issued.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ) != private_key.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ):
            raise RuntimeError("issued certificate does not contain the requested public key")

        atomic_write(self.paths.key, key_pem, mode=0o600)
        atomic_write(self.paths.certificate, certificate_pem, mode=0o644)
        atomic_write(self.paths.ca, ca_pem, mode=0o644)
        atomic_write(
            self.paths.config,
            json.dumps(response, indent=2, ensure_ascii=False).encode("utf-8"),
            mode=0o600,
        )
        self.provisioning = response
        print(
            f"[{self.args.device_id}] Provisioned. Certificate serial "
            f"{response.get('certificate_serial')}"
        )

    def run_mqtt(self) -> None:
        if self.provisioning is None:
            self.provisioning = json.loads(self.paths.config.read_text(encoding="utf-8"))
        mqtt_cfg = self.provisioning["mqtt"]
        if not isinstance(mqtt_cfg, dict):
            raise RuntimeError("invalid MQTT configuration")

        host = self.args.mqtt_host or str(mqtt_cfg["host"])
        port = self.args.mqtt_port or int(mqtt_cfg["port"])
        client_id = str(mqtt_cfg.get("client_id", self.args.device_id))
        status_topic = str(mqtt_cfg["status_topic"])
        telemetry_topic = str(mqtt_cfg["telemetry_topic"])
        command_topic = str(mqtt_cfg["command_topic"])
        response_topic = str(mqtt_cfg["response_topic"])

        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
            protocol=mqtt.MQTTv5,
        )
        self.client = client
        client.enable_logger()
        client.reconnect_delay_set(min_delay=1, max_delay=30)
        client.tls_set(
            ca_certs=str(self.paths.ca),
            certfile=str(self.paths.certificate),
            keyfile=str(self.paths.key),
            tls_version=ssl.PROTOCOL_TLS_CLIENT,
        )
        client.tls_insecure_set(False)

        offline_payload = json.dumps(
            {
                "online": False,
                "device_id": self.args.device_id,
                "family": self.args.family,
                "reason": "unexpected-disconnect",
            },
            separators=(",", ":"),
        )
        client.will_set(status_topic, offline_payload, qos=1, retain=True)

        def build_runtime_state() -> dict[str, Any]:
            base: dict[str, Any] = {
                "device_id": self.args.device_id,
                "family": self.args.family,
                "firmware": self.args.firmware,
                "timestamp": utc_iso(),
                "uptime_s": int(time.monotonic() - self.started_monotonic),
                "sequence": self.sequence,
            }
            if self.is_cromaled:
                base["channels"] = [
                    {
                        "channel": index + 1,
                        "name": CROMALED_CHANNELS[index],
                        "level": level,
                        "enabled": level > 0,
                    }
                    for index, level in enumerate(self.channels)
                ]
                base["measurements"] = {
                    "active_channels": sum(1 for level in self.channels if level > 0),
                    "average_level": round(sum(self.channels) / len(self.channels), 1),
                }
            else:
                base["measurements"] = {
                    "demo_value": self.demo_value,
                    "simulated_signal": round((self.sequence % 100) / 10.0, 1),
                }
            return base

        def publish_status(reason: str = "periodic") -> None:
            payload = {
                "online": True,
                "device_id": self.args.device_id,
                "family": self.args.family,
                "firmware": self.args.firmware,
                "uptime_s": int(time.monotonic() - self.started_monotonic),
                "timestamp": utc_iso(),
                "reason": reason,
            }
            client.publish(
                status_topic,
                json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
                qos=1,
                retain=True,
            )

        def publish_telemetry() -> None:
            telemetry = build_runtime_state()
            info = client.publish(
                telemetry_topic,
                json.dumps(telemetry, separators=(",", ":"), ensure_ascii=False),
                qos=1,
                retain=False,
            )
            try:
                info.wait_for_publish(timeout=5)
            except RuntimeError:
                pass

        def publish_response(command_id: str, status: str, result: dict[str, Any]) -> None:
            payload = {
                "command_id": command_id,
                "status": status,
                "device_id": self.args.device_id,
                "timestamp": utc_iso(),
                "result": result,
            }
            client.publish(
                response_topic,
                json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
                qos=1,
                retain=False,
            )

        def on_connect(
            connected_client: mqtt.Client,
            _userdata: Any,
            _flags: mqtt.ConnectFlags,
            reason_code: mqtt.ReasonCode,
            _properties: mqtt.Properties | None,
        ) -> None:
            if reason_code.is_failure:
                print(f"[{self.args.device_id}] MQTT connection rejected: {reason_code}")
                self.connected.clear()
                return
            self.connected.set()
            connected_client.subscribe(command_topic, qos=1)
            publish_status("connected")
            print(
                f"[{self.args.device_id}] MQTT mTLS connected to {host}:{port}; "
                f"subscribed to {command_topic}"
            )

        def on_disconnect(
            _connected_client: mqtt.Client,
            _userdata: Any,
            _disconnect_flags: mqtt.DisconnectFlags,
            reason_code: mqtt.ReasonCode,
            _properties: mqtt.Properties | None,
        ) -> None:
            self.connected.clear()
            if not self.stop_event.is_set():
                print(f"[{self.args.device_id}] MQTT disconnected: {reason_code}")

        def on_message(
            _connected_client: mqtt.Client, _userdata: Any, message: mqtt.MQTTMessage
        ) -> None:
            try:
                payload = json.loads(message.payload.decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("payload no es un objeto JSON")
                command_id = str(payload.get("command_id", "unknown"))
                command = str(payload.get("command", ""))
                parameters = payload.get("parameters", {})
                if not isinstance(parameters, dict):
                    parameters = {}

                if command == "ping":
                    publish_response(command_id, "completed", {"pong": True})

                elif command == "get_status":
                    publish_status("command")
                    state = build_runtime_state()
                    publish_response(command_id, "completed", state)
                    publish_telemetry()

                elif command == "set_demo_value":
                    value = parameters.get("value")
                    if not isinstance(value, (int, float)):
                        publish_response(
                            command_id, "rejected", {"error": "value must be numeric"}
                        )
                    else:
                        self.demo_value = round(float(value), 2)
                        publish_response(
                            command_id, "completed", {"demo_value": self.demo_value}
                        )
                        publish_telemetry()

                elif command == "set_channel" and self.is_cromaled:
                    channel = parameters.get("channel")
                    channel_index = parameters.get("channel_index")
                    level = parameters.get("level")
                    if isinstance(channel, str) and channel in CROMALED_CHANNELS:
                        resolved_channel = CROMALED_CHANNELS.index(channel) + 1
                    elif isinstance(channel_index, int) and 1 <= channel_index <= len(CROMALED_CHANNELS):
                        resolved_channel = channel_index
                    elif isinstance(channel, int) and 1 <= channel <= len(CROMALED_CHANNELS):
                        resolved_channel = channel
                    else:
                        resolved_channel = None
                    if resolved_channel is None:
                        publish_response(
                            command_id,
                            "rejected",
                            {"error": "invalid channel for CromaLED"},
                        )
                    elif not isinstance(level, (int, float)):
                        publish_response(
                            command_id, "rejected", {"error": "level must be numeric"}
                        )
                    else:
                        safe_level = int(max(0, min(100, round(float(level)))))
                        self.channels[resolved_channel - 1] = safe_level
                        publish_response(
                            command_id,
                            "completed",
                            {"channel": CROMALED_CHANNELS[resolved_channel - 1], "level": safe_level},
                        )
                        publish_telemetry()

                elif command == "set_all_channels" and self.is_cromaled:
                    level = parameters.get("level")
                    if not isinstance(level, (int, float)):
                        publish_response(
                            command_id, "rejected", {"error": "level must be numeric"}
                        )
                    else:
                        safe_level = int(max(0, min(100, round(float(level)))))
                        self.channels = [safe_level for _ in CROMALED_CHANNELS]
                        publish_response(
                            command_id,
                            "completed",
                            {"channels": len(CROMALED_CHANNELS), "level": safe_level},
                        )
                        publish_telemetry()

                elif command == "restart":
                    # In real firmware, the ACK would be sent before the hardware restart.
                    publish_response(command_id, "accepted", {"action": "simulated-restart"})
                    self.started_monotonic = time.monotonic()
                    self.sequence = 0
                    publish_status("simulated-restart")
                    publish_telemetry()

                else:
                    publish_response(command_id, "unsupported", {"command": command})
            except Exception as exc:
                print(f"[{self.args.device_id}] Invalid command: {exc}", file=sys.stderr)

        client.on_connect = on_connect
        client.on_disconnect = on_disconnect
        client.on_message = on_message

        print(f"[{self.args.device_id}] Connecting over MQTT mTLS to {host}:{port}")
        client.connect(host, port, keepalive=45)
        client.loop_start()

        try:
            if not self.connected.wait(timeout=15):
                raise RuntimeError("MQTT connection did not complete")

            while not self.stop_event.is_set():
                self.sequence += 1
                publish_telemetry()
                if self.sequence % max(1, int(30 / self.args.interval)) == 0:
                    publish_status("periodic")
                if self.args.once:
                    break
                self.stop_event.wait(self.args.interval)
        finally:
            if self.connected.is_set():
                graceful = {
                    "online": False,
                    "device_id": self.args.device_id,
                    "family": self.args.family,
                    "timestamp": utc_iso(),
                    "reason": "graceful-disconnect",
                }
                info = client.publish(
                    status_topic,
                    json.dumps(graceful, separators=(",", ":"), ensure_ascii=False),
                    qos=1,
                    retain=True,
                )
                try:
                    info.wait_for_publish(timeout=3)
                except RuntimeError:
                    pass
            client.disconnect()
            client.loop_stop()
            print(f"[{self.args.device_id}] Device stopped")

    def stop(self, *_args: Any) -> None:
        self.stop_event.set()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Simulated IoT device with bootstrap and MQTT mTLS")
    parser.add_argument("--device-id", required=True, help="Device identifier registered on the server")
    parser.add_argument(
        "--bootstrap-secret",
        default=os.getenv("BOOTSTRAP_SECRET"),
        help="Per-device base64url secret (also BOOTSTRAP_SECRET)",
    )
    parser.add_argument(
        "--api-url",
        default="https://127.0.0.1:8443",
        help="Public or internal HTTPS server URL",
    )
    parser.add_argument(
        "--bootstrap-ca",
        type=Path,
        default=Path("pki/ca/ca.crt"),
        help="Preinstalled CA used to verify the bootstrap server",
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=Path("device_state"),
        help="Directory that simulates persistent device storage",
    )
    parser.add_argument("--family", default="generic", help="Product family")
    parser.add_argument("--firmware", default="demo-1.1.1", help="Version de firmware")
    parser.add_argument("--mqtt-host", default=None, help="Override the MQTT host received during enrollment")
    parser.add_argument("--mqtt-port", type=int, default=None, help="Override the MQTT port")
    parser.add_argument("--interval", type=float, default=5.0, help="Telemetry period in seconds")
    parser.add_argument("--once", action="store_true", help="Publish one sample and exit")
    parser.add_argument(
        "--no-mqtt", action="store_true", help="Perform provisioning only"
    )
    parser.add_argument(
        "--force-provision",
        action="store_true",
        help="Delete local state and request a new certificate",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not DEVICE_ID_RE.fullmatch(args.device_id):
        print(
            "ERROR: --device-id must contain 3 to 64 characters and cannot contain '/'",
            file=sys.stderr,
        )
        return 2
    if args.interval <= 0:
        print("ERROR: --interval must be greater than zero", file=sys.stderr)
        return 2
    if not args.bootstrap_ca.exists():
        print(f"ERROR: bootstrap CA does not exist: {args.bootstrap_ca}", file=sys.stderr)
        return 2

    device = DemoDevice(args)
    signal.signal(signal.SIGINT, device.stop)
    signal.signal(signal.SIGTERM, device.stop)
    try:
        device.provision_if_needed()
        if not args.no_mqtt:
            device.run_mqtt()
    except (OSError, ValueError, RuntimeError, KeyError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
