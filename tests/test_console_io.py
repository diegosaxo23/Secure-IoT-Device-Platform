import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from console_io import console_safe


def test_console_safe_replaces_serial_replacement_character_for_windows_cp1252() -> None:
    # This is the exact class of failure observed when manufacturing output was
    # piped through PowerShell Tee-Object on Windows.
    rendered = console_safe("[ESP32] malformed byte: \ufffd", "cp1252")
    assert rendered == "[ESP32] malformed byte: ?"
    rendered.encode("cp1252")  # must remain printable by the redirected console


def test_console_safe_preserves_valid_utf8_text() -> None:
    text = "[ESP32] [METRIC] provisioning_total_ms=6283"
    assert console_safe(text, "utf-8") == text
