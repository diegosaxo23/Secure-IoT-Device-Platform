from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SIMULATORS = ROOT / "simulators"
sys.path.insert(0, str(SIMULATORS))

from profiles.area_lz7 import AreaLz7Profile  # noqa: E402
from profiles.as7341 import As7341Profile  # noqa: E402
from profiles.cromaled import CromaLedProfile  # noqa: E402


def test_cromaled_simulator_has_11_channels_and_accepts_named_control() -> None:
    profile = CromaLedProfile("CLED-SIM-0001")
    status, result = profile.handle_command("set_channel", {"channel": "DEEP_RED", "level": 73})
    assert status == "completed"
    assert result == {"channel": "DEEP_RED", "level": 73}
    telemetry = profile.telemetry(sequence=1, uptime_s=2, timestamp="2026-08-20T00:00:00Z")
    assert len(telemetry["channels"]) == 11
    assert telemetry["channels"][-1]["name"] == "DEEP_RED"
    assert telemetry["channels"][-1]["level"] == 73
    assert telemetry["deployment_type"] == "simulated"


def test_area_lz7_simulator_has_6_channels() -> None:
    profile = AreaLz7Profile("AREA-SIM-0001")
    status, _ = profile.handle_command("set_channels", {"channels": [10, 20, 30, 40, 50, 60]})
    assert status == "completed"
    telemetry = profile.telemetry(sequence=1, uptime_s=2, timestamp="2026-08-20T00:00:00Z")
    assert len(telemetry["channels"]) == 6
    assert telemetry["channels"][-1]["name"] == "RED"
    assert telemetry["channels"][-1]["level"] == 60


def test_as7341_simulator_matches_physical_fixed_256x_gain() -> None:
    profile = As7341Profile("AS7341-SIM-0001")
    telemetry = profile.telemetry(sequence=12, uptime_s=30, timestamp="2026-08-20T00:00:00Z")
    assert telemetry["gain"] == 256
    assert set(telemetry["spectrum"]) == {"F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "NIR", "CLEAR"}
    assert all(value >= 0 for value in telemetry["spectrum"].values())
