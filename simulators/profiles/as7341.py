from __future__ import annotations

import hashlib
import math
from typing import Any

from .base import DeviceProfile


BANDS = ["F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "NIR", "CLEAR"]


class As7341Profile(DeviceProfile):
    family = "AS7341"
    firmware = "as7341-sim-1.1.0"

    def __init__(self, device_id: str) -> None:
        super().__init__(device_id)
        digest = hashlib.sha256(device_id.encode("utf-8")).digest()
        self.phase = digest[0] / 255.0 * math.tau
        self.gain = 256
        self.last_spectrum = {band: 0 for band in BANDS}

    def _spectrum(self, sequence: int) -> dict[str, int]:
        bases = [420, 560, 780, 980, 1230, 1090, 830, 610, 470]
        values: dict[str, int] = {}
        for index, band in enumerate(BANDS[:-1]):
            wave = 1.0 + 0.18 * math.sin(self.phase + sequence / 7.0 + index * 0.47)
            secondary = 0.06 * math.sin(self.phase * 0.5 + sequence / 19.0 + index)
            values[band] = max(0, int(bases[index] * (wave + secondary)))
        values["CLEAR"] = int(sum(values[band] for band in BANDS[:8]) / 4.8)
        return values

    def telemetry(self, *, sequence: int, uptime_s: int, timestamp: str) -> dict[str, Any]:
        spectrum = self._spectrum(sequence)
        self.last_spectrum = spectrum
        return {
            "device_id": self.device_id,
            "family": self.family,
            "deployment_type": "simulated",
            "firmware": self.firmware,
            "timestamp": timestamp,
            "uptime_s": uptime_s,
            "sequence": sequence,
            "gain": self.gain,
            "spectrum": spectrum,
            "measurements": {
                "gain": self.gain,
                "clear": spectrum["CLEAR"],
                "nir": spectrum["NIR"],
                "synthetic": True,
            },
        }

    def handle_command(self, command: str, parameters: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        if command == "read_spectrum":
            return "completed", {"gain": self.gain, "spectrum": dict(self.last_spectrum)}
        raise ValueError("unsupported")
