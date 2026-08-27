from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class DeviceProfile(ABC):
    family: str = "generic"
    firmware: str = "sim-1.1.0"

    def __init__(self, device_id: str) -> None:
        self.device_id = device_id

    @abstractmethod
    def telemetry(self, *, sequence: int, uptime_s: int, timestamp: str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def handle_command(self, command: str, parameters: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """Return (status, result)."""
        raise NotImplementedError

    def reset(self) -> None:
        """Simulate an application-layer restart."""

    @staticmethod
    def clamp_level(value: Any) -> int:
        if not isinstance(value, (int, float)):
            raise ValueError("level must be numeric")
        return int(max(0, min(100, round(float(value)))))
