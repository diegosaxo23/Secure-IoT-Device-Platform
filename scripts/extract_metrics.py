#!/usr/bin/env python3
"""Extract [METRIC] records from physical/simulated device logs.

The script intentionally uses only the Python standard library so measurements
can be exported without adding another runtime dependency to the platform.
"""
from __future__ import annotations

import argparse
import codecs
import csv
import math
import re
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

METRIC_MARKER = "[METRIC]"
DEVICE_PREFIX = re.compile(r"^\[(?P<device>[^\]]+)\]\s+\[METRIC\]\s+(?P<body>.*)$")
PAIR = re.compile(r"(?P<key>[A-Za-z][A-Za-z0-9_]*)=(?P<value>-?\d+(?:\.\d+)?)")


@dataclass(frozen=True)
class MetricRecord:
    source: str
    line_number: int
    device_id: str
    values: dict[str, float]


def parse_metric_line(line: str, *, source: str = "", line_number: int = 0) -> MetricRecord | None:
    if METRIC_MARKER not in line:
        return None
    stripped = line.strip()
    device_id = ""
    match = DEVICE_PREFIX.match(stripped)
    if match:
        device_id = match.group("device")
        body = match.group("body")
    else:
        body = stripped.split(METRIC_MARKER, 1)[1].strip()
    values = {m.group("key"): float(m.group("value")) for m in PAIR.finditer(body)}
    if not values:
        return None
    return MetricRecord(source=source, line_number=line_number, device_id=device_id, values=values)


def iter_log_files(paths: Iterable[Path]) -> Iterable[Path]:
    seen: set[Path] = set()
    for path in paths:
        if path.is_file():
            resolved = path.resolve()
            if resolved not in seen:
                seen.add(resolved)
                yield path
            continue
        if not path.is_dir():
            continue
        for candidate in sorted(path.rglob("*")):
            if not candidate.is_file():
                continue
            if candidate.suffix.lower() not in {".log", ".txt", ".out"}:
                continue
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            yield candidate


def read_log_text(path: Path) -> str:
    """Read logs produced by Linux/macOS shells and Windows PowerShell safely.

    Windows PowerShell 5.x writes ``Tee-Object -FilePath`` output as UTF-16LE,
    while PowerShell 7 and most other shells normally produce UTF-8.  Reading a
    UTF-16 log as UTF-8 inserts NUL characters between every printable byte and
    makes the ``[METRIC]`` marker impossible to match.
    """
    data = path.read_bytes()
    if not data:
        return ""

    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode("utf-16", errors="replace")
    if data.startswith(codecs.BOM_UTF8):
        return data.decode("utf-8-sig", errors="replace")

    # Some tools can emit UTF-16LE without a BOM. A byte stream containing
    # ASCII text encoded as UTF-16LE is also technically valid UTF-8 because
    # NUL bytes are legal UTF-8 characters, so detect this *before* attempting
    # a normal UTF-8 decode.
    sample = data[:4096]
    if sample and sample.count(b"\x00") >= max(2, len(sample) // 8):
        try:
            return data.decode("utf-16-le")
        except UnicodeDecodeError:
            pass

    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass

    # Last-resort decoding keeps extraction useful even if a serial line contains
    # a byte that is not valid UTF-8. Metric names and values are ASCII.
    return data.decode("utf-8", errors="replace")


def collect_records(paths: Iterable[Path]) -> list[MetricRecord]:
    records: list[MetricRecord] = []
    for path in iter_log_files(paths):
        try:
            lines = read_log_text(path).splitlines()
        except OSError:
            continue
        for number, line in enumerate(lines, start=1):
            record = parse_metric_line(line, source=str(path), line_number=number)
            if record:
                records.append(record)
    return records


def write_raw_csv(records: list[MetricRecord], output: Path) -> None:
    metric_names = sorted({key for record in records for key in record.values})
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["source", "line_number", "device_id", *metric_names])
        writer.writeheader()
        for record in records:
            row: dict[str, object] = {
                "source": record.source,
                "line_number": record.line_number,
                "device_id": record.device_id,
            }
            row.update(record.values)
            writer.writerow(row)


def percentile95(values: list[float]) -> float:
    if not values:
        return math.nan
    ordered = sorted(values)
    index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return ordered[index]


def summarize(records: list[MetricRecord]) -> list[dict[str, float | int | str]]:
    by_metric: dict[str, list[float]] = {}
    for record in records:
        for key, value in record.values.items():
            by_metric.setdefault(key, []).append(value)
    rows: list[dict[str, float | int | str]] = []
    for metric in sorted(by_metric):
        values = by_metric[metric]
        rows.append(
            {
                "metric": metric,
                "count": len(values),
                "min": min(values),
                "mean": statistics.fmean(values),
                "median": statistics.median(values),
                "p95": percentile95(values),
                "max": max(values),
            }
        )
    return rows


def write_summary_csv(rows: list[dict[str, float | int | str]], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["metric", "count", "min", "mean", "median", "p95", "max"])
        writer.writeheader()
        writer.writerows(rows)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract Secure IoT Device Platform [METRIC] records from logs.")
    parser.add_argument("paths", nargs="+", type=Path, help="Log files or directories to scan recursively.")
    parser.add_argument("--output", type=Path, default=Path("metrics.csv"), help="Raw CSV output path.")
    parser.add_argument(
        "--summary-output",
        type=Path,
        default=Path("metrics-summary.csv"),
        help="Aggregate metric CSV output path.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    records = collect_records(args.paths)
    # Always emit both CSV files.  An empty, header-only CSV is useful evidence
    # that a campaign was executed but produced no metric records.
    write_raw_csv(records, args.output)
    summary = summarize(records)
    write_summary_csv(summary, args.summary_output)
    if not records:
        print("No [METRIC] records found.")
        print(f"Raw CSV -> {args.output}")
        print(f"Summary -> {args.summary_output}")
        return 1
    print(f"Extracted {len(records)} metric records -> {args.output}")
    print(f"Summary -> {args.summary_output}")
    for row in summary:
        print(
            f"{row['metric']}: n={row['count']} mean={row['mean']:.3f} "
            f"median={row['median']:.3f} p95={row['p95']:.3f} max={row['max']:.3f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
