#!/usr/bin/env python3
"""Automate repeated physical ESP32 manufacturing/provisioning measurements.

Each run invokes the normal factory programmer, streams its output to the console,
saves a dedicated UTF-8 log, and extracts the firmware [METRIC] records.  The
benchmark therefore measures the same path used by the manufacturing station.
"""
from __future__ import annotations

import argparse
import csv
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from extract_metrics import collect_records, summarize, write_raw_csv, write_summary_csv
from validation_reports import platform_version, write_metadata

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FACTORY_SCRIPT = PROJECT_ROOT / "scripts" / "factory_program_esp32.py"
VALID_PROFILES = ("cromaled", "area_lz7", "as7341")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run repeated physical ESP32 provisioning measurements and export CSV results."
    )
    parser.add_argument("--profile", required=True, choices=VALID_PROFILES)
    parser.add_argument("--port", required=True, help="Serial port, for example COM2 or /dev/ttyUSB0")
    parser.add_argument("--runs", type=int, default=10, help="Number of complete provisioning runs (default: 10)")
    parser.add_argument("--api-url", help="Optional explicit platform HTTPS URL")
    parser.add_argument("--platformio", help="Optional PlatformIO executable path")
    parser.add_argument("--observe-seconds", type=float, default=90.0)
    parser.add_argument("--serial-timeout", type=float, default=90.0)
    parser.add_argument("--api-timeout", type=float, default=15.0)
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Result directory. Default: validation_results/physical/<timestamp>",
    )
    parser.add_argument(
        "--keep-going",
        action="store_true",
        help="Continue with later runs after a failed run instead of stopping immediately.",
    )
    return parser


def stream_command(command: list[str], log_path: Path) -> int:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"

    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8", newline="") as log:
        process = subprocess.Popen(
            command,
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
        )
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="")
            log.write(line)
            log.flush()
        return process.wait()


def merged_values_for_log(log_path: Path) -> dict[str, float]:
    merged: dict[str, float] = {}
    for record in collect_records([log_path]):
        merged.update(record.values)
    return merged


def write_run_summary(rows: list[dict[str, object]], output: Path) -> None:
    metric_names = sorted(
        {
            key
            for row in rows
            for key in row
            if key not in {"run", "status", "log"}
        }
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["run", "status", "log", *metric_names])
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = build_parser().parse_args()
    if args.runs < 1:
        print("ERROR: --runs must be at least 1", file=sys.stderr)
        return 2

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = args.output_dir or (PROJECT_ROOT / "validation_results" / "physical" / stamp)
    output_dir = output_dir.resolve()
    logs_dir = output_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    write_metadata(output_dir / "metadata.csv", {"benchmark": "physical", "profile": args.profile, "port": args.port, "runs": args.runs})

    print("=" * 72)
    print("      BENCHMARK FISICO - SECURE IOT DEVICE PLATFORM")
    print("=" * 72)
    print(f"Version : {platform_version()}")
    print(f"Profile : {args.profile}")
    print(f"Port    : {args.port}")
    print(f"Runs    : {args.runs}")
    print(f"Output  : {output_dir}")
    print()

    run_rows: list[dict[str, object]] = []
    completed_logs: list[Path] = []
    failures = 0

    for run_number in range(1, args.runs + 1):
        log_path = logs_dir / f"physical-{run_number:02d}.txt"
        print("\n" + "=" * 72)
        print(f"CAMPANA FISICA - ENSAYO {run_number:02d}/{args.runs:02d}")
        print("=" * 72)

        command = [
            sys.executable,
            "-X",
            "utf8",
            "-u",
            str(FACTORY_SCRIPT),
            "--profile",
            args.profile,
            "--port",
            args.port,
            "--reset-existing",
            "--non-interactive",
            "--observe-seconds",
            str(args.observe_seconds),
            "--serial-timeout",
            str(args.serial_timeout),
            "--api-timeout",
            str(args.api_timeout),
        ]
        if args.api_url:
            command.extend(["--api-url", args.api_url])
        if args.platformio:
            command.extend(["--platformio", args.platformio])

        code = stream_command(command, log_path)
        values = merged_values_for_log(log_path)
        has_total = "provisioning_total_ms" in values
        status = "PASS" if code == 0 and has_total else "FAIL"
        row: dict[str, object] = {
            "run": run_number,
            "status": status,
            "log": str(log_path.relative_to(output_dir)),
        }
        row.update(values)
        run_rows.append(row)

        if status == "PASS":
            completed_logs.append(log_path)
            print(f"[PASS] Ensayo {run_number:02d}: metricas completas guardadas.")
        else:
            failures += 1
            if code != 0:
                print(f"[FAIL] Ensayo {run_number:02d}: factory programmer returned {code}.")
            if not has_total:
                print(f"[FAIL] Ensayo {run_number:02d}: provisioning_total_ms was not found in the log.")
            if not args.keep_going:
                print("Stopping campaign. Use --keep-going to continue after failures.")
                break

    run_summary_path = output_dir / "runs.csv"
    write_run_summary(run_rows, run_summary_path)

    records = collect_records(completed_logs)
    raw_path = output_dir / "physical-metrics.csv"
    summary_path = output_dir / "physical-metrics-summary.csv"
    summary_rows = summarize(records)
    # Always create both CSVs. If all runs fail, they remain header-only and
    # the campaign still leaves a machine-readable record of the failure.
    write_raw_csv(records, raw_path)
    write_summary_csv(summary_rows, summary_path)

    if records:
        print("\n" + "=" * 72)
        print("RESUMEN DE METRICAS")
        print("=" * 72)
        for row in summary_rows:
            print(
                f"{row['metric']}: n={row['count']} mean={row['mean']:.3f} "
                f"median={row['median']:.3f} p95={row['p95']:.3f} max={row['max']:.3f}"
            )
    else:
        print("\n[FAIL] No complete [METRIC] records were collected.")
        failures += 1
    print(f"\nRaw CSV     : {raw_path}")
    print(f"Summary CSV : {summary_path}")

    print(f"Run CSV     : {run_summary_path}")
    print(f"Completed   : {sum(1 for row in run_rows if row['status'] == 'PASS')}/{len(run_rows)}")
    return 0 if failures == 0 and len(run_rows) == args.runs else 1


if __name__ == "__main__":
    raise SystemExit(main())
