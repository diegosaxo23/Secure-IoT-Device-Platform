#!/usr/bin/env python3
"""Small helpers shared by validation and benchmark utilities."""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Iterable, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = PROJECT_ROOT / "VERSION"


def platform_version() -> str:
    try:
        value = VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"
    return value or "unknown"


def timestamped_output_dir(category: str, explicit: Path | None = None) -> Path:
    if explicit is not None:
        path = explicit
        if not path.is_absolute():
            path = PROJECT_ROOT / path
    else:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = PROJECT_ROOT / "validation_results" / category / stamp
    path = path.resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_csv(path: Path, rows: Iterable[Mapping[str, object]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(dict(row))


def write_metadata(path: Path, values: Mapping[str, object]) -> None:
    rows = [{"key": "platform_version", "value": platform_version()}]
    rows.extend({"key": key, "value": value} for key, value in values.items())
    write_csv(path, rows, ["key", "value"])
