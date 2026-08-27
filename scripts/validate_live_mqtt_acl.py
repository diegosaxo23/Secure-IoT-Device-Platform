#!/usr/bin/env python3
"""Live MQTT ACL validation using a real provisioned device certificate.

The check authenticates as DEVICE-A, publishes once to DEVICE-A's telemetry
branch and once to DEVICE-B's telemetry branch. MQTT v5 PUBACK reason codes are
used so the result comes from the running Mosquitto broker rather than from a
static configuration inspection.
"""
from __future__ import annotations

import argparse
import json
import ssl
import threading
import time
from pathlib import Path

from validation_reports import platform_version, timestamped_output_dir, write_csv, write_metadata

import paho.mqtt.client as mqtt


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate live Mosquitto per-device ACLs over mTLS.")
    parser.add_argument("--device-id", required=True, help="Provisioned identity used to authenticate (DEVICE-A).")
    parser.add_argument("--other-device-id", required=True, help="Different identity whose topic must be rejected (DEVICE-B).")
    parser.add_argument("--state-dir", type=Path, default=Path("simulated_state"))
    parser.add_argument("--mqtt-host", help="Broker host. If omitted, read provisioning.json.")
    parser.add_argument("--mqtt-port", type=int, default=None, help="Broker TLS port. Defaults to provisioning.json or 8883.")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--output-dir", type=Path, help="Optional explicit CSV report directory")
    return parser


def _provisioning_defaults(root: Path) -> tuple[str | None, int | None]:
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


def run_check(args: argparse.Namespace) -> int:
    output_dir = timestamped_output_dir("live-mqtt-acl", args.output_dir)
    rows: list[dict[str, object]] = []
    write_metadata(output_dir / "metadata.csv", {"runner": "validate_live_mqtt_acl.py"})

    def flush() -> None:
        write_csv(output_dir / "live-mqtt-acl.csv", rows, ["platform_version", "test", "status", "detail"])

    flush()

    if args.device_id == args.other_device_id:
        raise SystemExit("--other-device-id must be different from --device-id")

    root = args.state_dir / args.device_id
    cert = root / "device.crt"
    key = root / "device.key"
    ca = root / "ca.crt"
    for path in (cert, key, ca):
        if not path.is_file():
            raise SystemExit(f"missing provisioned credential: {path}")

    stored_host, stored_port = _provisioning_defaults(root)
    host = args.mqtt_host or stored_host
    port = args.mqtt_port or stored_port or 8883
    if not host:
        raise SystemExit("provide --mqtt-host or keep broker host in provisioning.json")

    connected = threading.Event()
    disconnected = threading.Event()
    publish_results: dict[int, mqtt.ReasonCode] = {}
    lock = threading.Lock()

    def on_connect(client, userdata, flags, reason_code, properties):
        del client, userdata, flags, properties
        if not reason_code.is_failure:
            connected.set()
        else:
            with lock:
                publish_results[-1] = reason_code
            connected.set()

    def on_publish(client, userdata, mid, reason_code, properties):
        del client, userdata, properties
        with lock:
            publish_results[mid] = reason_code

    def on_disconnect(client, userdata, disconnect_flags, reason_code, properties):
        del client, userdata, disconnect_flags, reason_code, properties
        disconnected.set()

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=args.device_id,
        protocol=mqtt.MQTTv5,
    )
    client.on_connect = on_connect
    client.on_publish = on_publish
    client.on_disconnect = on_disconnect
    client.tls_set(
        ca_certs=str(ca),
        certfile=str(cert),
        keyfile=str(key),
        tls_version=ssl.PROTOCOL_TLS_CLIENT,
    )
    client.tls_insecure_set(False)

    def wait_publish(mid: int) -> mqtt.ReasonCode | None:
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            with lock:
                result = publish_results.get(mid)
            if result is not None:
                return result
            if disconnected.is_set():
                return None
            time.sleep(0.02)
        return None

    print("============================================================")
    print("        VALIDACION MQTT ACL EN BROKER REAL")
    print("============================================================")
    print(f"Version        : {platform_version()}")
    print(f"Identidad mTLS : {args.device_id}")
    print(f"Broker         : {host}:{port}")
    print()

    try:
        client.connect(host, port, 60)
        client.loop_start()
        if not connected.wait(args.timeout):
            print("[FAIL] No se pudo completar la conexion MQTT/mTLS")
            rows.append({"platform_version": platform_version(), "test": "mqtt_connect", "status": "FAIL", "detail": "timeout"})
            flush()
            return 2
        with lock:
            connect_error = publish_results.get(-1)
        if connect_error is not None:
            print(f"[FAIL] Conexion MQTT/mTLS rechazada: {connect_error}")
            rows.append({"platform_version": platform_version(), "test": "mqtt_connect", "status": "FAIL", "detail": str(connect_error)})
            flush()
            return 2

        own_topic = f"devices/{args.device_id}/telemetry"
        foreign_topic = f"devices/{args.other_device_id}/telemetry"

        own_info = client.publish(own_topic, '{"security_test":"own_topic"}', qos=1)
        own_reason = wait_publish(own_info.mid)
        own_ok = own_reason is not None and not own_reason.is_failure
        rows.append({"platform_version": platform_version(), "test": "publish_own_topic", "status": "PASS" if own_ok else "FAIL", "detail": str(own_reason) if own_reason is not None else "no PUBACK"})
        print(f"[{'PASS' if own_ok else 'FAIL'}] Topic propio: {own_topic}")
        print(f"       Resultado: {own_reason if own_reason is not None else 'sin PUBACK'}")

        foreign_info = client.publish(foreign_topic, '{"security_test":"foreign_topic"}', qos=1)
        foreign_reason = wait_publish(foreign_info.mid)
        foreign_ok = foreign_reason is not None and foreign_reason.is_failure
        rows.append({"platform_version": platform_version(), "test": "publish_foreign_topic_denied", "status": "PASS" if foreign_ok else "FAIL", "detail": str(foreign_reason) if foreign_reason is not None else "no PUBACK"})
        print(f"[{'PASS' if foreign_ok else 'FAIL'}] Topic ajeno: {foreign_topic}")
        print(f"       Resultado: {foreign_reason if foreign_reason is not None else 'sin PUBACK'}")

        flush()
        print(f"CSV: {output_dir / 'live-mqtt-acl.csv'}")
        print("------------------------------------------------------------")
        if own_ok and foreign_ok:
            print("RESULTADO GLOBAL: PASS - ACL aplicada por identidad certificada")
            return 0
        print("RESULTADO GLOBAL: FAIL - revisar ACL/broker")
        return 1
    finally:
        try:
            client.disconnect()
        except Exception:
            pass
        client.loop_stop()


def main() -> int:
    return run_check(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
