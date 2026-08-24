#!/usr/bin/env python3
"""One-time local installation for the Secure IoT Device Platform.

The installer intentionally creates installation-specific state only on the
operator's machine. None of the generated credentials, PKI material, Wi-Fi
settings, databases, logs, or firmware build caches belong in Git.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from network_config import describe_candidates, select_wifi_ipv4
from start_platform import (
    DEFAULT_ENV_FILE,
    PROJECT_ROOT,
    configure_iot_wifi_on_startup,
    ensure_factory_dependencies,
    ensure_initialized,
    parse_env_lines,
    synchronize_network_if_enabled,
)


def _run(command: list[str], *, label: str, capture: bool = False) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        cwd=str(PROJECT_ROOT),
        check=False,
        shell=False,
        text=True,
        capture_output=capture,
    )
    if completed.returncode != 0:
        detail = ""
        if capture:
            detail = (completed.stderr or completed.stdout or "").strip()
        suffix = f": {detail}" if detail else ""
        raise RuntimeError(f"{label} failed with code {completed.returncode}{suffix}")
    return completed


def check_python() -> None:
    if sys.version_info < (3, 10):
        raise RuntimeError(
            f"Python 3.10 or newer is required for host tools; found {sys.version.split()[0]}"
        )
    print(f"[CHECK] Python {sys.version.split()[0]}")


def check_docker() -> None:
    docker = shutil.which("docker")
    if not docker:
        raise RuntimeError(
            "Docker was not found in PATH. Install Docker Desktop (Windows/macOS) or Docker Engine "
            "with the Compose plugin (Linux), start it, and run the installer again."
        )

    version = _run([docker, "--version"], label="Docker CLI check", capture=True)
    print(f"[CHECK] {version.stdout.strip()}")

    compose = _run([docker, "compose", "version"], label="Docker Compose check", capture=True)
    print(f"[CHECK] {compose.stdout.strip()}")

    _run([docker, "info"], label="Docker daemon check", capture=True)
    print("[CHECK] Docker daemon is reachable")


def check_wifi() -> str:
    selected, candidates = select_wifi_ipv4()
    print(f"[CHECK] Active physical Wi-Fi IPv4 candidates: {describe_candidates(candidates)}")
    print(f"[CHECK] Device-facing host address: {selected}")
    return selected


def validate_compose() -> None:
    _run(["docker", "compose", "config", "--quiet"], label="Docker Compose configuration check")
    print("[CHECK] docker-compose.yml is valid")


def build_images() -> None:
    print("[INSTALL] Building platform container images. The first build may take a few minutes.")
    _run(
        ["docker", "compose", "--profile", "tools", "build", "broker", "time-service", "api", "tools", "simulator-manager"],
        label="Docker image build",
    )
    print("[INSTALL] Container images built successfully")


def show_result(env_file: Path) -> None:
    _, env = parse_env_lines(env_file)
    host = env.get("API_PUBLIC_HOST", "<host-wifi-ip>")
    port = env.get("API_PUBLIC_PORT", "8443")
    mqtt_port = env.get("MQTT_PUBLIC_PORT", "8883")
    time_port = env.get("TIME_PUBLIC_PORT", "8091")
    username = env.get("DASHBOARD_USERNAME", "admin")
    password = env.get("DASHBOARD_PASSWORD", "<generated>")

    print()
    print("============================================================")
    print(" Secure IoT Device Platform - installation complete")
    print("============================================================")
    print(f"Device-facing host : {host}")
    print(f"Dashboard URL      : https://{host}:{port}")
    print(f"Dashboard user     : {username}")
    print(f"Dashboard password : {password}")
    print(f"MQTT/mTLS          : {host}:{mqtt_port}")
    print(f"Signed local time  : http://{host}:{time_port}")
    print()
    print("Installation-specific secrets were generated locally and are ignored by Git.")
    print("Run start-platform.bat (Windows) or ./start-platform.sh (Linux) to start the platform.")
    print("Run stop-platform.bat (Windows) or ./stop-platform.sh (Linux) to stop it.")
    print("============================================================")


def main() -> int:
    parser = argparse.ArgumentParser(description="Install the Secure IoT Device Platform locally")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="Initialize local state without pre-building all Docker images",
    )
    args = parser.parse_args()

    try:
        print("============================================================")
        print(" Secure IoT Device Platform")
        print(" Clean local installation")
        print("============================================================")
        print()

        check_python()
        check_docker()
        check_wifi()
        validate_compose()

        ensure_factory_dependencies()
        ensure_initialized(args.env_file)
        synchronize_network_if_enabled(args.env_file)
        configure_iot_wifi_on_startup(args.env_file)

        if not args.skip_build:
            build_images()

        show_result(args.env_file)
        return 0
    except (OSError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
