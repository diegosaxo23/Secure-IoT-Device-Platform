from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


STATE_ROOT = Path(os.getenv("SIMULATED_STATE_DIR", "/simulated_state"))
API_URL = os.getenv("PLATFORM_API_URL", "https://api:8443")
CA_PATH = os.getenv("PLATFORM_CA_PATH", "/pki/ca/ca.crt")
MQTT_HOST = os.getenv("PLATFORM_MQTT_HOST", "broker")
ADMIN_USER = os.getenv("DASHBOARD_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")
SCRIPT = Path(__file__).resolve().parent / "simulated_device.py"

FAMILY_CONFIG = {
    "cromaled": {"family": "CromaLED", "prefix": "CLED-SIM"},
    "area_lz7": {"family": "AREA LZ7", "prefix": "AREA-SIM"},
    "as7341": {"family": "AS7341", "prefix": "AS7341-SIM"},
}


class StartRequest(BaseModel):
    family: Literal["cromaled", "area_lz7", "as7341"]
    count: int = Field(default=1, ge=1, le=200)
    interval: float = Field(default=5.0, ge=0.5, le=300.0)


class ManagedProcess:
    def __init__(self, *, device_id: str, family: str, process: subprocess.Popen[str], log_handle) -> None:
        self.device_id = device_id
        self.family = family
        self.process = process
        self.log_handle = log_handle

    @property
    def running(self) -> bool:
        return self.process.poll() is None

    def status(self) -> str:
        code = self.process.poll()
        return "running" if code is None else f"exited({code})"

    def stop(self) -> None:
        if self.running:
            self.process.terminate()
            try:
                self.process.wait(timeout=7)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        try:
            self.log_handle.close()
        except Exception:
            pass


app = FastAPI(title="IoT Device Platform Simulation Manager", version="1.0.0")
lock = threading.Lock()
instances: dict[str, ManagedProcess] = {}
simulation_enabled = False


def _next_ids(prefix: str, count: int) -> list[str]:
    """Reuse stopped persistent identities first, then create new ones."""
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    pattern = re.compile(rf"^{re.escape(prefix)}-(\d{{4}})$")
    existing: set[int] = set()
    for child in STATE_ROOT.iterdir():
        match = pattern.match(child.name)
        if match:
            existing.add(int(match.group(1)))
    running: set[int] = set()
    for device_id, item in instances.items():
        match = pattern.match(device_id)
        if match and item.running:
            running.add(int(match.group(1)))

    result_numbers = sorted(existing - running)[:count]
    candidate = 1
    while len(result_numbers) < count:
        if candidate not in existing and candidate not in running:
            result_numbers.append(candidate)
            existing.add(candidate)
        candidate += 1
    return [f"{prefix}-{number:04d}" for number in result_numbers]


def _start_instance(device_id: str, family: str, interval: float) -> ManagedProcess:
    device_dir = STATE_ROOT / device_id
    device_dir.mkdir(parents=True, exist_ok=True)
    log_handle = (device_dir / "simulator.log").open("a", encoding="utf-8", buffering=1)
    command = [
        sys.executable,
        str(SCRIPT),
        "--device-id",
        device_id,
        "--family",
        family,
        "--api-url",
        API_URL,
        "--bootstrap-ca",
        CA_PATH,
        "--state-dir",
        str(STATE_ROOT),
        "--auto-register",
        "--admin-username",
        ADMIN_USER,
        "--admin-password",
        ADMIN_PASSWORD,
        "--mqtt-host",
        MQTT_HOST,
        "--interval",
        str(interval),
    ]
    process = subprocess.Popen(
        command,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
        env=os.environ.copy(),
        shell=False,
    )
    return ManagedProcess(device_id=device_id, family=family, process=process, log_handle=log_handle)


def _stop_all_locked() -> int:
    items = list(instances.values())
    stopped = 0
    for item in items:
        if item.running:
            item.stop()
            stopped += 1
    return stopped


def _purge_simulated_state_locked() -> int:
    """Delete only simulator-generated identity directories, never repository metadata."""
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    prefixes = tuple(config["prefix"] + "-" for config in FAMILY_CONFIG.values())
    removed = 0
    for child in list(STATE_ROOT.iterdir()):
        if child.is_dir() and child.name.startswith(prefixes):
            shutil.rmtree(child, ignore_errors=False)
            removed += 1
    return removed


@app.get("/health")
def health() -> dict[str, object]:
    with lock:
        running = sum(1 for item in instances.values() if item.running)
        enabled = simulation_enabled
    return {"status": "ok", "enabled": enabled, "running": running}


@app.get("/status")
def status() -> dict[str, object]:
    with lock:
        rows = [
            {"device_id": item.device_id, "family": item.family, "running": item.running, "status": item.status()}
            for item in sorted(instances.values(), key=lambda value: value.device_id)
        ]
        enabled = simulation_enabled
    return {
        "available": True,
        "enabled": enabled,
        "running": sum(1 for row in rows if row["running"]),
        "instances": rows,
    }


@app.post("/control/enable")
def enable_simulation() -> dict[str, object]:
    global simulation_enabled
    if not ADMIN_PASSWORD:
        raise HTTPException(status_code=500, detail="DASHBOARD_PASSWORD is not configured")
    with lock:
        simulation_enabled = True
    return {"enabled": True, "message": "Simulation enabled. No instances are started automatically."}


@app.post("/control/disable")
def disable_simulation() -> dict[str, object]:
    global simulation_enabled
    with lock:
        simulation_enabled = False
        stopped = _stop_all_locked()
    return {"enabled": False, "stopped": stopped, "message": "Simulation disabled and all instances stopped."}


@app.post("/control/reset")
def reset_simulation() -> dict[str, object]:
    global simulation_enabled
    with lock:
        simulation_enabled = False
        stopped = _stop_all_locked()
        removed = _purge_simulated_state_locked()
        instances.clear()
    return {"enabled": False, "stopped": stopped, "removed_state_directories": removed}


@app.post("/fleets/start")
def start_fleet(payload: StartRequest) -> dict[str, object]:
    if not ADMIN_PASSWORD:
        raise HTTPException(status_code=500, detail="DASHBOARD_PASSWORD is not configured")
    config = FAMILY_CONFIG[payload.family]
    started: list[str] = []
    with lock:
        if not simulation_enabled:
            raise HTTPException(status_code=409, detail="Simulation is disabled. Enable it from the dashboard first.")
        for device_id in _next_ids(config["prefix"], payload.count):
            item = _start_instance(device_id, config["family"], payload.interval)
            instances[device_id] = item
            started.append(device_id)
    return {"started": started, "family": config["family"], "interval": payload.interval}


@app.post("/fleets/stop-all")
def stop_all() -> dict[str, int]:
    with lock:
        stopped = _stop_all_locked()
    return {"stopped": stopped}
