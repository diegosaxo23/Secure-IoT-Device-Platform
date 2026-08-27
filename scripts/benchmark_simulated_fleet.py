#!/usr/bin/env python3
"""Automate simulated fleet benchmarks at 1/10/25/50 devices.

Every scale point first purges the previous simulated fleet (physical devices are
preserved), then uses a fresh local state directory and starts real simulated
clients that perform P-256/CSR/HMAC/X.509 enrollment and MQTT/mTLS, waits until
all expected clients are connected (or the timeout expires), then exports raw
and aggregate timing CSV files.
"""
from __future__ import annotations

import argparse
import csv
import os
import socket
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TextIO

from extract_metrics import collect_records, percentile95, summarize, write_raw_csv, write_summary_csv
from validation_config import parse_env, resolve_api_url
from validation_reports import platform_version, write_metadata

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SIMULATOR_SCRIPT = PROJECT_ROOT / "simulators" / "simulated_device.py"
DEFAULT_ENV = PROJECT_ROOT / ".env"
DEFAULT_CA = PROJECT_ROOT / "pki" / "ca" / "ca.crt"

FAMILIES = {
    "cromaled": ("CromaLED", "CLED-SIM"),
    "area_lz7": ("AREA LZ7", "AREA-SIM"),
    "as7341": ("AS7341", "AS7341-SIM"),
}


@dataclass
class ClientRun:
    device_id: str
    log_path: Path
    command: list[str]
    process: subprocess.Popen[object] | None = None
    handle: TextIO | None = None
    retries_used: int = 0
    exhausted: bool = False

    def has_connected(self) -> bool:
        try:
            return self.log_path.is_file() and "MQTT mTLS connected" in self.log_path.read_text(
                encoding="utf-8", errors="replace"
            )
        except OSError:
            return False


def _launch_client(client: ClientRun, *, child_env: dict[str, str], append: bool) -> None:
    if client.handle is not None:
        try:
            client.handle.close()
        except OSError:
            pass
    mode = "a" if append else "w"
    client.handle = client.log_path.open(mode, encoding="utf-8", buffering=1)
    if append:
        client.handle.write(
            f"\n[BENCHMARK] Relaunch attempt {client.retries_used} after premature simulator exit\n"
        )
    client.process = subprocess.Popen(
        client.command,
        cwd=PROJECT_ROOT,
        stdout=client.handle,
        stderr=subprocess.STDOUT,
        env=child_env,
        shell=False,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Benchmark simulated fleet provisioning and MQTT/mTLS.")
    parser.add_argument("--sizes", type=int, nargs="+", default=[1, 10, 25, 50])
    parser.add_argument("--family", choices=tuple(FAMILIES), default="cromaled")
    parser.add_argument("--api-url", help="Platform HTTPS base URL; defaults to .env API_PUBLIC_HOST/PORT")
    parser.add_argument("--mqtt-host", help="Broker host; defaults to .env MQTT_PUBLIC_HOST")
    parser.add_argument("--mqtt-port", type=int, help="Broker TLS port; defaults to .env MQTT_PUBLIC_PORT or 8883")
    parser.add_argument("--ca", type=Path, default=DEFAULT_CA)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--admin-username")
    parser.add_argument("--admin-password")
    parser.add_argument("--interval", type=float, default=30.0)
    parser.add_argument("--id-prefix", help="Optional Device ID prefix for benchmark identities")
    parser.add_argument("--timeout", type=float, default=240.0, help="Maximum seconds per scale point")
    parser.add_argument(
        "--mqtt-connect-timeout",
        type=float,
        default=90.0,
        help="Initial MQTT/mTLS connection window used by each simulated client",
    )
    parser.add_argument(
        "--launch-delay",
        type=float,
        default=0.08,
        help="Delay in seconds between simulated-client launches to avoid a TLS connection stampede",
    )
    parser.add_argument(
        "--client-retries",
        type=int,
        default=2,
        help="Number of automatic relaunches for a client that exits before its first MQTT connection",
    )
    parser.add_argument(
        "--progress-interval",
        type=float,
        default=10.0,
        help="Seconds between watchdog progress messages when the connected count does not change",
    )
    parser.add_argument(
        "--keep-existing",
        action="store_true",
        help=(
            "Do not purge pre-existing simulated devices before each scale point. "
            "By default the benchmark removes simulated state/registry rows only; physical devices are preserved."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Result directory. Default: validation_results/simulated/<timestamp>",
    )
    return parser


def count_log_marker(logs: list[Path], marker: str) -> int:
    count = 0
    for path in logs:
        try:
            if marker in path.read_text(encoding="utf-8", errors="replace"):
                count += 1
        except OSError:
            pass
    return count


def metric_values(records, name: str) -> list[float]:
    return [record.values[name] for record in records if name in record.values]


def stop_processes(processes: list[subprocess.Popen[object]]) -> None:
    for process in processes:
        if process.poll() is None:
            process.terminate()
    deadline = time.monotonic() + 8.0
    for process in processes:
        if process.poll() is not None:
            continue
        remaining = max(0.1, deadline - time.monotonic())
        try:
            process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def _run_cleanup_command(command: list[str], *, label: str, timeout: float = 120.0) -> None:
    print(f"[CLEANUP] {label}...")
    try:
        completed = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"Could not execute {command[0]!r}. Docker Desktop/Compose must be available for automatic cleanup."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Timed out while {label.lower()}") from exc

    output = (completed.stdout or "").strip()
    if output:
        for line in output.splitlines():
            print(f"          {line}")
    if completed.returncode != 0:
        raise RuntimeError(f"{label} failed with exit code {completed.returncode}")


def _wait_for_tcp(host: str, port: int, *, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=2.0):
                return
        except OSError as exc:
            last_error = exc
            time.sleep(0.5)
    detail = f": {last_error}" if last_error else ""
    raise RuntimeError(f"MQTT broker did not become reachable at {host}:{port}{detail}")


def purge_existing_simulated_devices(*, mqtt_host: str, mqtt_port: int) -> None:
    """Stop managed simulators and remove simulated registry state only.

    Physical devices are never touched. Existing simulated certificates are
    recorded in the CRL before their registry rows are deleted. The broker is
    then restarted synchronously so the new CRL is active before the next fleet.
    """
    manager_reset_code = (
        "import urllib.request; "
        "r=urllib.request.Request("
        "'http://localhost:8090/control/reset',data=b'{}',"
        "headers={'Content-Type':'application/json'},method='POST'); "
        "print(urllib.request.urlopen(r,timeout=10).read().decode())"
    )
    _run_cleanup_command(
        ["docker", "compose", "exec", "-T", "simulator-manager", "python", "-c", manager_reset_code],
        label="stopping managed simulators and removing their local state",
    )
    _run_cleanup_command(
        [
            "docker",
            "compose",
            "--profile",
            "tools",
            "run",
            "--rm",
            "tools",
            "scripts/admin.py",
            "purge-simulated",
            "--no-broker-restart",
        ],
        label="revoking and deleting simulated registry entries",
    )
    _run_cleanup_command(
        ["docker", "compose", "restart", "broker"],
        label="restarting Mosquitto so the refreshed CRL is active",
    )
    _wait_for_tcp(mqtt_host, mqtt_port)
    print("[CLEANUP] Ready. Physical devices were preserved.\n")


def write_global_summary(rows: list[dict[str, object]], path: Path) -> None:
    fields = [
        "requested",
        "provisioned",
        "mqtt_connected",
        "process_failures",
        "time_to_all_mqtt_s",
        "provisioning_mean_ms",
        "provisioning_median_ms",
        "provisioning_p95_ms",
        "provisioning_max_ms",
        "challenge_mean_ms",
        "enroll_mean_ms",
        "p256_csr_mean_ms",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = build_parser().parse_args()
    if any(size < 1 or size > 200 for size in args.sizes):
        print("ERROR: every --sizes value must be between 1 and 200", file=sys.stderr)
        return 2
    if args.timeout <= 0 or args.mqtt_connect_timeout <= 0 or args.progress_interval <= 0:
        print("ERROR: timeout values must be greater than zero", file=sys.stderr)
        return 2
    if args.launch_delay < 0 or args.client_retries < 0:
        print("ERROR: --launch-delay and --client-retries cannot be negative", file=sys.stderr)
        return 2

    env_file = parse_env(args.env_file)
    try:
        api_url = resolve_api_url(args.api_url, env_file)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    username = args.admin_username or env_file.get("DASHBOARD_USERNAME") or os.getenv("DASHBOARD_USERNAME")
    password = args.admin_password or env_file.get("DASHBOARD_PASSWORD") or os.getenv("DASHBOARD_PASSWORD")
    mqtt_host = args.mqtt_host or env_file.get("MQTT_PUBLIC_HOST") or env_file.get("API_PUBLIC_HOST")
    mqtt_port = args.mqtt_port or int(env_file.get("MQTT_PUBLIC_PORT", "8883"))
    if not username or not password:
        print("ERROR: dashboard administrator credentials were not found in .env or arguments", file=sys.stderr)
        return 2
    if not mqtt_host:
        print("ERROR: MQTT host was not found in .env; provide --mqtt-host", file=sys.stderr)
        return 2
    if not args.ca.is_file():
        print(f"ERROR: CA file not found: {args.ca}", file=sys.stderr)
        return 2

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = (args.output_dir or (PROJECT_ROOT / "validation_results" / "simulated" / stamp)).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    family_name, profile_prefix = FAMILIES[args.family]
    write_metadata(output_dir / "metadata.csv", {"benchmark": "simulated", "family": family_name, "sizes": ",".join(map(str, args.sizes))})
    benchmark_prefix = args.id_prefix or ("BENCH-" + profile_prefix.split("-")[0])
    run_tag = datetime.now().strftime("%m%d%H%M%S")
    global_rows: list[dict[str, object]] = []
    overall_ok = True

    print("=" * 72)
    print("    BENCHMARK DE FLOTA SIMULADA - SECURE IOT DEVICE PLATFORM")
    print("=" * 72)
    print(f"Version : {platform_version()}")
    print(f"Family  : {family_name}")
    print(f"Sizes   : {', '.join(map(str, args.sizes))}")
    print(f"API     : {api_url}")
    print(f"MQTT    : {mqtt_host}:{mqtt_port}")
    print(f"Output  : {output_dir}")

    for size in args.sizes:
        if not args.keep_existing:
            try:
                purge_existing_simulated_devices(mqtt_host=mqtt_host, mqtt_port=mqtt_port)
            except RuntimeError as exc:
                print(f"ERROR: automatic simulated-device cleanup failed: {exc}", file=sys.stderr)
                print(
                    "       Fix Docker/platform availability or rerun with --keep-existing only if preserving "
                    "the current simulated fleet is intentional.",
                    file=sys.stderr,
                )
                return 2

        run_dir = output_dir / f"fleet-{size:03d}"
        state_dir = run_dir / "state"
        logs_dir = run_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        state_dir.mkdir(parents=True, exist_ok=True)

        print("\n" + "=" * 72)
        print(f"FLOTA SIMULADA: {size} DISPOSITIVO(S)")
        print("=" * 72)

        fleet_started = time.perf_counter()
        time_to_all_mqtt_s: float | str = ""
        log_paths: list[Path] = []
        clients: list[ClientRun] = []
        child_env = os.environ.copy()
        child_env["PYTHONUTF8"] = "1"
        child_env["PYTHONIOENCODING"] = "utf-8"
        child_env["PYTHONUNBUFFERED"] = "1"

        for index in range(1, size + 1):
            device_id = f"{benchmark_prefix}-{run_tag}-{size:03d}-{index:04d}"
            log_path = logs_dir / f"{device_id}.log"
            log_paths.append(log_path)
            command = [
                sys.executable,
                "-X",
                "utf8",
                "-u",
                str(SIMULATOR_SCRIPT),
                "--device-id",
                device_id,
                "--family",
                family_name,
                "--api-url",
                api_url,
                "--bootstrap-ca",
                str(args.ca),
                "--state-dir",
                str(state_dir),
                "--auto-register",
                "--admin-username",
                username,
                "--admin-password",
                password,
                "--mqtt-host",
                mqtt_host,
                "--mqtt-port",
                str(mqtt_port),
                "--mqtt-connect-timeout",
                str(args.mqtt_connect_timeout),
                "--interval",
                str(args.interval),
            ]
            clients.append(ClientRun(device_id=device_id, log_path=log_path, command=command))

        early_failures: dict[str, int] = {}
        try:
            for client in clients:
                _launch_client(client, child_env=child_env, append=False)
                if args.launch_delay:
                    time.sleep(args.launch_delay)

            deadline = time.monotonic() + args.timeout
            last_reported = -1
            last_watchdog = time.monotonic()
            mqtt_connected = 0

            while time.monotonic() < deadline:
                mqtt_connected = sum(1 for client in clients if client.has_connected())
                now = time.monotonic()
                if mqtt_connected != last_reported:
                    active = sum(
                        1
                        for client in clients
                        if client.process is not None and client.process.poll() is None
                    )
                    print(f"[PROGRESS] MQTT/mTLS connected: {mqtt_connected}/{size} (active={active})")
                    last_reported = mqtt_connected
                    last_watchdog = now
                elif now - last_watchdog >= args.progress_interval:
                    active = sum(
                        1
                        for client in clients
                        if client.process is not None and client.process.poll() is None
                    )
                    exhausted = sum(1 for client in clients if client.exhausted)
                    remaining = max(0.0, deadline - now)
                    print(
                        f"[WAIT] MQTT/mTLS connected: {mqtt_connected}/{size}; "
                        f"active={active}; exhausted={exhausted}; timeout in {remaining:.0f}s"
                    )
                    last_watchdog = now

                if mqtt_connected >= size:
                    time_to_all_mqtt_s = time.perf_counter() - fleet_started
                    break

                # If a child exits before its first successful MQTT connection,
                # relaunch the same identity/state. A device that already enrolled
                # will therefore skip bootstrap and retry only the operational
                # MQTT path. This absorbs transient TLS/broker bursts at 50 clients.
                for client in clients:
                    if client.has_connected() or client.exhausted or client.process is None:
                        continue
                    returncode = client.process.poll()
                    if returncode is None:
                        continue
                    if client.handle is not None:
                        try:
                            client.handle.close()
                        except OSError:
                            pass
                        client.handle = None
                    if client.retries_used < args.client_retries:
                        client.retries_used += 1
                        print(
                            f"[RETRY] {client.device_id}: simulator exited with code {returncode}; "
                            f"retry {client.retries_used}/{args.client_retries}"
                        )
                        _launch_client(client, child_env=child_env, append=True)
                    else:
                        client.exhausted = True
                        early_failures[client.device_id] = int(returncode)
                        print(
                            f"[FAIL] {client.device_id}: exhausted {args.client_retries} reconnect relaunches "
                            f"(last exit code {returncode})"
                        )

                # Do not sit on X/N until the global timeout when every missing
                # client has already exhausted its retries.
                missing_clients = [client for client in clients if not client.has_connected()]
                if missing_clients and all(client.exhausted for client in missing_clients):
                    print("[FAIL] No remaining client can increase the connected count; ending this scale point early.")
                    break

                time.sleep(0.5)
        finally:
            stop_processes([client.process for client in clients if client.process is not None])
            for client in clients:
                if client.handle is not None:
                    try:
                        client.handle.close()
                    except Exception:
                        pass

        records = collect_records([logs_dir])
        provisioning = metric_values(records, "provisioning_total_ms")
        challenge = metric_values(records, "challenge_http_ms")
        enroll = metric_values(records, "enroll_http_ms")
        p256_csr = metric_values(records, "p256_csr_total_ms")
        provisioned = len(provisioning)
        mqtt_connected = count_log_marker(log_paths, "MQTT mTLS connected")
        process_failures = len(early_failures)

        raw_path = run_dir / "metrics.csv"
        summary_path = run_dir / "metrics-summary.csv"
        # Create per-fleet CSVs unconditionally so failed/time-out campaigns
        # still leave machine-readable evidence instead of silently missing files.
        write_raw_csv(records, raw_path)
        write_summary_csv(summarize(records), summary_path)

        row: dict[str, object] = {
            "requested": size,
            "provisioned": provisioned,
            "mqtt_connected": mqtt_connected,
            "process_failures": process_failures,
            "time_to_all_mqtt_s": time_to_all_mqtt_s,
            "provisioning_mean_ms": statistics.fmean(provisioning) if provisioning else "",
            "provisioning_median_ms": statistics.median(provisioning) if provisioning else "",
            "provisioning_p95_ms": percentile95(provisioning) if provisioning else "",
            "provisioning_max_ms": max(provisioning) if provisioning else "",
            "challenge_mean_ms": statistics.fmean(challenge) if challenge else "",
            "enroll_mean_ms": statistics.fmean(enroll) if enroll else "",
            "p256_csr_mean_ms": statistics.fmean(p256_csr) if p256_csr else "",
        }
        global_rows.append(row)

        ok = provisioned == size and mqtt_connected == size
        overall_ok = overall_ok and ok
        print(
            f"[{'PASS' if ok else 'FAIL'}] {size}: provisioned={provisioned}/{size}, "
            f"MQTT={mqtt_connected}/{size}"
        )
        if time_to_all_mqtt_s != "":
            print(f"       time to all MQTT connected={float(time_to_all_mqtt_s):.3f} s")
        if provisioning:
            print(
                f"       provisioning mean={statistics.fmean(provisioning):.3f} ms, "
                f"median={statistics.median(provisioning):.3f} ms, "
                f"p95={percentile95(provisioning):.3f} ms, max={max(provisioning):.3f} ms"
            )

    global_path = output_dir / "fleet-summary.csv"
    write_global_summary(global_rows, global_path)

    print("\n" + "=" * 72)
    print("RESULTADO GLOBAL")
    print("=" * 72)
    for row in global_rows:
        print(
            f"{row['requested']:>3} devices -> provisioned {row['provisioned']}/{row['requested']}, "
            f"MQTT {row['mqtt_connected']}/{row['requested']}"
        )
    print(f"\nSummary CSV: {global_path}")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
