from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import benchmark_simulated_fleet
import run_security_tests
import run_tests
import validate_live_bootstrap_security
from validation_config import parse_env, resolve_api_url
from app.security import calculate_proof_hex


def test_validation_config_reads_env_without_factory_dependencies(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text(
        'API_PUBLIC_HOST="192.168.50.10"\nAPI_PUBLIC_PORT=8443\nDASHBOARD_PASSWORD="secret value"\n',
        encoding="utf-8",
    )
    values = parse_env(path)
    assert values["API_PUBLIC_HOST"] == "192.168.50.10"
    assert values["DASHBOARD_PASSWORD"] == "secret value"
    assert resolve_api_url(None, values) == "https://192.168.50.10:8443"


def test_live_bootstrap_client_proof_matches_server_canonicalization() -> None:
    secret = validate_live_bootstrap_security.generate_secret()
    challenge = {"session_id": "0123456789abcdef", "nonce": "nonce-demo"}
    digest = "a" * 64
    live_proof = validate_live_bootstrap_security.proof(
        secret, "CROMALED-0001", challenge, digest
    )
    server_proof = calculate_proof_hex(
        secret_b64=secret,
        device_id="CROMALED-0001",
        session_id=challenge["session_id"],
        nonce_b64=challenge["nonce"],
        csr_digest=digest,
    )
    assert live_proof == server_proof


def test_default_simulated_benchmark_scale_is_1_10_25_50(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["benchmark_simulated_fleet.py"])
    args = benchmark_simulated_fleet.build_parser().parse_args()
    assert args.sizes == [1, 10, 25, 50]
    assert args.family == "cromaled"


def test_test_runner_builds_every_firmware_project() -> None:
    assert run_tests.FIRMWARE_PROJECTS == (
        "CromaLED_Gateway",
        "AREA_LZ7_Gateway",
        "AS7341_Gateway",
    )


def test_security_pretty_runner_references_existing_pytest_nodes() -> None:
    for _label, node in run_security_tests.CHECKS:
        file_part = node.split("::", 1)[0]
        assert (ROOT / file_part).is_file(), node


def test_windows_validation_wrappers_are_grouped_under_tests_and_pause() -> None:
    wrappers = (
        "run-validation-menu.bat",
        "run-tests.bat",
        "run-firmware-tests.bat",
        "run-all-tests.bat",
        "run-security-tests.bat",
        "run-live-bootstrap-tests.bat",
        "run-live-mqtt-acl-test.bat",
        "run-live-revocation-test.bat",
        "benchmark-simulated.bat",
        "benchmark-real.bat",
    )
    for wrapper in wrappers:
        path = ROOT / "tests" / wrapper
        assert path.is_file(), wrapper
        text = path.read_text(encoding="utf-8", errors="replace").lower()
        assert "pause" in text, wrapper
    for wrapper in wrappers[1:]:
        assert not (ROOT / wrapper).exists(), f"{wrapper} should live only under tests/"


def test_platform_version_file_matches_validation_release() -> None:
    assert (ROOT / "VERSION").read_text(encoding="utf-8").strip() == "1.1.0"


def test_simulated_benchmark_purges_existing_simulators_by_default(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["benchmark_simulated_fleet.py"])
    args = benchmark_simulated_fleet.build_parser().parse_args()
    assert args.keep_existing is False


def test_simulated_benchmark_cleanup_uses_simulator_reset_registry_purge_and_broker_restart(monkeypatch) -> None:
    commands: list[list[str]] = []

    class Completed:
        returncode = 0
        stdout = "ok"

    def fake_run(command, **kwargs):
        commands.append(list(command))
        return Completed()

    monkeypatch.setattr(benchmark_simulated_fleet.subprocess, "run", fake_run)
    monkeypatch.setattr(benchmark_simulated_fleet, "_wait_for_tcp", lambda host, port: None)

    benchmark_simulated_fleet.purge_existing_simulated_devices(
        mqtt_host="127.0.0.1",
        mqtt_port=8883,
    )

    assert commands[0][:5] == ["docker", "compose", "exec", "-T", "simulator-manager"]
    assert "purge-simulated" in commands[1]
    assert "--no-broker-restart" in commands[1]
    assert commands[2] == ["docker", "compose", "restart", "broker"]



def test_simulated_benchmark_has_connection_watchdog_and_retries(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["benchmark_simulated_fleet.py"])
    args = benchmark_simulated_fleet.build_parser().parse_args()
    assert args.timeout == 240.0
    assert args.mqtt_connect_timeout == 90.0
    assert args.launch_delay == 0.08
    assert args.client_retries == 2
    assert args.progress_interval == 10.0


def test_simulated_device_uses_async_mqtt_initial_connection() -> None:
    source = (ROOT / "simulators" / "simulated_device.py").read_text(encoding="utf-8")
    assert "client.connect_async(host, port, keepalive=45)" in source
    assert "self.connected.wait(self.args.mqtt_connect_timeout)" in source
    assert '"--mqtt-connect-timeout"' in source

def test_cromaled_uart_naming_is_current() -> None:
    assert (ROOT / "docs" / "cromaled_uart.md").is_file()
    paths = [
        ROOT / "firmware" / "esp32" / "CromaLED_Gateway" / "README.md",
        ROOT / "firmware" / "esp32" / "CromaLED_Gateway" / "src" / "README.md",
        ROOT / "docs" / "cromaled_uart.md",
    ]
    for path in paths:
        assert "legacy" not in path.read_text(encoding="utf-8", errors="replace").lower()
