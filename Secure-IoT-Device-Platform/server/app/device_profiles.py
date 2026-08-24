from __future__ import annotations

from typing import Any


CROMALED_CHANNELS = [
    ("ROYAL_BLUE", "Royal Blue"),
    ("BLUE", "Blue"),
    ("CYAN", "Cyan"),
    ("GREEN", "Green"),
    ("LIME", "Lime"),
    ("LIME2", "Lime 2"),
    ("AMBER", "Amber"),
    ("AMBER2", "Amber 2"),
    ("RED_ORANGE", "Red Orange"),
    ("RED", "Red"),
    ("DEEP_RED", "Deep Red"),
]

AREA_LZ7_CHANNELS = [
    ("BLUE", "Blue"),
    ("CYAN", "Cyan"),
    ("GREEN", "Green"),
    ("LIME", "Lime"),
    ("AMBER", "Amber"),
    ("RED", "Red"),
]

AS7341_BANDS = ["F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "NIR", "CLEAR"]


def family_slug(family: str) -> str:
    key = family.strip().lower().replace(" ", "").replace("-", "").replace("_", "")
    if "cromaled" in key or key.startswith("cled"):
        return "cromaled"
    if "arealz7" in key or key.startswith("area"):
        return "area_lz7"
    if "as7341" in key or "lightsensor" in key:
        return "as7341"
    if key in {"test", "demo", "demodevice", "generic"}:
        return "demo"
    return "generic"


def profile_for(family: str) -> dict[str, Any]:
    slug = family_slug(family)
    if slug == "cromaled":
        return {
            "slug": slug,
            "title": "CromaLED",
            "subtitle": "Multichannel spectral control",
            "kind": "lighting",
            "channels": [
                {"index": index, "id": channel_id, "label": label}
                for index, (channel_id, label) in enumerate(CROMALED_CHANNELS, start=1)
            ],
        }
    if slug == "area_lz7":
        return {
            "slug": slug,
            "title": "AREA LZ7",
            "subtitle": "Six-channel luminaire",
            "kind": "lighting",
            "channels": [
                {"index": index, "id": channel_id, "label": label}
                for index, (channel_id, label) in enumerate(AREA_LZ7_CHANNELS, start=1)
            ],
        }
    if slug == "as7341":
        return {
            "slug": slug,
            "title": "AS7341",
            "subtitle": "Spectral light sensor",
            "kind": "sensor",
            "bands": AS7341_BANDS,
        }
    if slug == "demo":
        return {
            "slug": slug,
            "title": "Demo Device",
            "subtitle": "Validation device",
            "kind": "demo",
        }
    return {
        "slug": "generic",
        "title": family or "Generic",
        "subtitle": "IoT device",
        "kind": "generic",
    }
