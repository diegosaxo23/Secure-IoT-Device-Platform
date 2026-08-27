from __future__ import annotations

import math
from typing import Any

from .base import DeviceProfile


CHANNELS = [
    "ROYAL_BLUE",
    "BLUE",
    "CYAN",
    "GREEN",
    "LIME",
    "LIME2",
    "AMBER",
    "AMBER2",
    "RED_ORANGE",
    "RED",
    "DEEP_RED",
]


class CromaLedProfile(DeviceProfile):
    family = "CromaLED"
    firmware = "cromaled-sim-1.1.0"

    def __init__(self, device_id: str) -> None:
        super().__init__(device_id)
        self.levels = {name: 0 for name in CHANNELS}

    def _resolve_channel(self, parameters: dict[str, Any]) -> str:
        channel = parameters.get("channel")
        if isinstance(channel, str):
            normalized = channel.strip().upper().replace(" ", "_").replace("-", "_")
            if normalized in self.levels:
                return normalized
        index = parameters.get("channel_index", channel)
        if isinstance(index, int) and 1 <= index <= len(CHANNELS):
            return CHANNELS[index - 1]
        raise ValueError(f"channel must identify one of the {len(CHANNELS)} channels")

    def telemetry(self, *, sequence: int, uptime_s: int, timestamp: str) -> dict[str, Any]:
        values = list(self.levels.values())
        average = sum(values) / len(values)
        temperature = 24.5 + average * 0.105 + math.sin(sequence / 7.0) * 0.45
        return {
            "device_id": self.device_id,
            "family": self.family,
            "deployment_type": "simulated",
            "firmware": self.firmware,
            "timestamp": timestamp,
            "uptime_s": uptime_s,
            "sequence": sequence,
            "channels": [
                {"index": index, "name": name, "level": self.levels[name], "enabled": self.levels[name] > 0}
                for index, name in enumerate(CHANNELS, start=1)
            ],
            "measurements": {
                "active_channels": sum(1 for value in values if value > 0),
                "average_level": round(average, 1),
                "temperature_c": round(temperature, 1),
            },
        }

    def handle_command(self, command: str, parameters: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        if command == "set_channel":
            channel = self._resolve_channel(parameters)
            level = self.clamp_level(parameters.get("level"))
            self.levels[channel] = level
            return "completed", {"channel": channel, "level": level}
        if command == "set_channels":
            channels = parameters.get("channels")
            if isinstance(channels, list) and len(channels) == len(CHANNELS):
                self.levels = {name: self.clamp_level(channels[index]) for index, name in enumerate(CHANNELS)}
                return "completed", {"channels": self.levels}
            if isinstance(channels, dict):
                for name, value in channels.items():
                    normalized = str(name).upper().replace(" ", "_").replace("-", "_")
                    if normalized in self.levels:
                        self.levels[normalized] = self.clamp_level(value)
                return "completed", {"channels": self.levels}
            raise ValueError("channels must be a list of 11 values or a name-keyed object")
        if command == "set_all_channels":
            level = self.clamp_level(parameters.get("level"))
            self.levels = {name: level for name in CHANNELS}
            return "completed", {"channels": len(CHANNELS), "level": level}
        if command == "off":
            self.levels = {name: 0 for name in CHANNELS}
            return "completed", {"off": True}
        raise ValueError("unsupported")

    def reset(self) -> None:
        # A restart does not clear the last application state in this simulation.
        pass
