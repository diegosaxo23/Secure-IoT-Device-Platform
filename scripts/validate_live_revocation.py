#!/usr/bin/env python3
"""Validate certificate revocation against the deployed Mosquitto broker.

The script uses an already-provisioned simulated identity, proves it can connect,
revokes that exact certificate through the real administration API, waits for the
broker security restart, and verifies that the old certificate cannot establish a
new MQTT/mTLS session.

This is destructive for the selected test identity: its current certificate is
revoked. Re-run/reprovision the simulator afterwards if it is still needed.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urljoin

import paho.mqtt.client as mqtt

from validation_config import parse_env, resolve_api_url
from validation_reports import platform_version, timestamped_output_dir, write_csv, write_metadata

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV = PROJECT_ROOT / ".env"


def provisioning_defaults(root: Path) -> tuple[str | None, int | None]:
    path = root / "provisioning.json"
    if not path.is_file():
        return None, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None
    mqtt_cfg = data.get("mqtt", data)
    if not isinstance(mqtt_cfg, dict):
        return None, None
    host = mqtt_cfg.get("host") or mqtt_cfg.get("mqtt_host")
    port = mqtt_cfg.get("port") or mqtt_cfg.get("mqtt_port")
    return (str(host) if host else None, int(port) if port else None)


def admin_post(url: str, ca: Path, username: str, password: str) -> tuple[int, str]:
    context = ssl.create_default_context(cafile=str(ca))
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    request = urllib.request.Request(
        url=url,
        data=b"{}",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Basic {token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, context=context, timeout=20) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def connect_once(
    *, device_id: str, host: str, port: int, ca: Path, cert: Path, key: Path, timeout: float
) -> tuple[bool, str]:
    done = threading.Event()
    result = {"ok": False, "detail": "timeout"}

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=device_id,
        protocol=mqtt.MQTTv5,
    )
    client.tls_set(
        ca_certs=str(ca),
        certfile=str(cert),
        keyfile=str(key),
        tls_version=ssl.PROTOCOL_TLS_CLIENT,
    )
    client.tls_insecure_set(False)

    def on_connect(_client, _userdata, _flags, reason_code, _properties):  # type: ignore[no-untyped-def]
        result["ok"] = not reason_code.is_failure
        result["detail"] = str(reason_code)
        done.set()

    def on_disconnect(_client, _userdata, _flags, reason_code, _properties):  # type: ignore[no-untyped-def]
        if not done.is_set() and reason_code.is_failure:
            result["detail"] = f"disconnect: {reason_code}"
            done.set()

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect

    try:
        client.connect(host, port, 30)
        client.loop_start()
        done.wait(timeout)
    except (OSError, ssl.SSLError) as exc:
        result["ok"] = False
        result["detail"] = f"TLS/connect error: {exc}"
    finally:
        try:
            client.disconnect()
        except Exception:
            pass
        client.loop_stop()
    return bool(result["ok"]), str(result["detail"])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate live CRL revocation against Mosquitto.")
    parser.add_argument("--device-id", required=True, help="Provisioned simulated identity to revoke")
    parser.add_argument("--state-dir", type=Path, default=Path("simulated_state"))
    parser.add_argument("--api-url")
    parser.add_argument("--mqtt-host")
    parser.add_argument("--mqtt-port", type=int)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--admin-username")
    parser.add_argument("--admin-password")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--restart-wait", type=float, default=8.0)
    parser.add_argument("--output-dir", type=Path, help="Optional explicit CSV report directory")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    output_dir = timestamped_output_dir("live-revocation", args.output_dir)
    rows: list[dict[str, object]] = []
    write_metadata(output_dir / "metadata.csv", {"runner": "validate_live_revocation.py"})

    def flush() -> None:
        write_csv(output_dir / "live-revocation.csv", rows, ["platform_version", "test", "status", "detail"])

    flush()

    env = parse_env(args.env_file)
    try:
        api_url = resolve_api_url(args.api_url, env)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    username = args.admin_username or env.get("DASHBOARD_USERNAME") or os.getenv("DASHBOARD_USERNAME")
    password = args.admin_password or env.get("DASHBOARD_PASSWORD") or os.getenv("DASHBOARD_PASSWORD")
    if not username or not password:
        print("ERROR: dashboard credentials not found", file=sys.stderr)
        return 2

    root = args.state_dir / args.device_id
    cert = root / "device.crt"
    key = root / "device.key"
    ca = root / "ca.crt"
    for path in (cert, key, ca):
        if not path.is_file():
            print(f"ERROR: missing provisioned credential: {path}", file=sys.stderr)
            return 2

    stored_host, stored_port = provisioning_defaults(root)
    host = args.mqtt_host or stored_host or env.get("MQTT_PUBLIC_HOST")
    port = args.mqtt_port or stored_port or int(env.get("MQTT_PUBLIC_PORT", "8883"))
    if not host:
        print("ERROR: MQTT host is unknown; provide --mqtt-host", file=sys.stderr)
        return 2

    print("=" * 72)
    print("       VALIDACION DE REVOCACION - BROKER REAL")
    print("=" * 72)
    print(f"Version   : {platform_version()}")
    print(f"Device ID : {args.device_id}")
    print(f"Broker    : {host}:{port}")
    print()

    before_ok, before_detail = connect_once(
        device_id=args.device_id,
        host=host,
        port=port,
        ca=ca,
        cert=cert,
        key=key,
        timeout=args.timeout,
    )
    print(f"[{'PASS' if before_ok else 'FAIL'}] Certificado vigente conecta -> {before_detail}")
    rows.append({"platform_version": platform_version(), "test": "valid_certificate_connects", "status": "PASS" if before_ok else "FAIL", "detail": before_detail})
    if not before_ok:
        flush()
        return 1

    status, text = admin_post(
        urljoin(api_url.rstrip("/") + "/", f"api/v1/admin/devices/{args.device_id}/revoke"),
        ca,
        username,
        password,
    )
    api_ok = status == 200
    rows.append({"platform_version": platform_version(), "test": "revocation_api", "status": "PASS" if api_ok else "FAIL", "detail": f"HTTP {status}"})
    if not api_ok:
        print(f"[FAIL] Revocation API returned HTTP {status}: {text}")
        flush()
        return 1
    print("[PASS] Certificado anadido a CRL y reinicio de seguridad solicitado")

    print(f"[INFO] Esperando {args.restart_wait:.1f} s a que Mosquitto recargue la CRL...")
    time.sleep(args.restart_wait)

    after_ok, after_detail = connect_once(
        device_id=args.device_id,
        host=host,
        port=port,
        ca=ca,
        cert=cert,
        key=key,
        timeout=args.timeout,
    )
    rejected = not after_ok
    rows.append({"platform_version": platform_version(), "test": "revoked_certificate_rejected", "status": "PASS" if rejected else "FAIL", "detail": after_detail})
    print(f"[{'PASS' if rejected else 'FAIL'}] Certificado revocado no reconecta -> {after_detail}")
    flush()

    print("-" * 72)
    print(f"RESULTADO GLOBAL: {'PASS' if rejected else 'FAIL'}")
    print(f"CSV: {output_dir / 'live-revocation.csv'}")
    print("-" * 72)
    return 0 if rejected else 1


if __name__ == "__main__":
    raise SystemExit(main())
