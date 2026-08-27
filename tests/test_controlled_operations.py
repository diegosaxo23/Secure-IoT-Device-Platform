from __future__ import annotations

import sys
from pathlib import Path

from cryptography.fernet import Fernet
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session


def test_simulation_manager_starts_disabled(tmp_path, monkeypatch) -> None:
    project_root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(project_root))
    import simulators.manager as manager

    monkeypatch.setattr(manager, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(manager, "ADMIN_PASSWORD", "test-password")
    manager.instances.clear()
    manager.simulation_enabled = False

    status = manager.status()
    assert status["enabled"] is False
    assert status["running"] == 0

    enabled = manager.enable_simulation()
    assert enabled["enabled"] is True
    assert manager.status()["enabled"] is True

    disabled = manager.disable_simulation()
    assert disabled["enabled"] is False
    assert manager.status()["enabled"] is False


def test_project_reset_keeps_revocation_tombstone(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_PASSWORD", "test-password")
    monkeypatch.setenv("BOOTSTRAP_MASTER_KEY", Fernet.generate_key().decode("ascii"))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'settings.db'}")
    monkeypatch.setenv("MQTT_ENABLED", "false")
    from app.config import get_settings
    get_settings.cache_clear()
    from app.database import Base
    from app.models import BootstrapSession, Command, Device, MqttEvent, RevokedCertificate
    from app import project_reset
    from app.time_utils import utcnow

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    # CRL generation is exercised in the PKI tests. Here we isolate reset database semantics.
    monkeypatch.setattr(project_reset, "rebuild_crl_from_db", lambda _db: None)

    with Session(engine) as db:
        device = Device(
            device_id="CROMALED-RESET-0001",
            family="CromaLED",
            deployment_type="physical",
            bootstrap_secret_encrypted="encrypted-test-value",
            lifecycle_status="provisioned",
            certificate_serial="ABC123",
            certificate_pem="test-certificate",
            online=True,
        )
        db.add(device)
        db.flush()
        db.add(
            BootstrapSession(
                session_id="session-1",
                device_id=device.device_id,
                nonce_b64="nonce",
                expires_at=utcnow(),
            )
        )
        db.add(
            MqttEvent(
                device_id=device.device_id,
                kind="telemetry",
                topic=f"devices/{device.device_id}/telemetry",
                payload="{}",
            )
        )
        db.add(
            Command(
                command_id="command-1",
                device_id=device.device_id,
                command_name="ping",
            )
        )
        db.commit()

        result = project_reset.clear_runtime_database(db)

        assert result["devices"] == 1
        assert result["new_revocations"] == 1
        assert db.scalars(select(Device)).all() == []
        assert db.scalars(select(BootstrapSession)).all() == []
        assert db.scalars(select(MqttEvent)).all() == []
        assert db.scalars(select(Command)).all() == []

        tombstone = db.get(RevokedCertificate, "ABC123")
        assert tombstone is not None
        assert tombstone.device_id == "CROMALED-RESET-0001"
        assert tombstone.reason == "cessation_of_operation"


def test_simulation_manager_allows_multiple_families_concurrently(tmp_path, monkeypatch) -> None:
    project_root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(project_root))
    import simulators.manager as manager

    class DummyProcess:
        def poll(self):
            return None

        def terminate(self):
            return None

        def wait(self, timeout=None):
            return 0

        def kill(self):
            return None

    class DummyLog:
        def close(self):
            return None

    def fake_start(device_id: str, family: str, interval: float):
        return manager.ManagedProcess(
            device_id=device_id,
            family=family,
            process=DummyProcess(),
            log_handle=DummyLog(),
        )

    monkeypatch.setattr(manager, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(manager, "ADMIN_PASSWORD", "test-password")
    monkeypatch.setattr(manager, "_start_instance", fake_start)
    manager.instances.clear()
    manager.simulation_enabled = True

    manager.start_fleet(manager.StartRequest(family="cromaled", count=3, interval=5.0))
    manager.start_fleet(manager.StartRequest(family="area_lz7", count=2, interval=5.0))
    manager.start_fleet(manager.StartRequest(family="as7341", count=4, interval=5.0))
    manager.start_fleet(manager.StartRequest(family="cromaled", count=2, interval=5.0))

    status = manager.status()
    assert status["running"] == 11
    assert sum(1 for row in status["instances"] if row["family"] == "CromaLED") == 5
    assert sum(1 for row in status["instances"] if row["family"] == "AREA LZ7") == 2
    assert sum(1 for row in status["instances"] if row["family"] == "AS7341") == 4


def test_manufacturing_page_is_always_on_and_programming_stays_explicit() -> None:
    project_root = Path(__file__).resolve().parents[1]
    template = (project_root / "server" / "app" / "templates" / "manufacturing.html").read_text(encoding="utf-8")
    assert "Program Device" in template
    assert "selected source project" in template.lower()
    assert "firmware/bin" not in template.lower()
    assert "starts automatically" not in template
    assert "Start Manufacturing Agent" not in template
    assert "Stop Manufacturing Agent" not in template
    assert "Enable Manufacturing" not in template
    assert "Disable Manufacturing" not in template




def test_manufacturing_submit_captures_form_before_disabling_controls() -> None:
    project_root = Path(__file__).resolve().parents[1]
    template = (project_root / "server" / "app" / "templates" / "manufacturing.html").read_text(encoding="utf-8")
    submit_start = template.index('programForm.addEventListener("submit"')
    submit_block = template[submit_start:]
    capture = submit_block.index('const submission = new FormData(programForm);')
    disable = submit_block.index('setFormEnabled(true, false, portSelect.options.length > 0);')
    assert capture < disable
    assert 'body: submission' in submit_block
    assert 'new AbortController()' in submit_block
    assert '[UI ERROR]' in submit_block


def test_manufacturing_status_refresh_does_not_cancel_submit_guard() -> None:
    project_root = Path(__file__).resolve().parents[1]
    template = (project_root / "server" / "app" / "templates" / "manufacturing.html").read_text(encoding="utf-8")
    refresh_start = template.index('async function refreshManufacturing()')
    submit_start = template.index('programForm.addEventListener("submit"')
    refresh_block = template[refresh_start:submit_start]
    assert 'submitInFlight = false;' not in refresh_block


def test_device_detail_has_cyclic_fleet_navigation_and_fast_refresh() -> None:
    project_root = Path(__file__).resolve().parents[1]
    template = (project_root / "server" / "app" / "templates" / "device.html").read_text(encoding="utf-8")
    router = (project_root / "server" / "app" / "routers" / "dashboard.py").read_text(encoding="utf-8")
    assert "previous_device_id" in template
    assert "next_device_id" in template
    assert "fleet_position" in template
    assert "setInterval(refreshRuntime, 1000)" in template
    assert "(fleet_index - 1) % len(fleet_ids)" in router
    assert "(fleet_index + 1) % len(fleet_ids)" in router


def test_dashboard_pages_use_fast_background_refresh() -> None:
    project_root = Path(__file__).resolve().parents[1]
    fleet = (project_root / "server" / "app" / "templates" / "index.html").read_text(encoding="utf-8")
    simulation = (project_root / "server" / "app" / "templates" / "simulation.html").read_text(encoding="utf-8")
    manufacturing = (project_root / "server" / "app" / "templates" / "manufacturing.html").read_text(encoding="utf-8")
    assert "setInterval(refreshDevices, 1500)" in fleet
    assert "setInterval(refreshManager, 1500)" in simulation
    assert "setInterval(refreshManufacturing, 1000)" in manufacturing



def test_lighting_dashboard_separates_current_values_from_slider_setpoints() -> None:
    project_root = Path(__file__).resolve().parents[1]
    template = (project_root / "server" / "app" / "templates" / "device.html").read_text(encoding="utf-8")

    assert 'class="channel-current-value"' in template
    assert 'class="channel-target-value"' in template
    assert 'Current values are updated from device telemetry' in template
    update_start = template.index('function updateLighting(state)')
    update_end = template.index('function updateSpectrum(state)')
    update_block = template[update_start:update_end]
    assert 'channel-current-value' in update_block
    assert 'channel-target-value' in update_block
    assert 'targetInitialized' in update_block
    assert '.querySelector(".channel-range").value = level' not in update_block
    assert 'Subsequent telemetry polls update Current but never move the slider.' in update_block


def test_destructive_actions_use_password_confirmation_modals() -> None:
    project_root = Path(__file__).resolve().parents[1]
    fleet = (project_root / "server" / "app" / "templates" / "index.html").read_text(encoding="utf-8")
    device = (project_root / "server" / "app" / "templates" / "device.html").read_text(encoding="utf-8")
    router = (project_root / "server" / "app" / "routers" / "dashboard.py").read_text(encoding="utf-8")

    assert '<dialog id="reset-project-modal"' in fleet
    assert 'id="open-reset-project-modal"' in fleet
    assert 'name="dashboard_password"' in fleet
    assert 'Confirm reset' in fleet
    assert 'onsubmit="return confirm(' not in fleet

    assert '<dialog id="revoke-certificate-modal"' in device
    assert 'id="open-revoke-certificate-modal"' in device
    assert 'name="dashboard_password"' in device
    assert 'Confirm revocation' in device
    assert 'verify_dashboard_password(dashboard_password)' in router


def test_recent_commands_show_live_tx_and_rx_activity() -> None:
    project_root = Path(__file__).resolve().parents[1]
    template = (project_root / "server" / "app" / "templates" / "device.html").read_text(encoding="utf-8")
    admin = (project_root / "server" / "app" / "routers" / "admin.py").read_text(encoding="utf-8")

    assert 'id="recent-command-body"' in template
    assert 'traffic-direction tx' in template
    assert 'traffic-direction rx' in template
    assert 'refreshCommandActivity()' in template
    assert 'setInterval(refreshCommandActivity, 1000)' in template
    assert '/devices/{device_id}/commands/recent' in admin
    assert 'MqttEvent.kind == "response"' in admin
    assert 'direction="TX"' in admin
    assert 'direction="RX"' in admin


def test_windows_launcher_keeps_errors_visible_and_agent_is_reverified() -> None:
    project_root = Path(__file__).resolve().parents[1]
    launcher = (project_root / "start-platform.bat").read_text(encoding="ascii")
    starter = (project_root / "scripts" / "start_platform.py").read_text(encoding="utf-8")

    assert "pause" in launcher.lower()
    assert "manufacturing-agent.log" in launcher
    assert "where py" in launcher.lower()
    assert "where python" in launcher.lower()
    assert "Docker startup will continue" in starter
    assert "agent_port_open" in starter
    assert "stop_stale_agent_listener" in starter
    assert "MANUFACTURING_AGENT_TOKEN_RUNTIME" in starter
    assert "ensure_initialized(args.env_file)" in starter
    assert ".env was not found; initializing a fresh project automatically" in starter
    assert ".env is missing but existing PKI/database state was found" in starter


def test_manufacturing_profile_selects_source_and_live_console() -> None:
    project_root = Path(__file__).resolve().parents[1]
    template = (project_root / "server" / "app" / "templates" / "manufacturing.html").read_text(encoding="utf-8")
    factory = (project_root / "scripts" / "factory_program_esp32.py").read_text(encoding="utf-8")
    agent = (project_root / "scripts" / "manufacturing_agent.py").read_text(encoding="utf-8")
    router = (project_root / "server" / "app" / "routers" / "manufacturing.py").read_text(encoding="utf-8")

    assert "firmware/esp32/CromaLED_Gateway" in router
    assert "firmware/esp32/AREA_LZ7_Gateway" in router
    assert "firmware/esp32/AS7341_Gateway" in router
    assert "--firmware-dir" not in factory
    assert "profile.firmware_dirname" in factory
    assert "manufacturing-live-log" in template
    assert "Checking PlatformIO" in agent
    assert 'self.path == "/job"' in agent
    assert "subprocess.Popen" in agent


def test_platformio_libraries_bypass_registry_mirror_for_common_dependencies() -> None:
    project_root = Path(__file__).resolve().parents[1]
    projects = ["CromaLED_Gateway", "AREA_LZ7_Gateway", "AS7341_Gateway"]
    for project in projects:
        config = (project_root / "firmware" / "esp32" / project / "platformio.ini").read_text(encoding="utf-8")
        assert "knolleary/PubSubClient @" not in config
        assert "bblanchon/ArduinoJson @" not in config
        assert "github.com/knolleary/pubsubclient" in config
        assert "github.com/bblanchon/ArduinoJson" in config

    factory = (project_root / "scripts" / "factory_program_esp32.py").read_text(encoding="utf-8")
    assert "retrying once in 2 seconds" in factory


def test_stop_platform_batch_file_stops_docker_and_host_agent() -> None:
    project_root = Path(__file__).resolve().parents[1]
    stop_bat = (project_root / "stop-platform.bat").read_text(encoding="ascii")
    starter = (project_root / "scripts" / "start_platform.py").read_text(encoding="utf-8")
    assert "--stop-platform" in stop_bat
    assert '"compose", "down", "--remove-orphans"' in starter
    assert "stop_host_services()" in starter


def test_cromaled_dashboard_shows_live_temperature_without_affecting_other_profiles() -> None:
    project_root = Path(__file__).resolve().parents[1]
    template = (project_root / "server" / "app" / "templates" / "device.html").read_text(encoding="utf-8")

    assert "{% if profile.slug == 'cromaled' %}" in template
    assert 'id="cromaled-temperature-value"' in template
    assert 'id="cromaled-temperature-status"' in template
    assert 'function updateCromaTemperature(state)' in template
    assert 'measurements.temperature_c ?? state?.temperature_c' in template
    assert 'profile.slug === "cromaled"' in template


def test_mqtt_identity_is_bound_to_certificate_cn_and_firmware_device_id() -> None:
    project_root = Path(__file__).resolve().parents[1]
    broker = (project_root / "broker" / "mosquitto.conf").read_text(encoding="utf-8")
    compose = (project_root / "docker-compose.yml").read_text(encoding="utf-8")
    assert "use_identity_as_username true" in broker
    assert "use_username_as_clientid true" in broker
    assert "/mosquitto/certs/healthcheck/healthcheck.crt" in compose

    for gateway in ("CromaLED_Gateway", "AREA_LZ7_Gateway", "AS7341_Gateway"):
        agent = (project_root / "firmware" / "esp32" / gateway / "src" / "BootstrapAgent.cpp").read_text(encoding="utf-8")
        assert "received.mqtt.clientId != bootstrapIdentity_.deviceId" in agent
        assert "Provisioned MQTT client_id does not match device_id" in agent


def test_revocation_forces_broker_reauthentication_instead_of_spoofing_client_id() -> None:
    project_root = Path(__file__).resolve().parents[1]
    mqtt_service = (project_root / "server" / "app" / "mqtt_service.py").read_text(encoding="utf-8")
    entrypoint = (project_root / "broker" / "docker-entrypoint-platform.sh").read_text(encoding="utf-8")
    assert "broker_restart_request_path" in mqtt_service
    assert "certificate-revoked" in mqtt_service
    assert "client_id=device_id" not in mqtt_service
    assert "Security restart requested" in entrypoint
    assert "kill -TERM" in entrypoint


def test_mqtt_device_acl_is_scoped_to_authenticated_username() -> None:
    project_root = Path(__file__).resolve().parents[1]
    acl = (project_root / "broker" / "acl").read_text(encoding="utf-8")

    expected_device_rules = {
        "pattern write devices/%u/status",
        "pattern write devices/%u/telemetry",
        "pattern write devices/%u/response",
        "pattern read devices/%u/command",
        "pattern read devices/%u/config",
    }
    for rule in expected_device_rules:
        assert rule in acl

    # Device identities must never receive a wildcard rule for the full fleet.
    device_section = acl.split("# Each device publishes only to its own branch", 1)[1]
    assert "devices/#" not in device_section
    assert "pattern write devices/%u/command" not in device_section
    assert "pattern read devices/%u/telemetry" not in device_section


def test_simulated_device_cleanup_preserves_physical_fleet(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_PASSWORD", "test-password")
    monkeypatch.setenv("BOOTSTRAP_MASTER_KEY", Fernet.generate_key().decode("ascii"))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'settings-sim-purge.db'}")
    monkeypatch.setenv("MQTT_ENABLED", "false")
    from app.config import get_settings
    get_settings.cache_clear()
    from app.database import Base
    from app.models import BootstrapSession, Command, Device, MqttEvent, RevokedCertificate
    from app import project_reset
    from app.time_utils import utcnow

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(project_reset, "rebuild_crl_from_db", lambda _db: None)

    with Session(engine) as db:
        physical = Device(
            device_id="CROMALED-PHYSICAL-0001",
            family="CromaLED",
            deployment_type="physical",
            bootstrap_secret_encrypted="physical-secret",
            lifecycle_status="provisioned",
            certificate_serial="PHY123",
            certificate_pem="physical-certificate",
        )
        simulated = Device(
            device_id="CLED-SIM-0001",
            family="CromaLED",
            deployment_type="simulated",
            bootstrap_secret_encrypted="simulated-secret",
            lifecycle_status="provisioned",
            certificate_serial="SIM123",
            certificate_pem="simulated-certificate",
        )
        db.add_all([physical, simulated])
        db.flush()
        db.add(
            BootstrapSession(
                session_id="sim-session",
                device_id=simulated.device_id,
                nonce_b64="nonce",
                expires_at=utcnow(),
            )
        )
        db.add(
            MqttEvent(
                device_id=simulated.device_id,
                kind="telemetry",
                topic=f"devices/{simulated.device_id}/telemetry",
                payload="{}",
            )
        )
        db.add(
            Command(
                command_id="sim-command",
                device_id=simulated.device_id,
                command_name="ping",
            )
        )
        db.commit()

        result = project_reset.clear_simulated_devices(db)

        assert result["devices"] == 1
        assert result["bootstrap_sessions"] == 1
        assert result["events"] == 1
        assert result["commands"] == 1
        assert result["new_revocations"] == 1
        assert db.get(Device, physical.device_id) is not None
        assert db.get(Device, simulated.device_id) is None
        assert db.get(RevokedCertificate, "SIM123") is not None
        assert db.get(RevokedCertificate, "PHY123") is None
