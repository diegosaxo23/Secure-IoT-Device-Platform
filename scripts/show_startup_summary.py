#!/usr/bin/env python3
"""Print the final operator-facing startup summary for start-platform.bat."""

from __future__ import annotations

from pathlib import Path

from network_config import detect_active_wifi_ssid, select_wifi_ipv4

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key.strip()] = value
    return values


def main() -> int:
    env = parse_env(ENV_FILE)
    configured_ip = env.get("API_PUBLIC_HOST", "").strip()
    try:
        active_wifi_ip, _ = select_wifi_ipv4()
    except RuntimeError:
        active_wifi_ip = configured_ip or "not detected"

    host_ssid = detect_active_wifi_ssid() or "not detected"
    dashboard_user = env.get("DASHBOARD_USERNAME", "admin")
    dashboard_password = env.get("DASHBOARD_PASSWORD", "<not available>")
    api_port = env.get("API_PUBLIC_PORT", "8443")
    mqtt_port = env.get("MQTT_PUBLIC_PORT", "8883")
    time_port = env.get("TIME_PUBLIC_PORT", "8091")
    iot_ssid = env.get("IOT_WIFI_SSID", "<not configured>") or "<not configured>"
    public_ip = configured_ip or active_wifi_ip

    print("============================================================")
    print(" SECURE IOT DEVICE PLATFORM - READY")
    print("============================================================")
    print(f"Active PC Wi-Fi SSID : {host_ssid}")
    print(f"Active PC Wi-Fi IPv4 : {active_wifi_ip}")
    print(f"IoT device Wi-Fi     : {iot_ssid}")
    print(f"Dashboard URL        : https://{public_ip}:{api_port}")
    print(f"Local Dashboard      : https://localhost:{api_port}")
    print(f"Dashboard user       : {dashboard_user}")
    print(f"Dashboard password   : {dashboard_password}")
    print(f"MQTT/mTLS            : {public_ip}:{mqtt_port}")
    print(f"Signed local time    : http://{public_ip}:{time_port}")
    print("============================================================")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
