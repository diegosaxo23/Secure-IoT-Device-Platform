#!/usr/bin/env python3
"""Run automated validation stages and export machine-readable CSV reports."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from validation_reports import platform_version, timestamped_output_dir, write_csv, write_metadata

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIRMWARE_PROJECTS = (
    "CromaLED_Gateway",
    "AREA_LZ7_Gateway",
    "AS7341_Gateway",
)


def banner(text: str) -> None:
    print("\n" + "=" * 72)
    print(text)
    print("=" * 72)


def run(cmd: list[str], *, env: dict[str, str] | None = None) -> int:
    print("$ " + " ".join(cmd))
    return subprocess.call(cmd, cwd=PROJECT_ROOT, env=env)


def parse_junit(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    if not path.is_file():
        return rows
    root = ET.parse(path).getroot()
    for case in root.iter("testcase"):
        status = "PASS"
        message = ""
        if case.find("failure") is not None:
            status = "FAIL"
            node = case.find("failure")
            message = (node.get("message") if node is not None else "") or ""
        elif case.find("error") is not None:
            status = "ERROR"
            node = case.find("error")
            message = (node.get("message") if node is not None else "") or ""
        elif case.find("skipped") is not None:
            status = "SKIP"
            node = case.find("skipped")
            message = (node.get("message") if node is not None else "") or ""
        rows.append(
            {
                "platform_version": platform_version(),
                "classname": case.get("classname", ""),
                "test": case.get("name", ""),
                "status": status,
                "duration_s": float(case.get("time", "0") or 0),
                "message": message,
            }
        )
    return rows


def run_pytest(output_dir: Path, verbose: bool = False) -> tuple[int, list[dict[str, object]]]:
    banner("PYTHON / SECURITY / SERVER REGRESSION TESTS")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT / "server")
    junit_path = output_dir / "pytest-junit.xml"
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-v" if verbose else "-q",
        "--junitxml",
        str(junit_path),
    ]
    code = run(cmd, env=env)
    rows = parse_junit(junit_path)
    write_csv(
        output_dir / "pytest-results.csv",
        rows,
        ["platform_version", "classname", "test", "status", "duration_s", "message"],
    )
    return code, rows


def platformio_available() -> bool:
    result = subprocess.run(
        [sys.executable, "-m", "platformio", "--version"],
        cwd=PROJECT_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def run_firmware_builds(output_dir: Path) -> tuple[int, list[dict[str, object]]]:
    banner("REAL PLATFORMIO BUILDS - ALL ESP32 GATEWAYS")
    rows: list[dict[str, object]] = []
    if not platformio_available():
        print("[FAIL] PlatformIO is not installed for this Python interpreter.")
        print(f"Install it with: {sys.executable} -m pip install platformio")
        rows.append(
            {
                "platform_version": platform_version(),
                "project": "PlatformIO",
                "status": "FAIL",
                "return_code": 2,
                "duration_s": 0.0,
            }
        )
        write_csv(
            output_dir / "firmware-builds.csv",
            rows,
            ["platform_version", "project", "status", "return_code", "duration_s"],
        )
        return 2, rows

    overall = 0
    for project in FIRMWARE_PROJECTS:
        project_dir = PROJECT_ROOT / "firmware" / "esp32" / project
        print(f"\n--- Building {project} ---")
        started = time.perf_counter()
        code = run(
            [
                sys.executable,
                "-m",
                "platformio",
                "run",
                "-d",
                str(project_dir),
                "-e",
                "esp32dev",
            ]
        )
        duration = time.perf_counter() - started
        rows.append(
            {
                "platform_version": platform_version(),
                "project": project,
                "status": "PASS" if code == 0 else "FAIL",
                "return_code": code,
                "duration_s": round(duration, 6),
            }
        )
        if code != 0:
            print(f"[FAIL] {project} did not compile.")
            overall = code
            break
        print(f"[PASS] {project} compiled successfully.")

    write_csv(
        output_dir / "firmware-builds.csv",
        rows,
        ["platform_version", "project", "status", "return_code", "duration_s"],
    )
    return overall, rows


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Secure IoT automated validation stages.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--python-only", action="store_true", help="Run pytest only (default).")
    mode.add_argument("--firmware-only", action="store_true", help="Compile all ESP32 firmware only.")
    mode.add_argument("--all", action="store_true", help="Run pytest and compile all ESP32 firmware.")
    parser.add_argument("--verbose", action="store_true", help="Use verbose pytest output.")
    parser.add_argument("--output-dir", type=Path, help="Optional explicit report directory.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    output_dir = timestamped_output_dir("tests", args.output_dir)
    write_metadata(output_dir / "metadata.csv", {"runner": "run_tests.py"})

    do_python = not args.firmware_only
    do_firmware = args.firmware_only or args.all
    summary: list[dict[str, object]] = []
    final_code = 0

    if do_python:
        code, rows = run_pytest(output_dir, args.verbose)
        counts = {status: sum(1 for row in rows if row["status"] == status) for status in ("PASS", "FAIL", "ERROR", "SKIP")}
        summary.append(
            {
                "platform_version": platform_version(),
                "stage": "pytest",
                "status": "PASS" if code == 0 else "FAIL",
                "passed": counts["PASS"],
                "failed": counts["FAIL"] + counts["ERROR"],
                "skipped": counts["SKIP"],
                "total": len(rows),
            }
        )
        if code != 0:
            final_code = code

    if do_firmware and final_code == 0:
        code, rows = run_firmware_builds(output_dir)
        summary.append(
            {
                "platform_version": platform_version(),
                "stage": "firmware-builds",
                "status": "PASS" if code == 0 else "FAIL",
                "passed": sum(1 for row in rows if row["status"] == "PASS"),
                "failed": sum(1 for row in rows if row["status"] == "FAIL"),
                "skipped": 0,
                "total": len(rows),
            }
        )
        if code != 0:
            final_code = code

    write_csv(
        output_dir / "validation-summary.csv",
        summary,
        ["platform_version", "stage", "status", "passed", "failed", "skipped", "total"],
    )

    banner("VALIDATION RESULT")
    print(f"Version : {platform_version()}")
    print(f"CSV dir : {output_dir}")
    if final_code == 0:
        print("[PASS] Requested validation stages completed successfully.")
    else:
        print(f"[FAIL] Validation finished with code {final_code}.")
    return final_code


if __name__ == "__main__":
    raise SystemExit(main())
