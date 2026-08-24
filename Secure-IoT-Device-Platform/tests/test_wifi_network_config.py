from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import network_config


def test_wifi_selection_uses_only_wifi_candidates(monkeypatch):
    candidates = [
        network_config.IPv4Candidate("10.10.20.30", "Wi-Fi 2"),
        network_config.IPv4Candidate("192.168.50.10", "Wi-Fi"),
    ]
    monkeypatch.setattr(network_config, "collect_wifi_ipv4_candidates", lambda: candidates)

    selected, returned = network_config.select_wifi_ipv4()

    assert selected == "192.168.50.10"
    assert returned == candidates


def test_wifi_selection_fails_without_active_wifi(monkeypatch):
    monkeypatch.setattr(network_config, "collect_wifi_ipv4_candidates", lambda: [])

    try:
        network_config.select_wifi_ipv4()
    except RuntimeError as exc:
        assert "No active Wi-Fi adapter" in str(exc)
    else:
        raise AssertionError("Expected RuntimeError when no Wi-Fi adapter is active")


def test_clean_env_has_wifi_mode_and_no_preferred_ip() -> None:
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "NETWORK_INTERFACE_MODE=wifi" in example
    assert "PREFERRED_PLATFORM_IP" not in example


def test_windows_detection_does_not_require_default_gateway() -> None:
    source = (SCRIPTS / "network_config.py").read_text(encoding="utf-8")
    assert "if ($cfg.IPv4Address)" in source
    assert "if ($cfg.IPv4Address -and $cfg.IPv4DefaultGateway)" not in source
    assert "default gateway is intentionally NOT required" in source


def test_error_message_allows_isolated_wifi_without_gateway(monkeypatch) -> None:
    monkeypatch.setattr(network_config, "collect_wifi_ipv4_candidates", lambda: [])
    try:
        network_config.select_wifi_ipv4()
    except RuntimeError as exc:
        message = str(exc).lower()
        assert "default gateway is not required" in message
        assert "iot wi-fi/ap" in message
    else:
        raise AssertionError("Expected RuntimeError when no Wi-Fi adapter is active")
