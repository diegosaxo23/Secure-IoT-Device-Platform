#!/usr/bin/env python3
"""Configure the common manufacturing Wi-Fi without modifying firmware source."""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"


def parse_env(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def set_values(path: Path, updates: dict[str, str]) -> None:
    lines = parse_env(path)
    remaining = dict(updates)
    output: list[str] = []
    for raw in lines:
        if "=" in raw and not raw.lstrip().startswith("#"):
            key = raw.split("=", 1)[0].strip()
            if key in remaining:
                output.append(f"{key}={remaining.pop(key)}")
                continue
        output.append(raw)
    if remaining:
        if output and output[-1].strip():
            output.append("")
        output.append("# Common network embedded in manufacturing firmware builds")
        output.extend(f"{key}={value}" for key, value in remaining.items())
    path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Configure common ESP32 manufacturing Wi-Fi")
    parser.add_argument("--ssid")
    parser.add_argument("--password")
    args = parser.parse_args()

    if not ENV_FILE.is_file():
        print("ERROR: .env does not exist. Run start-platform.bat once first.", file=sys.stderr)
        return 2

    ssid = args.ssid if args.ssid is not None else input("Wi-Fi SSID (2.4 GHz): ").strip()
    password = args.password if args.password is not None else getpass.getpass("Wi-Fi password: ")
    if not ssid:
        print("ERROR: SSID cannot be empty", file=sys.stderr)
        return 2
    if "\n" in ssid or "\r" in ssid or "\n" in password or "\r" in password:
        print("ERROR: Wi-Fi values cannot contain line breaks", file=sys.stderr)
        return 2

    set_values(
        ENV_FILE,
        {
            "IOT_WIFI_SSID": ssid,
            "IOT_WIFI_PASSWORD": password,
        },
    )
    print(f"[OK] Manufacturing Wi-Fi configured for SSID: {ssid}")
    print("[INFO] The password was stored only in .env and is not part of the source repository.")
    print("[INFO] factory_program_esp32.py will embed it only in the common product build, never in logs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
