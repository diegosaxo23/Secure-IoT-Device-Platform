from __future__ import annotations

from .area_lz7 import AreaLz7Profile
from .as7341 import As7341Profile
from .base import DeviceProfile
from .cromaled import CromaLedProfile


def create_profile(family: str, device_id: str) -> DeviceProfile:
    key = family.strip().lower().replace(" ", "").replace("-", "").replace("_", "")
    if "cromaled" in key or key.startswith("cled"):
        return CromaLedProfile(device_id)
    if "arealz7" in key or key.startswith("area"):
        return AreaLz7Profile(device_id)
    if "as7341" in key:
        return As7341Profile(device_id)
    raise ValueError(f"unsupported simulated family: {family}")


__all__ = ["DeviceProfile", "CromaLedProfile", "AreaLz7Profile", "As7341Profile", "create_profile"]
