#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ipaddress
import secrets
import shutil
import socket
import sys
from datetime import timedelta
from pathlib import Path

from cryptography import x509
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from network_config import describe_candidates, select_wifi_ipv4


ROOT = Path(__file__).resolve().parents[1]
PKI = ROOT / "pki"
APP_NAME = "IoT Device Platform"
ORG_NAME = "IoT Device Platform"
ROOT_CA_NAME = "IoT Device Platform Root CA"
DATABASE_BASENAME = "iot_device_platform.db"


def utcnow():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def write_private_key(path: Path, key) -> None:  # type: ignore[no-untyped-def]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    try:
        path.chmod(0o600)
    except OSError:
        pass


def write_certificate(path: Path, cert: x509.Certificate) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def write_public_key(path: Path, key) -> None:  # type: ignore[no-untyped-def]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    try:
        path.chmod(0o644)
    except OSError:
        pass


def ensure_time_signing_material() -> None:
    private_path = PKI / "time" / "time-signing.key"
    public_path = PKI / "time" / "time-signing.pub"
    private_exists = private_path.is_file()
    public_exists = public_path.is_file()
    if private_exists and public_exists:
        return
    if private_exists != public_exists:
        raise RuntimeError(
            "incomplete local-time signing material: expected both pki/time/time-signing.key "
            "and pki/time/time-signing.pub"
        )

    key = ec.generate_private_key(ec.SECP256R1())
    write_private_key(private_path, key)
    write_public_key(public_path, key.public_key())


def make_ca() -> tuple[ec.EllipticCurvePrivateKey, x509.Certificate]:
    key = ec.generate_private_key(ec.SECP384R1())
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "ES"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, ORG_NAME),
            x509.NameAttribute(NameOID.COMMON_NAME, ROOT_CA_NAME),
        ]
    )
    now = utcnow()
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=None,
                decipher_only=None,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), False)
        .sign(key, hashes.SHA384())
    )
    return key, cert


def make_leaf(
    *,
    common_name: str,
    ca_key,
    ca_cert: x509.Certificate,
    dns_names: list[str],
    ip_addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address],
    server_auth: bool,
    client_auth: bool,
) -> tuple[ec.EllipticCurvePrivateKey, x509.Certificate]:
    key = ec.generate_private_key(ec.SECP256R1())
    now = utcnow()
    san_entries: list[x509.GeneralName] = []
    for name in dict.fromkeys(name for name in dns_names if name):
        san_entries.append(x509.DNSName(name))
    for address in dict.fromkeys(ip_addresses):
        san_entries.append(x509.IPAddress(address))

    eku = []
    if server_auth:
        eku.append(ExtendedKeyUsageOID.SERVER_AUTH)
    if client_auth:
        eku.append(ExtendedKeyUsageOID.CLIENT_AUTH)

    builder = (
        x509.CertificateBuilder()
        .subject_name(
            x509.Name(
                [
                    x509.NameAttribute(NameOID.ORGANIZATION_NAME, ORG_NAME),
                    x509.NameAttribute(NameOID.COMMON_NAME, common_name),
                ]
            )
        )
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=825))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=None,
                decipher_only=None,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), False)
    )
    if san_entries:
        builder = builder.add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
    if eku:
        builder = builder.add_extension(x509.ExtendedKeyUsage(eku), critical=False)

    return key, builder.sign(ca_key, hashes.SHA384())


def make_empty_crl(ca_key, ca_cert: x509.Certificate) -> bytes:  # type: ignore[no-untyped-def]
    now = utcnow()
    crl = (
        x509.CertificateRevocationListBuilder()
        .issuer_name(ca_cert.subject)
        .last_update(now)
        .next_update(now + timedelta(days=7))
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), False)
        .sign(ca_key, hashes.SHA384())
    )
    return crl.public_bytes(serialization.Encoding.PEM)


def build_env(*, hostname: str, public_ip: str) -> str:
    password = secrets.token_urlsafe(18)
    master_key = Fernet.generate_key().decode("ascii")
    manufacturing_agent_token = secrets.token_urlsafe(32)
    return f'''APP_NAME="{APP_NAME}"
ENVIRONMENT=development
LOG_LEVEL=INFO
DASHBOARD_USERNAME=admin
DASHBOARD_PASSWORD={password}
BOOTSTRAP_MASTER_KEY={master_key}
DATABASE_URL=sqlite:////data/{DATABASE_BASENAME}
CHALLENGE_TTL_SECONDS=120
CERT_VALIDITY_DAYS=365
ONLINE_TIMEOUT_SECONDS=90
ALLOW_REPROVISIONING=false
NETWORK_INTERFACE_MODE=wifi
AUTO_NETWORK_SYNC=true
API_PUBLIC_HOST={public_ip}
API_PUBLIC_PORT=8443
MQTT_PUBLIC_HOST={public_ip}
MQTT_PUBLIC_PORT=8883
TIME_PUBLIC_PORT=8091
TIME_SIGNING_KEY_PATH=/pki/time/time-signing.key
MQTT_ENABLED=true
MQTT_HOST=broker
MQTT_PORT=8883
SIMULATOR_MANAGER_URL=http://simulator-manager:8090
MANUFACTURING_ENABLED=true
MANUFACTURING_AGENT_URL=http://host.docker.internal:8765
MANUFACTURING_AGENT_TOKEN={manufacturing_agent_token}
MANUFACTURING_AGENT_TIMEOUT_SECONDS=900
# Optional common Wi-Fi used by factory builds. Leave blank to require explicit configuration.
IOT_WIFI_SSID=
IOT_WIFI_PASSWORD=
CA_CERT_PATH=/pki/ca/ca.crt
CA_KEY_PATH=/pki/ca/ca.key
SERVER_CERT_PATH=/pki/api/api.crt
SERVER_KEY_PATH=/pki/api/api.key
MQTT_CA_PATH=/pki/ca/ca.crt
MQTT_CLIENT_CERT_PATH=/pki/control/control.crt
MQTT_CLIENT_KEY_PATH=/pki/control/control.key
CRL_PATH=/pki/crl/ca.crl
BROKER_RESTART_REQUEST_PATH=/data/broker/restart.request
SERVER_HOSTNAME={hostname}
'''


def parse_env_file(path: Path) -> tuple[list[str], dict[str, str]]:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    values: dict[str, str] = {}
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"\'", '"'}:
            value = value[1:-1]
        values[key.strip()] = value
    return lines, values


def set_env_values(path: Path, updates: dict[str, str]) -> None:
    lines, _ = parse_env_file(path)
    remaining = dict(updates)
    output: list[str] = []
    for raw in lines:
        if "=" in raw and not raw.lstrip().startswith("#"):
            key = raw.split("=", 1)[0].strip()
            if key in remaining:
                output.append(f"{key}={remaining.pop(key)}")
                continue
        output.append(raw)
    if remaining:
        if output and output[-1].strip():
            output.append("")
        output.extend(f"{key}={value}" for key, value in remaining.items())
    path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")


def resolve_public_ip(value: str) -> str:
    if value.strip().lower() != "auto":
        try:
            return str(ipaddress.ip_address(value))
        except ValueError as exc:
            raise ValueError(f"invalid IP address: {value}") from exc
    selected, candidates = select_wifi_ipv4()
    print(f"[NETWORK] Detected active Wi-Fi IPv4 addresses: {describe_candidates(candidates)}")
    print(f"[NETWORK] Automatically selected active Wi-Fi IPv4: {selected}")
    return selected


def _service_certificate_compatible(
    *,
    cert_path: Path,
    key_path: Path,
    ca_cert: x509.Certificate,
    required_dns_names: list[str],
    required_ip_addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address],
) -> tuple[bool, str]:
    """Validate the installed service certificate, including ESP32/mbedTLS 2.x IP compatibility.

    Arduino-ESP32 2.x ships Mbed TLS 2.28.x. That branch verifies host names
    against dNSName SAN entries but does not fully support iPAddress SAN matching.
    Modern clients, however, correctly require an iPAddress SAN when connecting
    to a literal IP. Service certificates therefore deliberately contain BOTH:

      * iPAddress: 192.168.x.x   (standards-compliant modern clients)
      * dNSName:    192.168.x.x   (legacy ESP32 Mbed TLS 2.x compatibility)

    The dNSName compatibility entry is installation-local and is never used as
    a substitute for the Root CA trust check.
    """
    if not cert_path.is_file() or not key_path.is_file():
        return False, "certificate or private key is missing"
    try:
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    except Exception as exc:  # corrupted local PKI material
        return False, f"certificate/key could not be parsed: {exc}"

    if cert.issuer != ca_cert.subject:
        return False, "certificate issuer does not match the active Root CA"

    cert_public = cert.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    key_public = key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    if cert_public != key_public:
        return False, "certificate and private key do not match"

    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return False, "certificate has no Subject Alternative Name extension"

    dns_names = set(san.get_values_for_type(x509.DNSName))
    ip_addresses = set(san.get_values_for_type(x509.IPAddress))
    missing_dns = [name for name in required_dns_names if name not in dns_names]
    missing_ips = [address for address in required_ip_addresses if address not in ip_addresses]
    if missing_dns:
        return False, "missing DNS SAN(s): " + ", ".join(missing_dns)
    if missing_ips:
        return False, "missing IP SAN(s): " + ", ".join(str(item) for item in missing_ips)

    try:
        eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    except x509.ExtensionNotFound:
        return False, "certificate has no Extended Key Usage extension"
    if ExtendedKeyUsageOID.SERVER_AUTH not in eku:
        return False, "certificate is not valid for TLS server authentication"

    now = utcnow()
    not_before = cert.not_valid_before_utc
    not_after = cert.not_valid_after_utc
    if now < not_before or now >= not_after:
        return False, "certificate is outside its validity period"
    return True, "ok"


def service_certificates_compatible(*, public_ip: str, hostname: str, ca_cert: x509.Certificate) -> tuple[bool, str]:
    address = ipaddress.ip_address(public_ip)
    common_ips = list(dict.fromkeys([ipaddress.ip_address("127.0.0.1"), address]))
    # The literal IP is intentionally also encoded as dNSName for Arduino-ESP32
    # 2.x / Mbed TLS 2.28.x. Keep the standards-compliant iPAddress SAN too.
    common_legacy_dns = ["localhost", hostname, str(address)]

    api_ok, api_reason = _service_certificate_compatible(
        cert_path=PKI / "api" / "api.crt",
        key_path=PKI / "api" / "api.key",
        ca_cert=ca_cert,
        required_dns_names=list(dict.fromkeys([*common_legacy_dns, "api"])),
        required_ip_addresses=common_ips,
    )
    if not api_ok:
        return False, f"API certificate: {api_reason}"

    broker_ok, broker_reason = _service_certificate_compatible(
        cert_path=PKI / "broker" / "broker.crt",
        key_path=PKI / "broker" / "broker.key",
        ca_cert=ca_cert,
        required_dns_names=list(dict.fromkeys([*common_legacy_dns, "broker"])),
        required_ip_addresses=common_ips,
    )
    if not broker_ok:
        return False, f"broker certificate: {broker_reason}"
    return True, "ok"


def issue_service_certificates(*, public_ip: str, hostname: str, ca_key, ca_cert: x509.Certificate) -> None:  # type: ignore[no-untyped-def]
    address = ipaddress.ip_address(public_ip)
    common_ips = list(dict.fromkeys([ipaddress.ip_address("127.0.0.1"), address]))

    # Arduino-ESP32 2.x uses Mbed TLS 2.28.x, whose certificate-name
    # verification only handles dNSName SANs reliably. Include the literal IP
    # as a dNSName compatibility SAN IN ADDITION TO the standards-compliant
    # iPAddress SAN. Modern Python/browser clients continue to use iPAddress,
    # while the ESP32 can authenticate the same endpoint without setInsecure().
    legacy_ip_dns = str(address)

    api_key, api_cert = make_leaf(
        common_name=hostname,
        ca_key=ca_key,
        ca_cert=ca_cert,
        dns_names=["localhost", "api", hostname, legacy_ip_dns],
        ip_addresses=common_ips,
        server_auth=True,
        client_auth=False,
    )
    write_private_key(PKI / "api" / "api.key", api_key)
    write_certificate(PKI / "api" / "api.crt", api_cert)

    broker_key, broker_cert = make_leaf(
        common_name="broker",
        ca_key=ca_key,
        ca_cert=ca_cert,
        dns_names=["localhost", "broker", hostname, legacy_ip_dns],
        ip_addresses=common_ips,
        server_auth=True,
        client_auth=False,
    )
    write_private_key(PKI / "broker" / "broker.key", broker_key)
    write_certificate(PKI / "broker" / "broker.crt", broker_cert)


def ensure_internal_client_certificates(*, ca_key, ca_cert: x509.Certificate) -> None:  # type: ignore[no-untyped-def]
    """Create platform-internal MQTT client certificates if they are missing.

    Existing installations are upgraded in place during normal network sync. The
    dedicated healthcheck certificate avoids sharing the control-service CN/Client ID.
    """
    definitions = (
        ("control", "control-service", "control"),
        ("healthcheck", "broker-healthcheck", "healthcheck"),
    )
    for directory, common_name, basename in definitions:
        key_path = PKI / directory / f"{basename}.key"
        cert_path = PKI / directory / f"{basename}.crt"
        if key_path.is_file() and cert_path.is_file():
            continue
        if key_path.exists() != cert_path.exists():
            raise RuntimeError(f"incomplete internal MQTT identity for {common_name}")
        key, cert = make_leaf(
            common_name=common_name,
            ca_key=ca_key,
            ca_cert=ca_cert,
            dns_names=[],
            ip_addresses=[],
            server_auth=False,
            client_auth=True,
        )
        write_private_key(key_path, key)
        write_certificate(cert_path, cert)
        print(f"[PKI] Created internal MQTT identity: {common_name}")


def sync_network(*, public_ip: str, hostname: str) -> int:
    env_path = ROOT / ".env"
    ca_key_path = PKI / "ca" / "ca.key"
    ca_cert_path = PKI / "ca" / "ca.crt"
    if not env_path.is_file() or not ca_key_path.is_file() or not ca_cert_path.is_file():
        print("ERROR: the platform is not initialized; network sync requires .env and the existing CA", file=sys.stderr)
        return 2

    ensure_time_signing_material()
    _, env = parse_env_file(env_path)
    current_api = env.get("API_PUBLIC_HOST", "")
    current_mqtt = env.get("MQTT_PUBLIC_HOST", "")
    current_hostname = env.get("SERVER_HOSTNAME", hostname) or hostname

    ca_key = serialization.load_pem_private_key(ca_key_path.read_bytes(), password=None)
    ca_cert = x509.load_pem_x509_certificate(ca_cert_path.read_bytes())
    ensure_internal_client_certificates(ca_key=ca_key, ca_cert=ca_cert)
    certs_ok, certs_reason = service_certificates_compatible(
        public_ip=public_ip,
        hostname=hostname,
        ca_cert=ca_cert,
    )
    env_matches = current_api == public_ip and current_mqtt == public_ip and current_hostname == hostname
    if env_matches and certs_ok:
        set_env_values(
            env_path,
            {
                "NETWORK_INTERFACE_MODE": "wifi",
                "AUTO_NETWORK_SYNC": "true",
            },
        )
        print(
            f"[NETWORK] Configuration and ESP32-compatible service certificates already "
            f"match active Wi-Fi IPv4 {public_ip}"
        )
        return 0

    if env_matches and not certs_ok:
        print(f"[NETWORK] Service certificate refresh required: {certs_reason}")
    else:
        print(
            f"[NETWORK] Service endpoint changed: API={current_api or '<unset>'}, "
            f"MQTT={current_mqtt or '<unset>'}, new={public_ip}"
        )

    issue_service_certificates(public_ip=public_ip, hostname=hostname, ca_key=ca_key, ca_cert=ca_cert)
    set_env_values(
        env_path,
        {
            "NETWORK_INTERFACE_MODE": "wifi",
            "AUTO_NETWORK_SYNC": "true",
            "API_PUBLIC_HOST": public_ip,
            "MQTT_PUBLIC_HOST": public_ip,
            "SERVER_HOSTNAME": hostname,
        },
    )
    print(f"[NETWORK] Updated API/MQTT address to active Wi-Fi IPv4: {public_ip}")
    print("[NETWORK] Reissued API and broker TLS certificates with IP SAN + ESP32 legacy dNSName-IP SAN using the existing Root CA")
    print("[NETWORK] Device certificates, bootstrap secrets, database state, and Root CA were preserved")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Initialize or synchronize IoT Device Platform configuration and PKI.",
    )
    parser.add_argument("--hostname", default=None, help="Server DNS name; defaults to the host name")
    parser.add_argument(
        "--ip",
        default="auto",
        help="Device-facing IPv4 address or 'auto' (default)",
    )
    parser.add_argument(
        "--sync-network",
        action="store_true",
        help="Update only API/MQTT address and service certificates, preserving the CA and database",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite existing PKI, database, and .env files")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    hostname = args.hostname or socket.gethostname() or "localhost"
    try:
        public_ip = resolve_public_ip(args.ip)
    except (ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.sync_network:
        if args.force:
            print("ERROR: --sync-network and --force cannot be used together", file=sys.stderr)
            return 2
        return sync_network(
            public_ip=public_ip,
            hostname=hostname,
        )

    env_path = ROOT / ".env"
    existing_material = env_path.exists() or (PKI / "ca" / "ca.key").exists()
    if existing_material and not args.force:
        print("ERROR: an existing initialization was found. Use --force only if you intend to replace it.", file=sys.stderr)
        return 2

    if args.force:
        for child in ("ca", "api", "broker", "control", "healthcheck", "crl", "time", "backend", "mosquitto", "dashboard"):
            shutil.rmtree(PKI / child, ignore_errors=True)
        for basename in (DATABASE_BASENAME, "iot_device_platform.db"):
            for suffix in ("", "-shm", "-wal"):
                (ROOT / "data" / f"{basename}{suffix}").unlink(missing_ok=True)

    ca_key, ca_cert = make_ca()
    write_private_key(PKI / "ca" / "ca.key", ca_key)
    write_certificate(PKI / "ca" / "ca.crt", ca_cert)
    ensure_time_signing_material()

    issue_service_certificates(
        public_ip=public_ip,
        hostname=hostname,
        ca_key=ca_key,
        ca_cert=ca_cert,
    )

    ensure_internal_client_certificates(ca_key=ca_key, ca_cert=ca_cert)

    crl_path = PKI / "crl" / "ca.crl"
    crl_path.parent.mkdir(parents=True, exist_ok=True)
    crl_path.write_bytes(make_empty_crl(ca_key, ca_cert))
    try:
        crl_path.chmod(0o644)
    except OSError:
        pass

    env_contents = build_env(
        hostname=hostname,
        public_ip=public_ip,
    )
    env_path.write_text(env_contents, encoding="utf-8")
    try:
        env_path.chmod(0o600)
    except OSError:
        pass

    password = next(
        line.split("=", 1)[1]
        for line in env_contents.splitlines()
        if line.startswith("DASHBOARD_PASSWORD=")
    )

    print("\nIoT Device Platform initialized")
    print("--------------------------------")
    print(f"Dashboard/API: https://{public_ip}:8443")
    print(f"MQTT mTLS: {public_ip}:8883")
    print(f"Signed local time: http://{public_ip}:8091")
    print("Network mode: active host Wi-Fi IPv4")
    print("Automatic Wi-Fi address synchronization: enabled")
    print("Dashboard user: admin")
    print(f"Password: {password}")
    print(f"Public CA: {PKI / 'ca' / 'ca.crt'}")
    print("\nProtect pki/ca/ca.key, pki/time/time-signing.key, and the .env file.")
    print("start-platform.bat will ask for the IoT Wi-Fi SSID/password before starting the full stack.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
