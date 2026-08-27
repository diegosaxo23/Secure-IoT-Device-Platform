#!/usr/bin/env python3
"""Host-side USB manufacturing service used by the dashboard.

The service runs outside Docker so it can access host COM/serial ports. It never
programs a board on startup. Programming begins only after an authenticated
/program request. Each job is executed in the background and its sanitized
console output is exposed through /job so the dashboard can display the same
progress that is visible when factory_program_esp32.py is run manually.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

try:
    import serial.tools.list_ports  # type: ignore[import-not-found]
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "pyserial is required. Start the platform with start-platform.bat/start-platform.sh "
        "or install scripts/requirements-factory.txt."
    ) from exc

import factory_program_esp32 as factory
import factory_provision_esp32 as provision

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"
ALLOWED_PROFILES = {profile.key for profile in factory.PRODUCT_PROFILES}
PROGRAM_LOCK = threading.Lock()
JOB_LOCK = threading.Lock()
MAX_BODY_BYTES = 8192
MAX_LOG_LINES = 1200

STAGES: dict[str, tuple[str, int]] = {
    "queued": ("Queued", 0),
    "dependencies": ("Checking dependencies", 8),
    "configuration": ("Preparing selected firmware", 15),
    "erase": ("Erasing ESP32 flash", 25),
    "build": ("Building selected firmware", 42),
    "upload": ("Uploading firmware", 58),
    "factory_ready": ("Waiting for FACTORY_READY", 68),
    "registration": ("Registering device", 75),
    "identity": ("Writing bootstrap identity to NVS", 82),
    "bootstrap": ("HMAC bootstrap and CSR enrollment", 90),
    "certificate": ("Issuing X.509 certificate", 95),
    "mqtt": ("Connecting MQTT/mTLS", 98),
    "complete": ("Device ready", 100),
    "failed": ("Programming failed", 100),
}

CURRENT_JOB: dict[str, Any] | None = None


class AgentConfig:
    def __init__(self, *, token: str, env_file: Path, timeout: int) -> None:
        self.token = token
        self.env_file = env_file
        self.timeout = timeout


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sanitize_output(text: str) -> str:
    """Redact secret-like fields as a defense-in-depth measure."""
    clean = re.sub(
        r'("bootstrap_secret"\s*:\s*")[^"]+(")',
        r'\1[REDACTED]\2',
        text,
        flags=re.IGNORECASE,
    )
    for name in ("bootstrap_secret", "MANUFACTURING_AGENT_TOKEN", "DASHBOARD_PASSWORD"):
        clean = re.sub(
            rf'({name}\s*=\s*)[^\s]+',
            r'\1[REDACTED]',
            clean,
            flags=re.IGNORECASE,
        )
    return clean


def serial_ports() -> list[dict[str, str]]:
    return [
        {
            "device": str(item.device),
            "description": str(item.description or ""),
            "hwid": str(item.hwid or ""),
        }
        for item in serial.tools.list_ports.comports()
    ]


def source_project_for(profile: str) -> str:
    selected = factory.normalize_profile(profile)
    return f"firmware/esp32/{selected.firmware_dirname}"


def _stage_from_line(line: str, current: str) -> str:
    lowered = line.lower()
    if "[dependency]" in lowered or "library manager:" in lowered or "tool manager:" in lowered or "installing" in lowered or "downloading" in lowered or "unpacking" in lowered:
        return "dependencies"
    if "[config]" in lowered or "[build] synchronized public ca" in lowered or "public ca prepared temporarily" in lowered:
        return "configuration"
    if "erasing esp32 flash" in lowered:
        return "erase"
    if "building selected firmware" in lowered or "building complete firmware" in lowered or "compiling" in lowered or "linking" in lowered:
        return "build"
    if "uploading firmware" in lowered or "writing at" in lowered:
        return "upload"
    if "waiting for factory_ready" in lowered or "factory_ready" in lowered:
        return "factory_ready"
    if "registering physical device" in lowered or "device registered:" in lowered or "bootstrap secret rotated" in lowered:
        return "registration"
    if "injecting bootstrap identity" in lowered or "transferring the initial identity" in lowered or "identity stored" in lowered:
        return "identity"
    if "hmac" in lowered or "bootstrap challenge" in lowered or "csr" in lowered:
        return "bootstrap"
    if "x.509" in lowered or "certificate issued" in lowered or "certificate stored" in lowered:
        return "certificate"
    if "mqtt/mtls" in lowered or "mqtt connected" in lowered or "mqtt" in lowered and "connected" in lowered:
        return "mqtt"
    if "device ready" in lowered:
        return "complete"
    return current


def _append_job_output(job_id: str, line: str) -> None:
    clean = sanitize_output(line.rstrip("\r\n"))
    if not clean:
        return
    with JOB_LOCK:
        global CURRENT_JOB
        if CURRENT_JOB is None or CURRENT_JOB.get("id") != job_id:
            return
        output = CURRENT_JOB.setdefault("lines", [])
        output.append(clean)
        if len(output) > MAX_LOG_LINES:
            del output[: len(output) - MAX_LOG_LINES]
        current_stage = str(CURRENT_JOB.get("stage", "queued"))
        stage = _stage_from_line(clean, current_stage)
        if STAGES.get(stage, ("", 0))[1] >= STAGES.get(current_stage, ("", 0))[1]:
            CURRENT_JOB["stage"] = stage
            CURRENT_JOB["stage_label"], CURRENT_JOB["progress"] = STAGES[stage]
        CURRENT_JOB["updated_at"] = utc_now_iso()


def _job_snapshot(include_output: bool = True) -> dict[str, Any] | None:
    with JOB_LOCK:
        if CURRENT_JOB is None:
            return None
        result = {key: value for key, value in CURRENT_JOB.items() if key != "lines"}
        if include_output:
            result["output"] = "\n".join(CURRENT_JOB.get("lines", []))
        return result


def _run_program_job(
    *,
    job_id: str,
    profile: str,
    port: str,
    reset_existing: bool,
    display_name: str | None,
    config: AgentConfig,
) -> None:
    try:
        command = [
            sys.executable,
            "-u",
            str(PROJECT_ROOT / "scripts" / "factory_program_esp32.py"),
            "--profile",
            profile,
            "--port",
            port,
            "--env-file",
            str(config.env_file),
            "--non-interactive",
        ]
        if reset_existing:
            command.append("--reset-existing")
        if display_name:
            command.extend(["--display-name", display_name[:128]])

        _append_job_output(job_id, f"[UI] Selected source project: {source_project_for(profile)}")
        _append_job_output(job_id, "[DEPENDENCY] Checking PlatformIO, framework, toolchain, and project libraries...")

        child_env = os.environ.copy()
        child_env["PYTHONUNBUFFERED"] = "1"
        child_env.setdefault("PYTHONUTF8", "1")
        process = subprocess.Popen(
            command,
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            shell=False,
            env=child_env,
        )

        deadline = time.monotonic() + config.timeout
        assert process.stdout is not None
        for line in iter(process.stdout.readline, ""):
            _append_job_output(job_id, line)
            if time.monotonic() > deadline:
                _append_job_output(job_id, "ERROR: Manufacturing operation timed out")
                process.kill()
                break
        process.stdout.close()
        try:
            returncode = process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            returncode = process.wait(timeout=5)

        with JOB_LOCK:
            global CURRENT_JOB
            if CURRENT_JOB is not None and CURRENT_JOB.get("id") == job_id:
                CURRENT_JOB["returncode"] = returncode
                CURRENT_JOB["ok"] = returncode == 0
                CURRENT_JOB["state"] = "success" if returncode == 0 else "failed"
                CURRENT_JOB["stage"] = "complete" if returncode == 0 else "failed"
                CURRENT_JOB["stage_label"], CURRENT_JOB["progress"] = STAGES[CURRENT_JOB["stage"]]
                CURRENT_JOB["finished_at"] = utc_now_iso()
                CURRENT_JOB["updated_at"] = CURRENT_JOB["finished_at"]
    except Exception as exc:  # pragma: no cover - final containment around host tools
        _append_job_output(job_id, f"ERROR: {exc}")
        with JOB_LOCK:
            if CURRENT_JOB is not None and CURRENT_JOB.get("id") == job_id:
                CURRENT_JOB["returncode"] = 2
                CURRENT_JOB["ok"] = False
                CURRENT_JOB["state"] = "failed"
                CURRENT_JOB["stage"] = "failed"
                CURRENT_JOB["stage_label"], CURRENT_JOB["progress"] = STAGES["failed"]
                CURRENT_JOB["finished_at"] = utc_now_iso()
                CURRENT_JOB["updated_at"] = CURRENT_JOB["finished_at"]
    finally:
        PROGRAM_LOCK.release()


def start_program_job(
    profile: str,
    port: str,
    reset_existing: bool,
    display_name: str | None,
    config: AgentConfig,
) -> dict[str, Any]:
    if profile not in ALLOWED_PROFILES:
        raise ValueError("Profile is not allowlisted")
    known_ports = {item["device"] for item in serial_ports()}
    if port not in known_ports:
        raise ValueError("Selected serial port is not currently available")
    if not PROGRAM_LOCK.acquire(blocking=False):
        raise RuntimeError("The manufacturing station is busy")

    job_id = uuid.uuid4().hex
    now = utc_now_iso()
    selected = factory.normalize_profile(profile)
    with JOB_LOCK:
        global CURRENT_JOB
        CURRENT_JOB = {
            "id": job_id,
            "state": "running",
            "stage": "queued",
            "stage_label": STAGES["queued"][0],
            "progress": STAGES["queued"][1],
            "profile": profile,
            "profile_label": selected.label,
            "port": port,
            "source_project": source_project_for(profile),
            "display_name": display_name,
            "reset_existing": reset_existing,
            "started_at": now,
            "updated_at": now,
            "finished_at": None,
            "returncode": None,
            "ok": None,
            "lines": [],
        }

    worker = threading.Thread(
        target=_run_program_job,
        kwargs={
            "job_id": job_id,
            "profile": profile,
            "port": port,
            "reset_existing": reset_existing,
            "display_name": display_name,
            "config": config,
        },
        name=f"manufacturing-{job_id[:8]}",
        daemon=True,
    )
    worker.start()
    snapshot = _job_snapshot(include_output=True)
    assert snapshot is not None
    return snapshot


class ManufacturingHandler(BaseHTTPRequestHandler):
    server_version = "IoTManufacturingAgent/1.1.1"

    @property
    def config(self) -> AgentConfig:
        return self.server.config  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: object) -> None:
        sys.stdout.write("[HTTP] " + format % args + "\n")
        sys.stdout.flush()

    def _authorized(self) -> bool:
        header = self.headers.get("Authorization", "")
        prefix = "Bearer "
        return header.startswith(prefix) and hmac.compare_digest(header[len(prefix) :], self.config.token)

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _require_authorization(self) -> bool:
        if self._authorized():
            return True
        self._send_json(HTTPStatus.UNAUTHORIZED, {"detail": "Unauthorized"})
        return False

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/ready":
            self._send_json(HTTPStatus.OK, {"status": "ready"})
            return
        if not self._require_authorization():
            return
        if self.path == "/health":
            self._send_json(
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "busy": PROGRAM_LOCK.locked(),
                    "enabled": True,
                    "profiles": sorted(ALLOWED_PROFILES),
                    "job": _job_snapshot(include_output=False),
                },
            )
            return
        if self.path == "/ports":
            self._send_json(HTTPStatus.OK, {"ports": serial_ports()})
            return
        if self.path == "/profiles":
            self._send_json(
                HTTPStatus.OK,
                {
                    "profiles": [
                        {
                            "key": item.key,
                            "family": item.family,
                            "label": item.label,
                            "source_project": f"firmware/esp32/{item.firmware_dirname}",
                        }
                        for item in factory.PRODUCT_PROFILES
                    ]
                },
            )
            return
        if self.path == "/job":
            self._send_json(HTTPStatus.OK, {"job": _job_snapshot(include_output=True)})
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"detail": "Not found"})

    def do_POST(self) -> None:  # noqa: N802
        if not self._require_authorization():
            return
        if self.path != "/program":
            self._send_json(HTTPStatus.NOT_FOUND, {"detail": "Not found"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"detail": "Invalid Content-Length"})
            return
        if length <= 0 or length > MAX_BODY_BYTES:
            self._send_json(HTTPStatus.BAD_REQUEST, {"detail": "Invalid request body size"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(HTTPStatus.BAD_REQUEST, {"detail": "Invalid JSON body"})
            return

        profile = payload.get("profile")
        port = payload.get("port")
        reset_existing = payload.get("reset_existing", False)
        display_name = payload.get("display_name")
        if not isinstance(profile, str) or profile not in ALLOWED_PROFILES:
            self._send_json(HTTPStatus.BAD_REQUEST, {"detail": "Invalid device profile"})
            return
        if not isinstance(port, str) or not port or len(port) > 128:
            self._send_json(HTTPStatus.BAD_REQUEST, {"detail": "Invalid serial port"})
            return
        if not isinstance(reset_existing, bool):
            self._send_json(HTTPStatus.BAD_REQUEST, {"detail": "reset_existing must be boolean"})
            return
        if display_name is not None and not isinstance(display_name, str):
            self._send_json(HTTPStatus.BAD_REQUEST, {"detail": "display_name must be a string"})
            return

        try:
            job = start_program_job(profile, port, reset_existing, display_name, self.config)
        except ValueError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"detail": str(exc)})
            return
        except RuntimeError as exc:
            self._send_json(HTTPStatus.CONFLICT, {"detail": str(exc)})
            return

        self._send_json(HTTPStatus.ACCEPTED, {"ok": True, "job": job})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the host-side USB Manufacturing Agent")
    parser.add_argument("--bind", default="0.0.0.0", help="Bind address visible to Docker Desktop")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--timeout", type=int, default=900)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    env = provision.parse_env(args.env_file)
    token = os.environ.get("MANUFACTURING_AGENT_TOKEN_RUNTIME", "").strip() or env.get("MANUFACTURING_AGENT_TOKEN", "").strip()
    if not token:
        print("ERROR: MANUFACTURING_AGENT_TOKEN is missing.", file=sys.stderr, flush=True)
        return 2

    config = AgentConfig(token=token, env_file=args.env_file, timeout=max(60, args.timeout))
    try:
        server = ThreadingHTTPServer((args.bind, args.port), ManufacturingHandler)
    except OSError as exc:
        print(f"ERROR: Could not bind Manufacturing Agent to {args.bind}:{args.port}: {exc}", file=sys.stderr, flush=True)
        return 2
    server.config = config  # type: ignore[attr-defined]
    print(f"[AGENT] Manufacturing Agent listening on {args.bind}:{args.port}", flush=True)
    print("[AGENT] Allowed profiles: cromaled, area_lz7, as7341", flush=True)
    print("[AGENT] Programming starts only on an explicit Program Device request.", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\n[AGENT] Stopping.", flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
