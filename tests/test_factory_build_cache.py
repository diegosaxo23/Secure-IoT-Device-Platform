from pathlib import Path


def test_factory_program_uses_smart_build_cache_and_tracks_network_inputs() -> None:
    root = Path(__file__).resolve().parents[1]
    factory = (root / "scripts" / "factory_program_esp32.py").read_text(encoding="utf-8")

    assert 'BUILD_CACHE_DIRNAME = ".factory-build-cache"' in factory
    assert '"bootstrap_host": bootstrap_host' in factory
    assert '"time_service_port": time_service_port' in factory
    assert '"time_public_key_sha256": _sha256_file(time_public_key_file)' in factory
    assert '"wifi_ssid": wifi_ssid' in factory
    assert '"wifi_password_sha256": _secret_hash(wifi_password)' in factory
    assert '"ca_sha256": ca_sha' in factory
    assert "cached_build_artifacts_exist" in factory
    assert "skipping explicit compile step" in factory
    assert "PlatformIO incremental compilation" in factory
    assert '"--force-rebuild"' in factory
    assert '"--clean-build"' in factory


def test_firmware_reads_generated_config_from_gitignored_cache() -> None:
    root = Path(__file__).resolve().parents[1]
    gitignore = (root / ".gitignore").read_text(encoding="utf-8")
    assert "firmware/esp32/*/.factory-build-cache/" in gitignore

    for project in ("CromaLED_Gateway", "AREA_LZ7_Gateway", "AS7341_Gateway"):
        src = root / "firmware" / "esp32" / project / "src"
        agent = (src / "AgentConfig.h").read_text(encoding="utf-8")
        ca = (src / "bootstrap_ca.h").read_text(encoding="utf-8")
        assert '../.factory-build-cache/FactoryBuildConfig.h' in agent
        assert '../.factory-build-cache/bootstrap_ca.generated.h' in ca
