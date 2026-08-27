from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ESP32_ROOT = ROOT / "firmware" / "esp32"
GATEWAYS = ("CromaLED_Gateway", "AREA_LZ7_Gateway", "AS7341_Gateway")


def test_pubsubclient_connected_wrapper_is_non_const_in_every_gateway() -> None:
    """PubSubClient 2.x connected() is non-const; const wrappers do not compile."""
    for gateway in GATEWAYS:
        header = (ESP32_ROOT / gateway / "src" / "BootstrapAgent.h").read_text(encoding="utf-8")
        source = (ESP32_ROOT / gateway / "src" / "BootstrapAgent.cpp").read_text(encoding="utf-8")
        assert "bool isMqttConnected();" in header
        assert "bool isMqttConnected() const;" not in header
        assert "bool BootstrapAgent::isMqttConnected() { return mqttClient_.connected(); }" in source
        assert "bool BootstrapAgent::isMqttConnected() const" not in source


def test_as7341_physical_channel_mapping_matches_upstream_buffer_layout() -> None:
    source = (ESP32_ROOT / "AS7341_Gateway" / "src" / "AS7341_Gateway.ino").read_text(encoding="utf-8")
    # Adafruit_AS7341 getAllChannels() stores Clear at index 10 and NIR at index 11.
    assert 'target["CLEAR"] = spectralSensor.value(10);' in source
    assert 'target["NIR"] = spectralSensor.value(11);' in source


def test_all_gateway_projects_pin_platform_and_have_build_manifests() -> None:
    for gateway in GATEWAYS:
        project = ESP32_ROOT / gateway
        config = (project / "platformio.ini").read_text(encoding="utf-8")
        assert "platform = espressif32 @ 7.0.1" in config
        assert "board = esp32dev" in config
        assert "framework = arduino" in config
        assert (project / "partitions.csv").is_file()


def test_area_lz7_dali_dependency_is_pinned_and_timer_configuration_is_explicit() -> None:
    config = (ESP32_ROOT / "AREA_LZ7_Gateway" / "platformio.ini").read_text(encoding="utf-8")
    assert "arduino-dali/archive/862b2c255ed8d1d269ad73fd508b5d8524414d06.zip" in config
    assert "refs/heads/main.zip" not in config
    assert "-DDALI_TIMER=1" in config
    assert "-DDALI_NO_COMMISSIONING" in config


def test_as7341_vendored_driver_and_busio_are_present() -> None:
    lib = ESP32_ROOT / "AS7341_Gateway" / "lib"
    assert (lib / "Adafruit_AS7341-master" / "Adafruit_AS7341.h").is_file()
    assert (lib / "Adafruit_AS7341-master" / "Adafruit_AS7341.cpp").is_file()
    assert (lib / "Adafruit_BusIO-master" / "Adafruit_I2CDevice.h").is_file()
    assert (lib / "Adafruit_BusIO-master" / "Adafruit_BusIO_Register.cpp").is_file()


def test_ci_build_matrix_contains_all_firmware_projects() -> None:
    workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    assert "firmware-builds:" in workflow
    for gateway in GATEWAYS:
        assert f"- {gateway}" in workflow
    assert 'python -m platformio run -d "firmware/esp32/${{ matrix.project }}" -e esp32dev' in workflow
