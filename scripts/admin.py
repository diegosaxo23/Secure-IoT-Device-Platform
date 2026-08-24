#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys

from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal, init_db
from app.models import Device
from app.mqtt_service import MqttService
from app.registry import (
    RegistryError,
    register_device,
    reset_bootstrap_secret,
    revoke_current_certificate,
)
from app.time_utils import isoformat_utc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local IoT registry administration")
    sub = parser.add_subparsers(dest="command", required=True)

    register = sub.add_parser("register", help="Register a unit and generate its bootstrap secret")
    register.add_argument("--device-id", required=True)
    register.add_argument("--family", default="generic")
    register.add_argument("--name", default=None)
    register.add_argument("--type", dest="deployment_type", choices=["physical", "simulated"], default="physical")
    register.add_argument("--allow-reprovisioning", action="store_true")

    sub.add_parser("list", help="List registered devices")

    reset = sub.add_parser("reset", help="Revoke the current credential and generate a new bootstrap secret")
    reset.add_argument("--device-id", required=True)

    revoke = sub.add_parser("revoke", help="Add the current certificate to the CRL")
    revoke.add_argument("--device-id", required=True)

    enable = sub.add_parser("enable", help="Enable a device")
    enable.add_argument("--device-id", required=True)

    disable = sub.add_parser("disable", help="Disable a device")
    disable.add_argument("--device-id", required=True)

    return parser


def print_secret(device_id: str, secret: str) -> None:
    print(f"DEVICE_ID={device_id}")
    print(f"BOOTSTRAP_SECRET={secret}")
    print("WARNING: the secret is shown only once; load it into the unit through a secure process.")


def main() -> int:
    args = build_parser().parse_args()
    init_db()

    with SessionLocal() as db:
        if args.command == "register":
            try:
                device, secret = register_device(
                    db,
                    device_id=args.device_id,
                    family=args.family,
                    display_name=args.name,
                    deployment_type=args.deployment_type,
                    allow_reprovisioning=args.allow_reprovisioning,
                )
            except (RegistryError, ValueError) as exc:
                print(f"ERROR: {exc}", file=sys.stderr)
                return 2
            print_secret(device.device_id, secret)
            return 0

        if args.command == "list":
            devices = db.scalars(select(Device).order_by(Device.device_id)).all()
            if not devices:
                print("No registered devices.")
                return 0
            header = f"{'DEVICE_ID':<28} {'FAMILY':<18} {'TYPE':<10} {'STATUS':<14} {'MQTT':<8} {'LAST ACTIVITY'}"
            print(header)
            print("-" * len(header))
            for device in devices:
                mqtt_state = "online" if device.online else "offline"
                print(
                    f"{device.device_id:<28} {device.family:<18} {device.deployment_type:<10} {device.lifecycle_status:<14} "
                    f"{mqtt_state:<8} {isoformat_utc(device.last_seen) or '-'}"
                )
            return 0

        device = db.get(Device, args.device_id)
        if device is None:
            print(f"ERROR: device not found: {args.device_id}", file=sys.stderr)
            return 2

        if args.command == "reset":
            had_certificate = bool(device.certificate_serial)
            secret = reset_bootstrap_secret(db, device)
            if had_certificate:
                MqttService(get_settings()).evict_device_sync(device.device_id)
            print_secret(device.device_id, secret)
            print("CRL updated and previous MQTT session evicted.")
            return 0

        if args.command == "revoke":
            if not device.certificate_serial:
                print("ERROR: device does not have an operational certificate", file=sys.stderr)
                return 2
            revoke_current_certificate(db, device)
            MqttService(get_settings()).evict_device_sync(device.device_id)
            print(f"Certificate for {device.device_id} added to the CRL.")
            print("CRL reloaded automatically and MQTT session evicted.")
            return 0

        if args.command in {"enable", "disable"}:
            device.enabled = args.command == "enable"
            db.add(device)
            db.commit()
            print(f"{device.device_id}: enabled={device.enabled}")
            return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
