"""Small console-output helpers shared by Windows manufacturing scripts."""
from __future__ import annotations

import sys


def console_safe(text: str, encoding: str | None = None) -> str:
    """Return *text* converted to characters supported by the target console.

    Serial data is decoded with replacement characters when malformed bytes are
    received. Windows PowerShell can redirect Python stdout through a cp1252
    stream, which cannot encode U+FFFD and used to abort an otherwise valid
    manufacturing run. Unsupported characters are therefore replaced before
    printing.
    """
    target_encoding = encoding or getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        return text.encode(target_encoding, errors="replace").decode(
            target_encoding, errors="replace"
        )
    except (LookupError, UnicodeError):
        return text.encode("ascii", errors="replace").decode("ascii")


def print_esp32_line(line: str) -> None:
    """Print one ESP32 serial line without allowing console encoding to abort."""
    print(console_safe(f"[ESP32] {line}"))
