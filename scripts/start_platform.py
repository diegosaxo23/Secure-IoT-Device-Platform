#!/usr/bin/env python3
"""Start or stop the complete Secure IoT Device Platform.

The Manufacturing Agent runs on the host so it can access Windows COM ports or
Linux serial devices. Docker runs the broker, API/dashboard, signed local-time service, and Simulation
Manager. The launcher keeps both sides synchronized and deliberately rotates the
host-agent bearer token on every full start so a stale agent from an older copy
cannot remain authorized accidentally.
"""

from __future__ import annotations

import argparse
import getpass
import http.client
import importlib.util
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

from network_config import detect_active_wifi_ssid, describe_candidates, select_wifi_ipv4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"
AGENT_PORT = 8765
AGENT_PID_FILE = PROJECT_ROOT / "logs" / "manufacturing-agent.pid"
AGENT_LOG_FILE = PROJECT_ROOT / "logs" / "manufacturing-agent.log"
LEGACY_CONTROLLER_PID_FILE = PROJECT_ROOT / "logs" / "manufacturing-controller.pid"


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def read_pid(path: Path) -> int | None:
    try:
        value = int(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return None
    if process_alive(value):
        return value
    path.unlink(missing_ok=True)
    return None


def parse_env_lines(path: Path) -> tuple[list[str], dict[str, str]]:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    values: dict[str, str] = {}
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key.strip()] = value
    return lines, values


def set_env_values(path: Path, updates: dict[str, str]) -> None:
    lines, _ = parse_env_lines(path)
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
        output.append("# Local ESP32 manufacturing station")
        output.extend(f"{key}={value}" for key, value in remaining.items())
    path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")


def _select_host_wifi_address() -> str:
    selected, candidates = select_wifi_ipv4()
    print(f"[NETWORK] Active Wi-Fi IPv4 candidates: {describe_candidates(candidates)}")
    print(f"[NETWORK] Using active Wi-Fi IPv4 for ESP32/API/MQTT: {selected}")
    return selected


def ensure_initialized(env_file: Path) -> None:
    if env_file.exists():
        return

    existing_state = any(
        path.exists()
        for path in (
            PROJECT_ROOT / "pki" / "ca" / "ca.key",
            PROJECT_ROOT / "data" / "iot_device_platform.db",
        )
    )
    if existing_state:
        raise RuntimeError(
            ".env is missing but existing PKI/database state was found. Copy the previous .env "
            "into this project so dashboard credentials and encrypted bootstrap data are preserved."
        )

    docker = shutil.which("docker")
    if not docker:
        raise RuntimeError("Docker was not found in PATH and the project has not been initialized yet.")

    hostname = socket.gethostname() or "localhost"
    public_ip = _select_host_wifi_address()
    print("[INIT] .env was not found; initializing a fresh project automatically")
    print(f"[INIT] Device-facing address selected: {public_ip}")
    command = [
        docker,
        "compose",
        "--profile",
        "tools",
        "run",
        "--rm",
        "tools",
        "scripts/setup.py",
        "--hostname",
        hostname,
        "--ip",
        public_ip,
    ]
    completed = subprocess.run(command, cwd=str(PROJECT_ROOT), check=False, shell=False)
    if completed.returncode != 0 or not env_file.exists():
        raise RuntimeError("Automatic first-run initialization failed")
    print("[INIT] Fresh project initialization completed")


def synchronize_network_if_enabled(env_file: Path) -> str:
    _, env = parse_env_lines(env_file)
    automatic = env.get("AUTO_NETWORK_SYNC", "true").strip().lower() not in {"0", "false", "no", "off"}
    configured_ip = env.get("API_PUBLIC_HOST", "").strip()

    if not automatic:
        print(f"[NETWORK] Automatic Wi-Fi address synchronization is disabled; keeping {configured_ip}")
        return configured_ip

    selected = _select_host_wifi_address()

    # Always ask setup.py to validate the installed API/broker certificates.
    # This is intentionally done even when .env already contains the current IP:
    # v2.3.6 adds a legacy dNSName representation of the literal IP so
    # Arduino-ESP32 2.x / Mbed TLS 2.28.x can perform hostname verification.
    # setup.py is idempotent and only reissues service certificates when their
    # SAN/profile/key/issuer actually needs refreshing.
    docker = shutil.which("docker")
    if not docker:
        raise RuntimeError("Docker is required to synchronize service TLS certificates")
    hostname = socket.gethostname() or env.get("SERVER_HOSTNAME", "localhost") or "localhost"
    if selected == configured_ip and env.get("MQTT_PUBLIC_HOST", "").strip() == selected:
        print(f"[NETWORK] Validating service certificates for active Wi-Fi IPv4 {selected}")
    else:
        print(f"[NETWORK] Wi-Fi IPv4 changed from {configured_ip or '<unset>'} to {selected}; synchronizing certificates")
    command = [
        docker,
        "compose",
        "--profile",
        "tools",
        "run",
        "--rm",
        "tools",
        "scripts/setup.py",
        "--sync-network",
        "--hostname",
        hostname,
        "--ip",
        selected,
    ]
    completed = subprocess.run(command, cwd=str(PROJECT_ROOT), check=False, shell=False)
    if completed.returncode != 0:
        raise RuntimeError("Automatic Wi-Fi network synchronization failed")
    return selected


def configure_iot_wifi_on_startup(env_file: Path) -> None:
    """Ask for the Wi-Fi used by physical IoT devices on every interactive start.

    The active PC Wi-Fi SSID is offered as the first-run default when it can be
    detected. Pressing Enter keeps an existing configuration. Password input is
    hidden and stored only in .env; it is never printed by this launcher.
    """
    _, env = parse_env_lines(env_file)
    current_ssid = env.get("IOT_WIFI_SSID", "").strip()
    current_password = env.get("IOT_WIFI_PASSWORD", "")
    host_ssid = detect_active_wifi_ssid()

    if not sys.stdin.isatty():
        if not current_ssid:
            raise RuntimeError(
                "IOT_WIFI_SSID is not configured and startup is non-interactive. "
                "Run start-platform.bat interactively or configure .env first."
            )
        return

    print()
    print("[IOT WIFI] Configure the Wi-Fi network that the ESP32 devices will use.")
    if host_ssid:
        print(f"[IOT WIFI] PC is currently connected to: {host_ssid}")
    print("[IOT WIFI] For direct device access, use the same reachable Wi-Fi network as the PC.")

    if current_ssid:
        entered = input(f"IoT Wi-Fi SSID [{current_ssid}] (Enter to keep): ").strip()
        if not entered:
            print(f"[IOT WIFI] Keeping configured SSID: {current_ssid}")
            if host_ssid and current_ssid != host_ssid:
                print(f"[WARN] Configured IoT SSID '{current_ssid}' differs from current PC Wi-Fi '{host_ssid}'.")
            return
        ssid = entered
    else:
        default_ssid = host_ssid or ""
        if default_ssid:
            entered = input(f"IoT Wi-Fi SSID [{default_ssid}]: ").strip()
            ssid = entered or default_ssid
        else:
            ssid = input("IoT Wi-Fi SSID (2.4 GHz): ").strip()
        if not ssid:
            raise RuntimeError("IoT Wi-Fi SSID cannot be empty")

    password = getpass.getpass("IoT Wi-Fi password: ")
    if "\n" in ssid or "\r" in ssid or "\n" in password or "\r" in password:
        raise RuntimeError("Wi-Fi SSID/password cannot contain line breaks")
    if not password and current_ssid == ssid and current_password:
        password = current_password

    set_env_values(
        env_file,
        {
            "IOT_WIFI_SSID": ssid,
            "IOT_WIFI_PASSWORD": password,
        },
    )
    print(f"[IOT WIFI] IoT device Wi-Fi configured: {ssid}")
    if host_ssid and ssid != host_ssid:
        print(f"[WARN] IoT SSID '{ssid}' differs from current PC Wi-Fi '{host_ssid}'. Ensure routing exists.")
    print("[IOT WIFI] Password stored only in .env and will be embedded only during firmware build.")


def configure_manufacturing(env_file: Path) -> str:
    if not env_file.exists():
        raise RuntimeError(".env does not exist")

    # Rotate this internal token on every full start. The host agent and Docker API
    # are then launched from the same value, which eliminates stale-token mismatches.
    agent_token = secrets.token_urlsafe(32)
    set_env_values(
        env_file,
        {
            "MANUFACTURING_ENABLED": "true",
            "MANUFACTURING_AGENT_URL": f"http://host.docker.internal:{AGENT_PORT}",
            "MANUFACTURING_AGENT_TOKEN": agent_token,
            "MANUFACTURING_AGENT_TIMEOUT_SECONDS": "900",
        },
    )
    print("[START] Manufacturing integration configured")
    return agent_token


def ensure_factory_dependencies() -> None:
    serial_available = importlib.util.find_spec("serial") is not None
    platformio_available = shutil.which("pio") is not None or shutil.which("platformio") is not None
    if not platformio_available:
        platformio_available = importlib.util.find_spec("platformio") is not None
    if serial_available and platformio_available:
        print("[START] Host manufacturing dependencies are available")
        return

    requirements = PROJECT_ROOT / "scripts" / "requirements-factory.txt"
    print("[START] Installing missing host manufacturing dependencies")
    completed = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", str(requirements)],
        cwd=str(PROJECT_ROOT),
        check=False,
        shell=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("Unable to install scripts/requirements-factory.txt")


def agent_port_open(timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", AGENT_PORT), timeout=timeout):
            return True
    except OSError:
        return False


def agent_health(agent_token: str, timeout: float = 1.0) -> dict[str, Any] | None:
    connection = http.client.HTTPConnection("127.0.0.1", AGENT_PORT, timeout=timeout)
    try:
        connection.request(
            "GET",
            "/health",
            headers={
                "Authorization": f"Bearer {agent_token}",
                "Accept": "application/json",
                "Connection": "close",
            },
        )
        response = connection.getresponse()
        raw = response.read()
        if response.status != 200:
            return None
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, TimeoutError, http.client.HTTPException, json.JSONDecodeError):
        return None
    finally:
        connection.close()
    return payload if isinstance(payload, dict) else None


def listener_pid(port: int) -> int | None:
    if os.name == "nt":
        powershell = shutil.which("powershell") or shutil.which("pwsh")
        if not powershell:
            return None
        script = (
            f"$c=Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue "
            "| Select-Object -First 1; if ($c) { $c.OwningProcess }"
        )
        completed = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
            check=False,
            shell=False,
            capture_output=True,
            text=True,
        )
        try:
            return int((completed.stdout or "").strip().splitlines()[0])
        except (ValueError, IndexError):
            return None

    lsof = shutil.which("lsof")
    if lsof:
        completed = subprocess.run(
            [lsof, "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            check=False,
            shell=False,
            capture_output=True,
            text=True,
        )
        try:
            return int((completed.stdout or "").strip().splitlines()[0])
        except (ValueError, IndexError):
            return None
    return None


def process_command_line(pid: int) -> str:
    if os.name == "nt":
        powershell = shutil.which("powershell") or shutil.which("pwsh")
        if not powershell:
            return ""
        script = (
            f"$p=Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\" -ErrorAction SilentlyContinue; "
            "if ($p) { $p.CommandLine }"
        )
        completed = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
            check=False,
            shell=False,
            capture_output=True,
            text=True,
        )
        return (completed.stdout or "").strip()
    try:
        return (Path("/proc") / str(pid) / "cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", errors="replace")
    except OSError:
        return ""


def is_manufacturing_agent_process(pid: int) -> bool:
    command_line = process_command_line(pid).lower().replace("\\", "/")
    return "manufacturing_agent.py" in command_line


def terminate_pid(pid: int) -> None:
    if pid <= 0:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False,
            shell=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def stop_stale_agent_listener() -> None:
    # First stop the agent tracked by this project copy.
    tracked = read_pid(AGENT_PID_FILE)
    if tracked is not None:
        terminate_pid(tracked)
        AGENT_PID_FILE.unlink(missing_ok=True)
        for _ in range(30):
            if not agent_port_open(timeout=0.1):
                break
            time.sleep(0.1)

    if not agent_port_open(timeout=0.2):
        return

    pid = listener_pid(AGENT_PORT)
    if pid is None:
        raise RuntimeError(
            f"Port {AGENT_PORT} is already in use and its owner could not be identified. "
            "Close the previous Manufacturing Agent or the program using that port."
        )
    if not is_manufacturing_agent_process(pid):
        raise RuntimeError(
            f"Port {AGENT_PORT} is already used by another application (PID {pid}). "
            "Manufacturing Agent was not allowed to terminate it."
        )

    print(f"[START] Stopping stale Manufacturing Agent on port {AGENT_PORT} (PID {pid})")
    terminate_pid(pid)
    for _ in range(50):
        if not agent_port_open(timeout=0.1):
            return
        time.sleep(0.1)
    raise RuntimeError(f"The stale Manufacturing Agent on port {AGENT_PORT} could not be stopped")


def start_agent(env_file: Path, agent_token: str) -> int:
    stop_stale_agent_listener()

    AGENT_PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    AGENT_LOG_FILE.write_text(
        "[LAUNCHER] Starting Manufacturing Agent with unbuffered output.\n",
        encoding="utf-8",
    )
    log_handle = AGENT_LOG_FILE.open("a", encoding="utf-8", buffering=1)
    command = [
        sys.executable,
        "-u",
        str(PROJECT_ROOT / "scripts" / "manufacturing_agent.py"),
        "--env-file",
        str(env_file),
        "--port",
        str(AGENT_PORT),
    ]
    child_env = os.environ.copy()
    child_env["PYTHONUNBUFFERED"] = "1"
    child_env.setdefault("PYTHONUTF8", "1")
    child_env["MANUFACTURING_AGENT_TOKEN_RUNTIME"] = agent_token
    kwargs: dict[str, object] = {
        "cwd": str(PROJECT_ROOT),
        "stdout": log_handle,
        "stderr": subprocess.STDOUT,
        "stdin": subprocess.DEVNULL,
        "env": child_env,
        "shell": False,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
    else:
        kwargs["start_new_session"] = True

    process = subprocess.Popen(command, **kwargs)  # type: ignore[arg-type]
    log_handle.close()
    AGENT_PID_FILE.write_text(f"{process.pid}\n", encoding="ascii")

    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        if process.poll() is not None:
            AGENT_PID_FILE.unlink(missing_ok=True)
            raise RuntimeError(f"Manufacturing Agent exited with code {process.returncode}. Check {AGENT_LOG_FILE}")
        if agent_port_open(timeout=0.2):
            break
        time.sleep(0.25)
    else:
        terminate_pid(process.pid)
        AGENT_PID_FILE.unlink(missing_ok=True)
        raise RuntimeError(f"Manufacturing Agent did not open port {AGENT_PORT}. Check {AGENT_LOG_FILE}")

    # This check uses the exact in-memory token passed to the child. A failure is
    # diagnostic only; Docker still starts and performs the end-to-end check later.
    health = agent_health(agent_token, timeout=2.0)
    if health is not None:
        print(f"[START] Manufacturing Agent ready on host port {AGENT_PORT}")
    else:
        print(f"[WARN] Manufacturing Agent is listening on port {AGENT_PORT}, but the host authenticated check did not answer")
    return process.pid


def docker_up() -> None:
    command = ["docker", "compose", "up", "-d", "--build", "--remove-orphans", "--force-recreate"]
    completed = subprocess.run(command, cwd=str(PROJECT_ROOT), check=False, shell=False)
    if completed.returncode != 0:
        raise RuntimeError("docker compose up failed")
    print("[START] Docker stack started: broker, signed local-time service, API/dashboard, and Simulation Manager")
    subprocess.run(["docker", "compose", "ps"], cwd=str(PROJECT_ROOT), check=False, shell=False)


def wait_for_signed_time_service(port: int, timeout_seconds: float = 30.0) -> None:
    url = f"http://127.0.0.1:{port}/health"
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2.0) as response:
                if response.status == 200:
                    response.read(4096)
                    print(f"[READY] Signed local-time service is healthy on port {port}")
                    return
        except Exception as exc:  # startup probe; detailed Docker logs remain available
            last_error = exc
        time.sleep(0.5)
    raise RuntimeError(f"signed local-time service did not become healthy at {url}: {last_error}")


def docker_down() -> int:
    docker = shutil.which("docker")
    if not docker:
        print("[WARN] Docker was not found in PATH; host services will still be stopped")
        return 0
    completed = subprocess.run(
        [docker, "compose", "down", "--remove-orphans"],
        cwd=str(PROJECT_ROOT),
        check=False,
        shell=False,
    )
    return completed.returncode


def verify_agent_auth_from_api_container() -> bool:
    probe = (
        "import json,os,urllib.request; "
        "u=os.environ['MANUFACTURING_AGENT_URL'].rstrip('/')+'/health'; "
        "t=os.environ['MANUFACTURING_AGENT_TOKEN']; "
        "r=urllib.request.Request(u,headers={'Authorization':'Bearer '+t,'Accept':'application/json'}); "
        "print(urllib.request.urlopen(r,timeout=5).read().decode())"
    )
    completed = subprocess.run(
        ["docker", "compose", "exec", "-T", "api", "python", "-c", probe],
        cwd=str(PROJECT_ROOT),
        check=False,
        shell=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return completed.returncode == 0


def stop_host_services() -> None:
    stopped: set[int] = set()
    tracked = read_pid(AGENT_PID_FILE)
    if tracked is not None:
        terminate_pid(tracked)
        stopped.add(tracked)
    AGENT_PID_FILE.unlink(missing_ok=True)

    pid = listener_pid(AGENT_PORT) if agent_port_open(timeout=0.2) else None
    if pid is not None and pid not in stopped and is_manufacturing_agent_process(pid):
        terminate_pid(pid)
        stopped.add(pid)

    legacy = read_pid(LEGACY_CONTROLLER_PID_FILE)
    if legacy is not None:
        terminate_pid(legacy)
        stopped.add(legacy)
    LEGACY_CONTROLLER_PID_FILE.unlink(missing_ok=True)

    if stopped:
        print("[STOP] Manufacturing host process stopped")
    else:
        print("[STOP] Manufacturing host process was not running")


def stop_platform() -> int:
    print("[STOP] Stopping Docker stack")
    docker_rc = docker_down()
    stop_host_services()
    if docker_rc == 0:
        print("[STOP] Platform stopped")
    else:
        print(f"[WARN] docker compose down exited with code {docker_rc}")
    return docker_rc


def main() -> int:
    parser = argparse.ArgumentParser(description="Start or stop the complete Secure IoT Device Platform")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--stop-platform", action="store_true", help="Stop Docker and host Manufacturing services")
    parser.add_argument("--stop-host-services", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--stop-agent", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    try:
        if args.stop_platform:
            return stop_platform()
        if args.stop_host_services or args.stop_agent:
            stop_host_services()
            return 0

        ensure_initialized(args.env_file)
        public_ip = synchronize_network_if_enabled(args.env_file)
        configure_iot_wifi_on_startup(args.env_file)
        agent_token = configure_manufacturing(args.env_file)

        agent_available = False
        try:
            ensure_factory_dependencies()
            start_agent(args.env_file, agent_token)
            agent_available = agent_port_open(timeout=1.0)
        except (OSError, RuntimeError) as exc:
            print(f"[WARN] Manufacturing Agent startup failed: {exc}")
            print("[WARN] Docker startup will continue; programming will be unavailable until the host issue is fixed")

        docker_up()
        _, runtime_env = parse_env_lines(args.env_file)
        time_port = int(runtime_env.get("TIME_PUBLIC_PORT", "8091") or "8091")
        wait_for_signed_time_service(time_port)

        if agent_available and verify_agent_auth_from_api_container():
            print("[READY] Docker API authenticated successfully to the Manufacturing Agent")
        elif agent_available:
            print("[WARN] Docker is running, but API-to-agent authentication failed. Check logs/manufacturing-agent.log")
        else:
            print(f"[WARN] Manufacturing Agent is unavailable. Log: {AGENT_LOG_FILE}")

        print("[READY] Platform is ready")
        print("[READY] Dashboard (local): https://localhost:8443")
        if public_ip:
            print(f"[READY] Dashboard (LAN):   https://{public_ip}:8443")
            print(f"[READY] MQTT/mTLS:         {public_ip}:8883")
            print(f"[READY] Signed local time: http://{public_ip}:{time_port}")
        return 0
    except (OSError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
