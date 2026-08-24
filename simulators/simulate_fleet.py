#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


FAMILIES = [
    ("CromaLED", "CLED-SIM", "cromaled"),
    ("AREA LZ7", "AREA-SIM", "area_lz7"),
    ("AS7341", "AS7341-SIM", "as7341"),
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Launch a simulated IoT Device Platform fleet")
    parser.add_argument("--cromaled", type=int, default=0)
    parser.add_argument("--area-lz7", type=int, default=0)
    parser.add_argument("--as7341", type=int, default=0)
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--api-url", default="https://127.0.0.1:8443")
    parser.add_argument("--ca", type=Path, default=Path("pki/ca/ca.crt"))
    parser.add_argument("--mqtt-host", default="127.0.0.1")
    parser.add_argument("--admin-username", default=os.getenv("DASHBOARD_USERNAME", "admin"))
    parser.add_argument("--admin-password", default=os.getenv("DASHBOARD_PASSWORD"))
    parser.add_argument("--state-dir", type=Path, default=Path("simulated_state"))
    return parser


def main() -> int:
    args = build_parser().parse_args()
    counts = {"cromaled": args.cromaled, "area_lz7": args.area_lz7, "as7341": args.as7341}
    if any(value < 0 for value in counts.values()) or sum(counts.values()) == 0:
        print("ERROR: specify at least one simulator instance", file=sys.stderr)
        return 2
    if not args.admin_password:
        print("ERROR: --admin-password or DASHBOARD_PASSWORD is required", file=sys.stderr)
        return 2
    script = Path(__file__).resolve().parent / "simulated_device.py"
    processes: list[subprocess.Popen[str]] = []
    for family, prefix, key in FAMILIES:
        for index in range(1, counts[key] + 1):
            device_id = f"{prefix}-{index:04d}"
            cmd = [
                sys.executable, str(script), "--device-id", device_id, "--family", family,
                "--api-url", args.api_url, "--bootstrap-ca", str(args.ca), "--state-dir", str(args.state_dir),
                "--auto-register", "--admin-username", args.admin_username, "--admin-password", args.admin_password,
                "--mqtt-host", args.mqtt_host, "--interval", str(args.interval),
            ]
            processes.append(subprocess.Popen(cmd, text=True))
    stopping = False
    def stop(*_args):
        nonlocal stopping
        if stopping:
            return
        stopping = True
        for process in processes:
            if process.poll() is None:
                process.terminate()
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        while any(process.poll() is None for process in processes):
            time.sleep(.5)
    finally:
        stop()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
