#!/usr/bin/env python3
"""Complete ESP32 manufacturing station: flash firmware and provision identity.

Default flow:
  1. Select the product profile and serial port.
  2. Synchronize the public bootstrap CA into a local git-ignored build cache.
  3. Generate/cache the common build configuration (host Wi-Fi IP + IoT Wi-Fi).
  4. Compare source/configuration fingerprints and reuse the previous build when safe.
  5. Resolve only missing/changed dependencies, build incrementally when needed, and flash.
  6. Wait for FACTORY_READY and verify the compiled product family.
  7. Register the physical device in the administration API.
  8. Receive the one-time bootstrap secret in memory.
  9. Transfer device_id + bootstrap secret to NVS over the serial factory link.
 10. Let the ESP32 continue with HMAC bootstrap, P-256 key generation, CSR,
     X.509 enrollment, and MQTT/mTLS.

The individual bootstrap secret is not compiled into the firmware, printed, or
persisted by this station.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import factory_provision_esp32 as provision
from network_config import describe_candidates, select_wifi_ipv4


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"
DEFAULT_CA_FILE = PROJECT_ROOT / "pki" / "ca" / "ca.crt"
DEFAULT_TIME_PUBLIC_KEY_FILE = PROJECT_ROOT / "pki" / "time" / "time-signing.pub"
DEFAULT_BUILD_HEADER_NAME = "FactoryBuildConfig.h"
BUILD_CACHE_DIRNAME = ".factory-build-cache"
BUILD_STATE_FILENAME = "build-state.json"
BUILD_STATE_VERSION = 1


class FactoryProgrammingError(provision.FactoryProvisioningError):
    """Controlled error raised by the complete programming station."""


@dataclass(frozen=True)
class ProductProfile:
    key: str
    family: str
    firmware_version: str
    label: str
    firmware_dirname: str


PRODUCT_PROFILES: tuple[ProductProfile, ...] = (
    ProductProfile("cromaled", "CromaLED", "cromaled-1.1.1", "CromaLED", "CromaLED_Gateway"),
    ProductProfile("area_lz7", "AREA LZ7", "area-lz7-1.1.1", "AREA LZ7", "AREA_LZ7_Gateway"),
    ProductProfile("as7341", "AS7341", "as7341-1.1.1", "AS7341", "AS7341_Gateway"),
)

PROFILE_ALIASES: dict[str, str] = {
    "1": "cromaled",
    "cromaled": "cromaled",
    "cled": "cromaled",
    "2": "area_lz7",
    "area": "area_lz7",
    "arealz7": "area_lz7",
    "area-lz7": "area_lz7",
    "area_lz7": "area_lz7",
    "3": "as7341",
    "as7341": "as7341",
}


def normalize_profile(value: str) -> ProductProfile:
    normalized = value.strip().lower().replace(" ", "")
    key = PROFILE_ALIASES.get(normalized) or PROFILE_ALIASES.get(value.strip().lower())
    if key is None:
        valid = ", ".join(profile.key for profile in PRODUCT_PROFILES)
        raise FactoryProgrammingError(f"Invalid device profile: {value}. Available profiles: {valid}")
    return next(profile for profile in PRODUCT_PROFILES if profile.key == key)


def select_profile(explicit: str | None, *, non_interactive: bool = False) -> ProductProfile:
    if explicit:
        return normalize_profile(explicit)
    if non_interactive or not sys.stdin.isatty():
        raise FactoryProgrammingError("Provide --profile in non-interactive mode")

    print("\nSelect device profile:")
    for index, profile in enumerate(PRODUCT_PROFILES, start=1):
        print(f"  {index}. {profile.label}")
    while True:
        choice = input("Device profile: ").strip()
        try:
            return normalize_profile(choice)
        except FactoryProgrammingError:
            print("Invalid selection.")


def api_host_and_port(api_url: str) -> tuple[str, int]:
    parsed = urllib.parse.urlsplit(api_url)
    if parsed.hostname is None:
        raise FactoryProgrammingError("The API URL does not contain a valid hostname")
    return parsed.hostname, parsed.port or 443


def validate_host_network(api_url: str, env: dict[str, str]) -> None:
    automatic = env.get("AUTO_NETWORK_SYNC", "true").strip().lower() not in {"0", "false", "no", "off"}
    if not automatic:
        return
    try:
        selected, candidates = select_wifi_ipv4()
    except RuntimeError as exc:
        raise FactoryProgrammingError(f"Could not determine the active host Wi-Fi IPv4: {exc}") from exc
    parsed = urllib.parse.urlsplit(api_url)
    host = parsed.hostname or ""
    try:
        import ipaddress
        ipaddress.ip_address(host)
    except ValueError:
        return
    if host != selected:
        raise FactoryProgrammingError(
            f"The platform is configured for {host}, but the active Wi-Fi IPv4 is {selected}. "
            f"Detected Wi-Fi interfaces: {describe_candidates(candidates)}. "
            "Run start-platform.bat again so .env and the API/broker TLS certificates are synchronized "
            "with the current Wi-Fi adapter before programming a device."
        )
    print(f"[PREFLIGHT] Host active Wi-Fi IPv4 matches platform configuration: {selected}")


def preflight_api(api_url: str, ca_file: Path, timeout: float) -> None:
    context = provision.make_ssl_context(ca_file)
    request = urllib.request.Request(api_url.rstrip("/") + "/health", headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, context=context, timeout=timeout) as response:
            if response.status != 200:
                raise FactoryProgrammingError(f"API health check returned HTTP {response.status}")
            response.read(4096)
    except Exception as exc:
        if isinstance(exc, FactoryProgrammingError):
            raise
        raise FactoryProgrammingError(
            f"The platform API is not reachable with the configured CA at {api_url}: {exc}. "
            "The ESP32 flash was not erased."
        ) from exc
    print(f"[PREFLIGHT] API TLS health check passed: {api_url}")


def preflight_time_service(api_url: str, time_port: int, timeout: float) -> None:
    host, _ = api_host_and_port(api_url)
    url = f"http://{host}:{time_port}/health"
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                raise FactoryProgrammingError(f"local signed-time health check returned HTTP {response.status}")
            response.read(4096)
    except Exception as exc:
        if isinstance(exc, FactoryProgrammingError):
            raise
        raise FactoryProgrammingError(
            f"The local signed-time service is not reachable at {url}: {exc}. "
            "The ESP32 needs this local clock source before TLS certificate validation, "
            "especially on an isolated ESP32 access point. The flash was not erased."
        ) from exc
    print(f"[PREFLIGHT] Signed local-time service is reachable: {url}")


def _cpp_quote(value: str) -> str:
    if "\x00" in value:
        raise FactoryProgrammingError("Build configuration values cannot contain NUL bytes")
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r", "\\r")
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def render_factory_build_header(
    *,
    profile: ProductProfile,
    bootstrap_host: str,
    bootstrap_port: int,
    time_service_port: int,
    time_public_key_file: Path,
    wifi_ssid: str | None,
    wifi_password: str | None,
) -> str:
    """Render common build-time settings; never include per-device secrets."""
    if not time_public_key_file.is_file():
        raise FactoryProgrammingError(f"Local-time public key does not exist: {time_public_key_file}")
    public_key_pem = time_public_key_file.read_text(encoding="ascii").strip() + "\n"
    if "-----BEGIN PUBLIC KEY-----" not in public_key_pem or "-----END PUBLIC KEY-----" not in public_key_pem:
        raise FactoryProgrammingError("The local-time public key is not a PEM SubjectPublicKeyInfo key")
    delimiter = "IOT_TIME_PUB"
    if f"){delimiter}\"" in public_key_pem:
        raise FactoryProgrammingError("The local-time public key contains the reserved raw-string delimiter")

    lines = [
        "#pragma once",
        "",
        "/* Generated temporarily by scripts/factory_program_esp32.py. */",
        f"#define IOT_PRODUCT_FAMILY {_cpp_quote(profile.family)}",
        f"#define IOT_FIRMWARE_VERSION {_cpp_quote(profile.firmware_version)}",
        f"#define IOT_BOOTSTRAP_HOST {_cpp_quote(bootstrap_host)}",
        f"#define IOT_BOOTSTRAP_PORT {bootstrap_port}",
        f"#define IOT_TIME_SERVICE_PORT {time_service_port}",
        "#define IOT_HAVE_TIME_SIGNING_PUBLIC_KEY 1",
        f"static const char IOT_TIME_SIGNING_PUBLIC_KEY[] = R\"{delimiter}({public_key_pem}){delimiter}\";",
    ]
    if wifi_ssid is not None:
        lines.append(f"#define IOT_WIFI_SSID {_cpp_quote(wifi_ssid)}")
    if wifi_password is not None:
        lines.append(f"#define IOT_WIFI_PASSWORD {_cpp_quote(wifi_password)}")
    lines.append("")
    return "\n".join(lines)


def render_ca_header(ca_file: Path) -> str:
    """Render the public platform CA for a temporary manufacturing build."""
    if not ca_file.is_file():
        raise FactoryProgrammingError(f"Server CA file does not exist: {ca_file}")
    pem = ca_file.read_text(encoding="ascii").strip() + "\n"
    if "-----BEGIN CERTIFICATE-----" not in pem or "-----END CERTIFICATE-----" not in pem:
        raise FactoryProgrammingError("The selected CA file does not contain a PEM certificate")
    delimiter = "IOT_CA_PEM"
    if f"){delimiter}\"" in pem:
        raise FactoryProgrammingError("The PEM certificate contains the reserved raw-string delimiter")
    return (
        "#pragma once\n\n"
        "/* Generated temporarily for the selected installation. */\n"
        f"static const char IOT_BOOTSTRAP_ROOT_CA[] = R\"{delimiter}({pem}){delimiter}\";\n"
    )


def resolve_platformio(explicit: str | None) -> list[str]:
    if explicit:
        candidate = shutil.which(explicit) if os.path.sep not in explicit else explicit
        if not candidate or not Path(candidate).exists():
            raise FactoryProgrammingError(f"PlatformIO executable was not found: {explicit}")
        return [str(candidate)]

    candidate = shutil.which("pio") or shutil.which("platformio")
    if candidate:
        return [candidate]
    if importlib.util.find_spec("platformio") is not None:
        return [sys.executable, "-m", "platformio"]
    raise FactoryProgrammingError(
        "PlatformIO was not found. Install the factory dependencies with: "
        "python -m pip install -r scripts/requirements-factory.txt"
    )


def run_command(command: Sequence[str], *, cwd: Path) -> None:
    """Run an allowlisted command without invoking a shell."""
    try:
        completed = subprocess.run(list(command), cwd=str(cwd), check=False, shell=False)
    except OSError as exc:
        raise FactoryProgrammingError(f"Could not execute {command[0]}: {exc}") from exc
    if completed.returncode != 0:
        raise FactoryProgrammingError(
            f"Build/programming command exited with code {completed.returncode}"
        )


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _secret_hash(value: str | None) -> str | None:
    if value is None:
        return None
    return _sha256_bytes(value.encode("utf-8"))


def write_if_changed(path: Path, contents: str) -> bool:
    """Write a generated build input only when its bytes actually changed.

    Stable mtimes are important here: PlatformIO can then reuse the exact same
    object files for the next unit of the same product family.
    """
    encoded = contents.encode("utf-8")
    if path.is_file() and path.read_bytes() == encoded:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encoded)
    return True


def _source_files_for_fingerprint(firmware_dir: Path) -> list[Path]:
    """Return build-relevant project files, excluding generated/build caches."""
    allowed_suffixes = {
        ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".ino",
        ".s", ".S", ".ini", ".csv", ".ld", ".json", ".py",
    }
    result: list[Path] = []
    for path in firmware_dir.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(firmware_dir)
        if any(part in {".pio", BUILD_CACHE_DIRNAME, "__pycache__"} for part in rel.parts):
            continue
        if path.suffix in allowed_suffixes or path.name in {"platformio.ini", "partitions.csv"}:
            result.append(path)
    return sorted(result, key=lambda item: item.as_posix().lower())


def firmware_source_fingerprint(firmware_dir: Path) -> str:
    digest = hashlib.sha256()
    for path in _source_files_for_fingerprint(firmware_dir):
        rel = path.relative_to(firmware_dir).as_posix().encode("utf-8")
        digest.update(len(rel).to_bytes(4, "big"))
        digest.update(rel)
        data = path.read_bytes()
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def load_build_state(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def save_build_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def current_build_state(
    *,
    firmware_dir: Path,
    profile: ProductProfile,
    bootstrap_host: str,
    bootstrap_port: int,
    time_service_port: int,
    time_public_key_file: Path,
    wifi_ssid: str | None,
    wifi_password: str | None,
    ca_file: Path,
    pio_env: str,
) -> dict[str, Any]:
    platformio_ini = firmware_dir / "platformio.ini"
    source_sha = firmware_source_fingerprint(firmware_dir)
    ca_sha = _sha256_file(ca_file)
    dependency_sha = _sha256_file(platformio_ini)
    settings = {
        "schema": BUILD_STATE_VERSION,
        "profile": profile.key,
        "family": profile.family,
        "firmware_version": profile.firmware_version,
        "pio_env": pio_env,
        "source_sha256": source_sha,
        "dependency_sha256": dependency_sha,
        "bootstrap_host": bootstrap_host,
        "bootstrap_port": bootstrap_port,
        "time_service_port": time_service_port,
        "time_public_key_sha256": _sha256_file(time_public_key_file),
        "wifi_ssid": wifi_ssid,
        "wifi_password_sha256": _secret_hash(wifi_password),
        "ca_sha256": ca_sha,
    }
    canonical = json.dumps(settings, sort_keys=True, separators=(",", ":")).encode("utf-8")
    settings["build_fingerprint"] = _sha256_bytes(canonical)
    return settings


def cached_build_artifacts_exist(firmware_dir: Path, pio_env: str) -> bool:
    build_dir = firmware_dir / ".pio" / "build" / pio_env
    return (build_dir / "firmware.bin").is_file() and (build_dir / "firmware.elf").is_file()


def describe_build_cache(previous: dict[str, Any], current: dict[str, Any], *, artifacts_exist: bool) -> list[str]:
    if not previous:
        return ["no previous successful build cache"]
    reasons: list[str] = []
    if previous.get("source_sha256") != current.get("source_sha256"):
        reasons.append("firmware source/configuration changed")
    if previous.get("firmware_version") != current.get("firmware_version"):
        reasons.append("firmware version changed")
    if (previous.get("bootstrap_host"), previous.get("bootstrap_port")) != (
        current.get("bootstrap_host"), current.get("bootstrap_port")
    ):
        reasons.append("platform Wi-Fi IP / bootstrap endpoint changed")
    if previous.get("time_service_port") != current.get("time_service_port"):
        reasons.append("local signed-time service port changed")
    if previous.get("time_public_key_sha256") != current.get("time_public_key_sha256"):
        reasons.append("local signed-time verification public key changed")
    if previous.get("wifi_ssid") != current.get("wifi_ssid"):
        reasons.append("IoT Wi-Fi SSID changed")
    if previous.get("wifi_password_sha256") != current.get("wifi_password_sha256"):
        reasons.append("IoT Wi-Fi password changed")
    if previous.get("ca_sha256") != current.get("ca_sha256"):
        reasons.append("public Root CA changed")
    if previous.get("dependency_sha256") != current.get("dependency_sha256"):
        reasons.append("PlatformIO dependency/build configuration changed")
    if previous.get("pio_env") != current.get("pio_env"):
        reasons.append("PlatformIO environment changed")
    if not artifacts_exist:
        reasons.append("compiled PlatformIO artifacts are missing")
    return reasons


def read_agent_config_value(path: Path, macro: str) -> str | None:
    if not path.is_file():
        return None
    prefix = f"#define {macro} "
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith(prefix):
            value = line[len(prefix) :].strip()
            if len(value) >= 2 and value[0] == value[-1] == '"':
                return value[1:-1]
            return value
    return None


def firmware_source_dir(firmware_dir: Path) -> Path:
    """Return the directory containing AgentConfig.h and generated headers."""
    src = firmware_dir / "src"
    return src if (src / "AgentConfig.h").is_file() else firmware_dir

def validate_filesystem_partition(firmware_dir: Path) -> None:
    """Fail before flashing if the credential filesystem partition is missing."""
    partitions_file = firmware_dir / "partitions.csv"
    if not partitions_file.is_file():
        raise FactoryProgrammingError(
            f"Firmware partition table does not exist: {partitions_file}"
        )

    has_littlefs = False
    for raw_line in partitions_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = [field.strip() for field in line.split(",")]
        if len(fields) >= 3 and fields[0] == "littlefs" and fields[1] == "data":
            has_littlefs = True
            break

    if not has_littlefs:
        raise FactoryProgrammingError(
            "The firmware partition table must contain the data partition named "
            "'littlefs'; operational X.509 credentials are stored there."
        )


def validate_network_build_config(
    *,
    firmware_dir: Path,
    wifi_ssid: str | None,
    wifi_password: str | None,
) -> None:
    if (wifi_ssid is None) != (wifi_password is None):
        raise FactoryProgrammingError(
            "Provide --wifi-ssid and --wifi-password together, or omit both to use AgentConfig.h"
        )
    if wifi_ssid is not None:
        if not wifi_ssid:
            raise FactoryProgrammingError("--wifi-ssid cannot be empty")
        return

    configured_ssid = read_agent_config_value(firmware_source_dir(firmware_dir) / "AgentConfig.h", "IOT_WIFI_SSID")
    if configured_ssid in {None, "", "CHANGE_SSID"}:
        raise FactoryProgrammingError(
            "The firmware does not have Wi-Fi configured. Provide --wifi-ssid and "
            "--wifi-password, or set IOT_WIFI_SSID/IOT_WIFI_PASSWORD in .env."
        )


def clean_build_cache(firmware_dir: Path, pio_env: str) -> None:
    """Explicitly remove PlatformIO artifacts for troubleshooting/full rebuilds."""
    print("[CACHE] Full clean requested; removing PlatformIO build/dependency cache...")
    build_dir = firmware_dir / ".pio" / "build" / pio_env
    if build_dir.exists():
        shutil.rmtree(build_dir, ignore_errors=True)
        print(f"[CACHE] Removed build directory: {build_dir}")
    libdeps_dir = firmware_dir / ".pio" / "libdeps" / pio_env
    if libdeps_dir.exists():
        shutil.rmtree(libdeps_dir, ignore_errors=True)
        print(f"[CACHE] Removed dependency directory: {libdeps_dir}")


def prepare_dependencies(
    *,
    firmware_dir: Path,
    pio_env: str,
    platformio_command: Sequence[str],
    force: bool,
) -> None:
    """Resolve dependencies only when the local dependency cache needs attention."""
    libdeps_dir = firmware_dir / ".pio" / "libdeps" / pio_env
    if libdeps_dir.is_dir() and not force:
        print("[DEPENDENCY] Existing PlatformIO libraries/toolchain cache is reusable; skipping package reinstall.")
        return

    print("[DEPENDENCY] Resolving PlatformIO framework, toolchain, and project libraries...")
    print("[DEPENDENCY] Project libraries use direct GitHub archives instead of PlatformIO registry mirrors.")
    command = [*platformio_command, "pkg", "install", "-d", str(firmware_dir), "-e", pio_env]
    try:
        run_command(command, cwd=PROJECT_ROOT)
    except FactoryProgrammingError:
        print("[DEPENDENCY] First dependency attempt failed; retrying once in 2 seconds...")
        time.sleep(2)
        run_command(command, cwd=PROJECT_ROOT)
    print("[DEPENDENCY] Project dependencies are ready.")


def flash_firmware(
    *,
    firmware_dir: Path,
    pio_env: str,
    platformio_command: Sequence[str],
    serial_port: str,
    profile: ProductProfile,
    api_url: str,
    ca_file: Path,
    time_service_port: int,
    time_public_key_file: Path,
    wifi_ssid: str | None,
    wifi_password: str | None,
    erase_flash: bool,
    force_rebuild: bool,
    clean_build: bool,
) -> None:
    platformio_ini = firmware_dir / "platformio.ini"
    source_dir = firmware_source_dir(firmware_dir)
    agent_config = source_dir / "AgentConfig.h"
    if not platformio_ini.is_file() or not agent_config.is_file():
        raise FactoryProgrammingError(f"Firmware directory is not valid: {firmware_dir}")

    bootstrap_host, bootstrap_port = api_host_and_port(api_url)
    cache_dir = firmware_dir / BUILD_CACHE_DIRNAME
    build_header = cache_dir / DEFAULT_BUILD_HEADER_NAME
    ca_header = cache_dir / "bootstrap_ca.generated.h"
    state_file = cache_dir / BUILD_STATE_FILENAME
    ca_contents = render_ca_header(ca_file)
    build_contents = render_factory_build_header(
        profile=profile,
        bootstrap_host=bootstrap_host,
        bootstrap_port=bootstrap_port,
        time_service_port=time_service_port,
        time_public_key_file=time_public_key_file,
        wifi_ssid=wifi_ssid,
        wifi_password=wifi_password,
    )

    print(f"[BUILD] Product family: {profile.family}")
    print(f"[BUILD] Firmware version: {profile.firmware_version}")
    print(f"[BUILD] Bootstrap endpoint: {bootstrap_host}:{bootstrap_port}")
    print(f"[BUILD] Signed local-time endpoint: {bootstrap_host}:{time_service_port}")
    if wifi_ssid is not None:
        print(f"[BUILD] IoT Wi-Fi SSID: {wifi_ssid}")
    else:
        print("[BUILD] Wi-Fi settings: AgentConfig.h")

    # Generated common settings are deliberately outside src/ and git-ignored.
    # Keeping their bytes/mtime stable is what allows PlatformIO to reuse a build
    # across multiple physical units. Per-device secrets are never written here.
    build_header_changed = write_if_changed(build_header, build_contents)
    ca_header_changed = write_if_changed(ca_header, ca_contents)

    current = current_build_state(
        firmware_dir=firmware_dir,
        profile=profile,
        bootstrap_host=bootstrap_host,
        bootstrap_port=bootstrap_port,
        time_service_port=time_service_port,
        time_public_key_file=time_public_key_file,
        wifi_ssid=wifi_ssid,
        wifi_password=wifi_password,
        ca_file=ca_file,
        pio_env=pio_env,
    )
    previous = load_build_state(state_file)
    artifacts_exist = cached_build_artifacts_exist(firmware_dir, pio_env)
    reasons = describe_build_cache(previous, current, artifacts_exist=artifacts_exist)

    if clean_build:
        clean_build_cache(firmware_dir, pio_env)
        artifacts_exist = False
        reasons = ["operator requested --clean-build"]
    elif force_rebuild:
        reasons = ["operator requested --force-rebuild"]

    cache_hit = not reasons and not force_rebuild and not clean_build
    dependency_changed = (
        clean_build
        or not previous
        or previous.get("dependency_sha256") != current.get("dependency_sha256")
        or previous.get("pio_env") != current.get("pio_env")
        or not (firmware_dir / ".pio" / "libdeps" / pio_env).is_dir()
    )

    if cache_hit:
        print("[CACHE] Firmware source unchanged.")
        print(f"[CACHE] Platform Wi-Fi IP unchanged: {bootstrap_host}")
        print(f"[CACHE] IoT Wi-Fi unchanged: {wifi_ssid or '<AgentConfig.h>'}")
        print(f"[CACHE] Signed local-time service unchanged: {bootstrap_host}:{time_service_port}")
        print("[CACHE] Public Root CA and local-time verification key unchanged.")
        print("[CACHE] Reusing the previous compiled firmware; no full rebuild is required.")
    else:
        print("[CACHE] Firmware build must be refreshed because:")
        for reason in reasons:
            print(f"[CACHE]   - {reason}")
        if build_header_changed:
            print("[CACHE] Common build header updated (server IP and/or IoT Wi-Fi settings changed).")
        if ca_header_changed:
            print("[CACHE] Public CA build header updated.")
        print("[CACHE] PlatformIO incremental compilation will reuse unchanged framework/library objects.")

    prepare_dependencies(
        firmware_dir=firmware_dir,
        pio_env=pio_env,
        platformio_command=platformio_command,
        force=dependency_changed,
    )

    base = [*platformio_command, "run", "-d", str(firmware_dir), "-e", pio_env]

    # Build before erasing the target. On a cache hit the explicit build is skipped;
    # the upload target still performs PlatformIO's lightweight dependency check.
    if not cache_hit:
        print("[BUILD] Building selected firmware incrementally...")
        run_command(base, cwd=PROJECT_ROOT)
        if not cached_build_artifacts_exist(firmware_dir, pio_env):
            raise FactoryProgrammingError("PlatformIO reported success but firmware build artifacts are missing")
        save_build_state(state_file, current)
        print(f"[CACHE] Build cache updated for {profile.label}.")
    else:
        print(f"[BUILD] Cached {profile.label} binary is current; skipping explicit compile step.")

    if erase_flash:
        print("[FLASH] Erasing ESP32 flash...")
        run_command([*base, "-t", "erase", "--upload-port", serial_port], cwd=PROJECT_ROOT)

    print(f"[FLASH] Uploading firmware to {serial_port}...")
    run_command([*base, "-t", "upload", "--upload-port", serial_port], cwd=PROJECT_ROOT)

    # PlatformIO upload can rebuild if it independently detects a stale dependency.
    # Record the state again only after a successful upload so the next unit can reuse it.
    if cached_build_artifacts_exist(firmware_dir, pio_env):
        save_build_state(state_file, current)
    print("[FLASH] Firmware uploaded successfully.")

def program_and_provision(args: argparse.Namespace) -> provision.ReadyIdentity | None:
    """Execute the full manufacturing flow from a parsed argument namespace."""
    env = provision.parse_env(args.env_file)
    profile = select_profile(args.profile, non_interactive=args.non_interactive)
    serial_port = provision.select_port(args.port, non_interactive=args.non_interactive)
    api_url = provision.resolve_api_url(args.api_url, env)
    provision.validate_api_url(api_url)

    wifi_ssid = args.wifi_ssid or env.get("IOT_WIFI_SSID") or os.getenv("IOT_WIFI_SSID")
    wifi_password = (
        args.wifi_password or env.get("IOT_WIFI_PASSWORD") or os.getenv("IOT_WIFI_PASSWORD")
    )
    username = args.username or env.get("DASHBOARD_USERNAME") or os.getenv("DASHBOARD_USERNAME")
    password = args.password or env.get("DASHBOARD_PASSWORD") or os.getenv("DASHBOARD_PASSWORD")

    print(f"[CONFIG] Device profile: {profile.label}")
    print(f"[CONFIG] Serial port: {serial_port}")
    print(f"[CONFIG] API: {api_url}")
    time_service_port = int(env.get("TIME_PUBLIC_PORT", str(args.time_port)) or args.time_port)
    time_public_key_file = args.time_public_key
    print(f"[CONFIG] CA:  {args.ca_file}")
    print(f"[CONFIG] Signed time public key: {time_public_key_file}")

    validate_host_network(api_url, env)
    preflight_api(api_url, args.ca_file, args.api_timeout)
    preflight_time_service(api_url, time_service_port, args.api_timeout)

    firmware_dir = PROJECT_ROOT / "firmware" / "esp32" / profile.firmware_dirname
    print(f"[CONFIG] Selected source project: firmware/esp32/{profile.firmware_dirname}")

    validate_network_build_config(
        firmware_dir=firmware_dir,
        wifi_ssid=wifi_ssid,
        wifi_password=wifi_password,
    )
    validate_filesystem_partition(firmware_dir)
    platformio_command = resolve_platformio(args.platformio)
    flash_firmware(
        firmware_dir=firmware_dir,
        pio_env=args.pio_env,
        platformio_command=platformio_command,
        serial_port=serial_port,
        profile=profile,
        api_url=api_url,
        ca_file=args.ca_file,
        time_service_port=time_service_port,
        time_public_key_file=time_public_key_file,
        wifi_ssid=wifi_ssid,
        wifi_password=wifi_password,
        erase_flash=not args.no_erase,
        force_rebuild=args.force_rebuild,
        clean_build=args.clean_build,
    )

    if args.flash_only:
        print("[OK] Firmware programmed. No device registration or bootstrap secret was created.")
        return None

    if not username or not password:
        raise FactoryProgrammingError(
            "DASHBOARD_USERNAME/DASHBOARD_PASSWORD were not found. Initialize the server "
            "or provide --username and --password."
        )

    identity = provision.provision_device(
        serial_port=serial_port,
        baud=args.baud,
        api_url=api_url,
        ca_file=args.ca_file,
        username=username,
        password=password,
        display_name=args.display_name,
        reset_existing=args.reset_existing,
        serial_timeout=args.serial_timeout,
        api_timeout=args.api_timeout,
        expected_family=profile.family,
        observe_seconds=args.observe_seconds,
        require_operational_ready=True,
    )
    print("[READY] DEVICE READY - firmware, identity, bootstrap, certificate, and MQTT/mTLS verified")
    return identity


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Complete ESP32 manufacturing station: erase, build, flash, register, inject "
            "the individual bootstrap secret, and start X.509 enrollment."
        )
    )
    parser.add_argument(
        "--profile",
        help="Device profile: cromaled, area_lz7, or as7341. Prompts when omitted.",
    )
    parser.add_argument("--port", help="Serial port, for example COM5 or /dev/ttyUSB0")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--api-url", help="Server LAN URL, for example https://192.168.50.10:8443")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--ca-file", type=Path, default=DEFAULT_CA_FILE)
    parser.add_argument("--time-public-key", type=Path, default=DEFAULT_TIME_PUBLIC_KEY_FILE)
    parser.add_argument("--time-port", type=int, default=8091, help="Signed local-time service port")
    parser.add_argument("--username", help="Administrator username; defaults to .env")
    parser.add_argument("--password", help="Administrator password; defaults to .env")
    parser.add_argument("--display-name", default=None)
    parser.add_argument("--wifi-ssid", help="Common Wi-Fi SSID embedded in this firmware build")
    parser.add_argument("--wifi-password", help="Common Wi-Fi password embedded in this firmware build")
    parser.add_argument("--pio-env", default="esp32dev", help="PlatformIO environment name")
    parser.add_argument("--platformio", help="Explicit pio/platformio executable path")
    parser.add_argument(
        "--reset-existing",
        action="store_true",
        help=(
            "Re-manufacture a registered device. The server rotates its bootstrap secret, "
            "revokes the previous operational certificate, updates the CRL, and evicts MQTT."
        ),
    )
    parser.add_argument(
        "--no-erase",
        action="store_true",
        help="Skip the full flash erase before upload. Not recommended for manufacturing.",
    )
    parser.add_argument(
        "--force-rebuild",
        action="store_true",
        help="Force an incremental PlatformIO rebuild even when source/IP/Wi-Fi/CA are unchanged.",
    )
    parser.add_argument(
        "--clean-build",
        action="store_true",
        help="Delete this product's PlatformIO build/dependency cache and perform a full rebuild.",
    )
    parser.add_argument(
        "--flash-only",
        action="store_true",
        help="Build and upload firmware but do not register or provision the unit.",
    )
    parser.add_argument("--serial-timeout", type=float, default=90.0)
    parser.add_argument("--api-timeout", type=float, default=15.0)
    parser.add_argument("--observe-seconds", type=float, default=90.0)
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Require --profile and --port instead of displaying menus.",
    )
    parser.add_argument("--list-ports", action="store_true", help="List serial ports and exit")
    parser.add_argument("--list-profiles", action="store_true", help="List device profiles and exit")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.list_profiles:
            for profile in PRODUCT_PROFILES:
                print(f"{profile.key:10s} -> {profile.family} ({profile.firmware_version})")
            return 0
        if args.list_ports:
            print(provision.list_ports())
            return 0
        program_and_provision(args)
        return 0
    except (FactoryProgrammingError, provision.FactoryProvisioningError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except provision.serial.SerialException as exc:  # type: ignore[attr-defined]
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nCancelled by the user.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
