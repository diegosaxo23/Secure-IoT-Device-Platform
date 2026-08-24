from __future__ import annotations

from app.device_profiles import profile_for


def test_family_profiles_have_expected_application_shape() -> None:
    cromaled = profile_for("CromaLED")
    assert cromaled["slug"] == "cromaled"
    assert len(cromaled["channels"]) == 11
    assert cromaled["channels"][0]["id"] == "ROYAL_BLUE"
    assert cromaled["channels"][-1]["id"] == "DEEP_RED"

    area = profile_for("AREA LZ7")
    assert area["slug"] == "area_lz7"
    assert [item["id"] for item in area["channels"]] == [
        "BLUE", "CYAN", "GREEN", "LIME", "AMBER", "RED"
    ]

    sensor = profile_for("AS7341")
    assert sensor["slug"] == "as7341"
    assert sensor["bands"] == ["F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "NIR", "CLEAR"]

