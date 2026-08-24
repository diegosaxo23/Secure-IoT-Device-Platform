#!/usr/bin/env python3
"""Provision an ESP32 that already contains the common bootstrap firmware.

This is the legacy/maintenance manufacturing path. It does not compile, erase,
or flash firmware. It waits for the firmware to announce ``FACTORY_READY``,
registers the physical unit with the server, receives the one-time bootstrap
secret, and transfers the identity to ESP32 NVS over the serial factory link.

The bootstrap secret is never printed and is never deliberately written to a
file or log by this script.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import serial  # type: ignore[import-not-found]
    import serial.tools.list_ports  # type: ignore[import-not-found]
except ImportError as exc:  # pragma: no cover - dependency message
    raise SystemExit(
        "pyserial is required. Install the factory dependencies with: "
        "python -m pip install -r scripts/requirements-factory.txt"
    ) from exc


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"
DEFAULT_CA_FILE = PROJECT_ROOT / "pki" / "ca" / "ca.crt"
FACTORY_PROTOCOL = "FACTORY-SERIAL-V1"


class FactoryProvisioningError(RuntimeError):
    """Controlled error raised by the factory provisioning station."""


@dataclass(frozen=True)
class ReadyIdentity:
    device_id: str
    family: str


def parse_env(path: Path) -> dict[str, str]:
    """Read a simple KEY=VALUE environment file without exporting values."""
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key] = value
    return values


def resolve_api_url(explicit: str | None, env: dict[str, str]) -> str:
    """Resolve the externally reachable HTTPS API URL used by the ESP32."""
    if explicit:
        return explicit.rstrip("/")

    host = env.get("API_PUBLIC_HOST", "").strip()
    port = env.get("API_PUBLIC_PORT", "8443").strip()
    if not host or host in {"127.0.0.1", "localhost"}:
        raise FactoryProvisioningError(
            "Provide --api-url with the server LAN IP address or DNS name. "
            "An ESP32 cannot use 127.0.0.1 because that address refers to itself."
        )
    return f"https://{host}:{port}"


def validate_api_url(api_url: str) -> None:
    parsed = urllib.parse.urlsplit(api_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise FactoryProvisioningError("--api-url must start with https:// and contain a host")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise FactoryProvisioningError(
            "--api-url must contain only the base URL, without a path, query, or fragment"
        )


def make_ssl_context(ca_file: Path) -> ssl.SSLContext:
    if not ca_file.is_file():
        raise FactoryProvisioningError(f"Server CA file does not exist: {ca_file}")
    return ssl.create_default_context(cafile=str(ca_file))


def basic_auth_header(username: str, password: str) -> str:
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def api_post(
    *,
    api_url: str,
    path: str,
    payload: dict[str, Any] | None,
    username: str,
    password: str,
    ssl_context: ssl.SSLContext,
    timeout: float,
) -> tuple[int, dict[str, Any]]:
    """POST JSON to an authenticated administration endpoint."""
    body = b"" if payload is None else json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")

    request = urllib.request.Request(
        f"{api_url}{path}",
        data=body,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": basic_auth_header(username, password),
        },
    )

    try:
        with urllib.request.urlopen(request, context=ssl_context, timeout=timeout) as response:
            response_body = response.read().decode("utf-8")
            parsed = json.loads(response_body) if response_body else {}
            return response.status, parsed
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            detail: Any = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            detail = {"detail": raw}
        return exc.code, detail
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise FactoryProvisioningError(f"Could not contact the API: {reason}") from exc
    except TimeoutError as exc:
        raise FactoryProvisioningError("The API request timed out") from exc


def extract_detail(response: dict[str, Any]) -> str:
    detail = response.get("detail")
    if isinstance(detail, str):
        return detail
    if detail is not None:
        return json.dumps(detail, ensure_ascii=False)
    return json.dumps(response, ensure_ascii=False)


def register_or_reset_device(
    *,
    api_url: str,
    identity: ReadyIdentity,
    display_name: str | None,
    reset_existing: bool,
    username: str,
    password: str,
    ssl_context: ssl.SSLContext,
    timeout: float,
) -> str:
    """Register the unit and return its one-time bootstrap secret in memory."""
    status, response = api_post(
        api_url=api_url,
        path="/api/v1/admin/devices",
        payload={
            "device_id": identity.device_id,
            "family": identity.family,
            "display_name": display_name,
            "deployment_type": "physical",
            "allow_reprovisioning": False,
        },
        username=username,
        password=password,
        ssl_context=ssl_context,
        timeout=timeout,
    )

    if status == 201:
        secret = response.get("bootstrap_secret")
        if not isinstance(secret, str) or len(secret) < 40:
            raise FactoryProvisioningError("The API did not return a valid bootstrap secret")
        print(f"[API] Device registered: {identity.device_id}")
        return secret

    if status == 409 and reset_existing:
        quoted_device_id = urllib.parse.quote(identity.device_id, safe="")
        reset_status, reset_response = api_post(
            api_url=api_url,
            path=f"/api/v1/admin/devices/{quoted_device_id}/reset-bootstrap",
            payload=None,
            username=username,
            password=password,
            ssl_context=ssl_context,
            timeout=timeout,
        )
        if reset_status != 200:
            raise FactoryProvisioningError(
                f"Could not regenerate the bootstrap secret (HTTP {reset_status}): "
                f"{extract_detail(reset_response)}"
            )
        secret = reset_response.get("bootstrap_secret")
        if not isinstance(secret, str) or len(secret) < 40:
            raise FactoryProvisioningError("The API did not return a valid replacement secret")
        print(f"[API] Bootstrap secret rotated for {identity.device_id}")
        print("[API] The previous operational certificate was revoked automatically.")
        return secret

    if status == 409:
        raise FactoryProvisioningError(
            "The device is already registered and its original bootstrap secret cannot be "
            "recovered. Run again with --reset-existing only when you intentionally want to "
            "invalidate the previous operational identity and create a new bootstrap secret."
        )

    raise FactoryProvisioningError(
        f"The API rejected device registration (HTTP {status}): {extract_detail(response)}"
    )


def parse_ready_line(line: str) -> ReadyIdentity | None:
    """Parse a FACTORY_READY announcement from the ESP32."""
    prefix = "FACTORY_READY "
    if not line.startswith(prefix):
        return None
    try:
        document = json.loads(line[len(prefix) :])
    except json.JSONDecodeError as exc:
        raise FactoryProvisioningError(f"FACTORY_READY contains invalid JSON: {exc}") from exc

    if document.get("protocol") != FACTORY_PROTOCOL:
        raise FactoryProvisioningError("Unsupported serial factory protocol version")

    device_id = document.get("device_id")
    family = document.get("family")
    if not isinstance(device_id, str) or len(device_id) < 3:
        raise FactoryProvisioningError("The ESP32 announced an invalid device_id")
    if not isinstance(family, str) or not family:
        raise FactoryProvisioningError("The ESP32 announced an invalid product family")
    return ReadyIdentity(device_id=device_id, family=family)


def wait_for_ready(port: "serial.Serial", timeout: float) -> ReadyIdentity:
    deadline = time.monotonic() + timeout
    print("[SERIAL] Waiting for FACTORY_READY from the ESP32...")
    while time.monotonic() < deadline:
        raw = port.readline()
        if not raw:
            continue
        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        print(f"[ESP32] {line}")
        identity = parse_ready_line(line)
        if identity is not None:
            return identity
    raise FactoryProvisioningError(
        "FACTORY_READY was not received. Check the port, baud rate, and verify that the "
        "the selected device firmware is loaded and has no factory identity stored."
    )


def build_factory_command(*, identity: ReadyIdentity, bootstrap_secret: str) -> str:
    document = {
        "command": "set_identity",
        "device_id": identity.device_id,
        "bootstrap_secret": bootstrap_secret,
    }
    return json.dumps(document, ensure_ascii=False, separators=(",", ":"))


def send_factory_data(
    port: "serial.Serial",
    *,
    factory_command: str,
    expected_device_id: str,
    timeout: float,
) -> None:
    command = (factory_command + "\n").encode("ascii")
    print("[SERIAL] Transferring the initial identity to the ESP32...")
    port.write(command)
    port.flush()

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        raw = port.readline()
        if not raw:
            continue
        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        print(f"[ESP32] {line}")
        if line == f"FACTORY_OK {expected_device_id}":
            return
        if line.startswith("FACTORY_ERROR "):
            raise FactoryProvisioningError(line[len("FACTORY_ERROR ") :])

    raise FactoryProvisioningError("The ESP32 did not confirm that the identity was stored")


def detected_ports() -> list[Any]:
    return list(serial.tools.list_ports.comports())


def list_ports() -> str:
    found = detected_ports()
    if not found:
        return "No serial ports were detected."
    return "\n".join(f"  {item.device}: {item.description}" for item in found)


def select_port(explicit: str | None, *, non_interactive: bool = False) -> str:
    if explicit:
        return explicit
    found = detected_ports()
    if not found:
        raise FactoryProvisioningError("No serial ports were detected")
    if len(found) == 1 and not non_interactive:
        selected = str(found[0].device)
        print(f"[SELECT] Automatically selected port: {selected} ({found[0].description})")
        return selected
    if non_interactive or not sys.stdin.isatty():
        raise FactoryProvisioningError("Provide --port in non-interactive mode")

    print("\nSelect serial port:")
    for index, item in enumerate(found, start=1):
        print(f"  {index}. {item.device} - {item.description}")
    while True:
        choice = input("Serial port: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(found):
            return str(found[int(choice) - 1].device)
        print("Invalid selection.")


def provision_device(
    *,
    serial_port: str,
    baud: int,
    api_url: str,
    ca_file: Path,
    username: str,
    password: str,
    display_name: str | None,
    reset_existing: bool,
    serial_timeout: float,
    api_timeout: float,
    expected_family: str | None = None,
    observe_seconds: float = 30.0,
    require_operational_ready: bool = False,
) -> ReadyIdentity:
    """Run the serial identity injection and server registration flow."""
    validate_api_url(api_url)
    ssl_context = make_ssl_context(ca_file)
    print(f"[CONFIG] API: {api_url}")
    print(f"[CONFIG] CA:  {ca_file}")
    print(f"[SERIAL] Opening {serial_port} at {baud} baud")

    # Opening the USB-UART port resets many ESP32 development boards. Give the
    # bootloader a short period to hand control to the application firmware.
    with serial.Serial(
        port=serial_port,
        baudrate=baud,
        timeout=0.5,
        write_timeout=5.0,
    ) as port:
        time.sleep(1.5)
        port.reset_input_buffer()
        identity = wait_for_ready(port, serial_timeout)
        print("[PROGRESS] FACTORY_READY received from the ESP32.")
        if expected_family is not None and identity.family != expected_family:
            raise FactoryProvisioningError(
                f"The firmware announced family {identity.family!r}, but {expected_family!r} "
                "was selected. No bootstrap secret will be sent."
            )

        print(f"[FACTORY] device_id={identity.device_id}")
        print(f"[FACTORY] family={identity.family}")
        print("[PROGRESS] Registering physical device and obtaining a one-time bootstrap secret.")

        bootstrap_secret = register_or_reset_device(
            api_url=api_url,
            identity=identity,
            display_name=display_name,
            reset_existing=reset_existing,
            username=username,
            password=password,
            ssl_context=ssl_context,
            timeout=api_timeout,
        )
        factory_command = build_factory_command(
            identity=identity,
            bootstrap_secret=bootstrap_secret,
        )
        # Remove the extra reference as soon as possible. Python cannot provide
        # guaranteed secure RAM erasure, but the value is never logged or persisted.
        bootstrap_secret = ""
        print("[PROGRESS] Injecting bootstrap identity into ESP32 NVS.")
        send_factory_data(
            port,
            factory_command=factory_command,
            expected_device_id=identity.device_id,
            timeout=serial_timeout,
        )
        factory_command = ""

        print("[OK] Initial identity stored and locked in NVS.")
        print("[PROGRESS] Identity stored. Waiting for signed local time, HMAC bootstrap, P-256 CSR, X.509 enrollment, and MQTT/mTLS.")
        print("[OK] The ESP32 will continue with Wi-Fi, signed local clock setup, HTTPS, CSR, X.509 enrollment, and MQTT/mTLS.")
        operational_ready = False
        failure_hint: str | None = None
        if observe_seconds > 0:
            print("[INFO] First-bootstrap serial output:")
            observe_until = time.monotonic() + observe_seconds
            while time.monotonic() < observe_until:
                raw = port.readline()
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="replace").strip()
                if line:
                    print(f"[ESP32] {line}")

                if "[MQTT] Connected." in line or "[MQTT] Connected. Subscribed" in line:
                    operational_ready = True
                    print("[VERIFY] MQTT/mTLS operational connection confirmed by the ESP32.")
                    break

                if "NO_AP_FOUND" in line:
                    failure_hint = "Wi-Fi network not found (NO_AP_FOUND). Verify SSID and use a 2.4 GHz network."
                elif "[WIFI] Connection failed" in line:
                    failure_hint = "Wi-Fi connection failed. Verify SSID/password and 2.4 GHz coverage."
                elif "[TIME] Local-time service failed" in line or "[TIME] Could not initialize" in line:
                    failure_hint = "Signed local-time service is unreachable; verify TCP 8091 and that the PC/device share the IoT Wi-Fi."
                elif "[TIME] Invalid" in line or "[TIME] Signed local-time response failed" in line or "signed-local-time" in line:
                    failure_hint = "Signed local-time verification failed; check the installation time public key and rebuild cache."
                elif "[BOOTSTRAP] Challenge rejected" in line:
                    failure_hint = "The bootstrap challenge was rejected by the API."
                elif "[BOOTSTRAP] Enrollment rejected" in line:
                    failure_hint = "X.509 enrollment was rejected by the API."
                elif "[MQTT] Connection rejected" in line or "[MQTT] TLS error" in line:
                    failure_hint = "MQTT/mTLS connection was rejected. Check broker certificate, CRL, and ACL state."
                elif "[FATAL]" in line:
                    failure_hint = line

        if require_operational_ready and not operational_ready:
            detail = failure_hint or (
                f"The device did not confirm an MQTT/mTLS connection within {observe_seconds:.0f} seconds."
            )
            raise FactoryProvisioningError(
                "DEVICE NOT READY: " + detail + " The firmware and factory identity may be stored, "
                "but end-to-end bootstrap has not been verified."
            )
    return identity


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Provision an ESP32 that already contains the common firmware. "
            "This command does not compile or flash firmware."
        )
    )
    parser.add_argument("--port", help="Serial port, for example COM5 or /dev/ttyUSB0")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--api-url", help="Server LAN URL, for example https://192.168.50.10:8443")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--ca-file", type=Path, default=DEFAULT_CA_FILE)
    parser.add_argument("--username", help="Administrator username; defaults to .env")
    parser.add_argument("--password", help="Administrator password; defaults to .env")
    parser.add_argument("--display-name", default=None)
    parser.add_argument(
        "--reset-existing",
        action="store_true",
        help=(
            "Rotate the bootstrap secret if the device already exists. This revokes the "
            "previous operational certificate and disconnects its MQTT session."
        ),
    )
    parser.add_argument("--serial-timeout", type=float, default=90.0)
    parser.add_argument("--api-timeout", type=float, default=15.0)
    parser.add_argument("--observe-seconds", type=float, default=30.0)
    parser.add_argument(
        "--list-ports",
        action="store_true",
        help="List available serial ports and exit.",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Fail instead of prompting when required options are missing.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.list_ports:
        print(list_ports())
        return 0

    try:
        serial_port = select_port(args.port, non_interactive=args.non_interactive)
        env = parse_env(args.env_file)
        username = args.username or env.get("DASHBOARD_USERNAME") or os.getenv("DASHBOARD_USERNAME")
        password = args.password or env.get("DASHBOARD_PASSWORD") or os.getenv("DASHBOARD_PASSWORD")
        if not username or not password:
            raise FactoryProvisioningError(
                "DASHBOARD_USERNAME/DASHBOARD_PASSWORD were not found. Initialize the server "
                "or provide --username and --password."
            )
        api_url = resolve_api_url(args.api_url, env)
        provision_device(
            serial_port=serial_port,
            baud=args.baud,
            api_url=api_url,
            ca_file=args.ca_file,
            username=username,
            password=password,
            display_name=args.display_name,
            reset_existing=args.reset_existing,
            serial_timeout=args.serial_timeout,
            api_timeout=args.api_timeout,
            observe_seconds=args.observe_seconds,
        )
        return 0
    except (FactoryProvisioningError, serial.SerialException) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nCancelled by the user.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
