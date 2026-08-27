#!/usr/bin/env python3
"""Run security-focused checks with concise output and automatic CSV reports."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from validation_reports import platform_version, timestamped_output_dir, write_csv, write_metadata

PROJECT_ROOT = Path(__file__).resolve().parents[1]

CHECKS = (
    ("HMAC vinculado a sesion, nonce y CSR", "tests/test_security.py::test_hmac_is_bound_to_session_nonce_and_csr"),
    ("Enrollment: secreto incorrecto, CSR sustituida y replay rechazados", "tests/test_provisioning_flow.py::test_complete_bootstrap_flow"),
    ("Identidad X.509 controlada por el servidor", "tests/test_pki.py::test_csr_subject_is_untrusted_and_certificate_cn_comes_from_authenticated_device"),
    ("Tiempo local firmado verificable", "tests/test_signed_local_time.py::test_signed_time_token_verifies_with_public_key"),
    ("ACL MQTT limitada a la identidad autenticada", "tests/test_controlled_operations.py::test_mqtt_device_acl_is_scoped_to_authenticated_username"),
    ("Logs de metricas Windows PowerShell UTF-16 compatibles", "tests/test_metrics.py::test_collect_metrics_from_windows_powershell_utf16_log"),
    ("Salida serie segura en consola Windows cp1252", "tests/test_console_io.py::test_console_safe_replaces_serial_replacement_character_for_windows_cp1252"),
    ("Regresiones de compilacion compartidas entre gateways", "tests/test_firmware_regressions.py::test_pubsubclient_connected_wrapper_is_non_const_in_every_gateway"),
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the concise security validation report.")
    parser.add_argument("--output-dir", type=Path, help="Optional explicit report directory.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    output_dir = timestamped_output_dir("security", args.output_dir)
    write_metadata(output_dir / "metadata.csv", {"runner": "run_security_tests.py"})

    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT / "server")
    passed = 0
    rows: list[dict[str, object]] = []

    print("=" * 72)
    print("          VALIDACION DE SEGURIDAD - SECURE IOT PLATFORM")
    print("=" * 72)
    print(f"Version: {platform_version()}")
    print()

    for label, node in CHECKS:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", node, "-q", "--tb=no"],
            cwd=PROJECT_ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        ok = result.returncode == 0
        print(f"[{'PASS' if ok else 'FAIL'}] {label}")
        passed += int(ok)
        rows.append(
            {
                "platform_version": platform_version(),
                "label": label,
                "pytest_node": node,
                "status": "PASS" if ok else "FAIL",
                "return_code": result.returncode,
            }
        )

    write_csv(
        output_dir / "security-tests.csv",
        rows,
        ["platform_version", "label", "pytest_node", "status", "return_code"],
    )
    write_csv(
        output_dir / "security-summary.csv",
        [{"platform_version": platform_version(), "passed": passed, "failed": len(CHECKS) - passed, "total": len(CHECKS)}],
        ["platform_version", "passed", "failed", "total"],
    )

    print()
    print("-" * 72)
    print(f"RESULTADO: {passed}/{len(CHECKS)} CONTROLES SUPERADOS")
    print(f"CSV: {output_dir / 'security-tests.csv'}")
    print("-" * 72)
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
