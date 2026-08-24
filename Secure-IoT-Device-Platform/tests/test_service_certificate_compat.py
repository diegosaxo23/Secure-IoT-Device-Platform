from __future__ import annotations

import importlib.util
import ipaddress
import sys
from pathlib import Path

from cryptography import x509


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location("platform_setup", SCRIPTS / "setup.py")
assert spec and spec.loader
platform_setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(platform_setup)


def test_service_certificates_include_modern_ip_and_legacy_dns_ip_sans(tmp_path, monkeypatch) -> None:
    pki = tmp_path / "pki"
    monkeypatch.setattr(platform_setup, "PKI", pki)

    ca_key, ca_cert = platform_setup.make_ca()
    platform_setup.issue_service_certificates(
        public_ip="192.168.50.10",
        hostname="iot-host",
        ca_key=ca_key,
        ca_cert=ca_cert,
    )

    for service in ("api", "broker"):
        cert = x509.load_pem_x509_certificate((pki / service / f"{service}.crt").read_bytes())
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        assert ipaddress.ip_address("192.168.50.10") in set(san.get_values_for_type(x509.IPAddress))
        assert "192.168.50.10" in set(san.get_values_for_type(x509.DNSName))

    ok, reason = platform_setup.service_certificates_compatible(
        public_ip="192.168.50.10",
        hostname="iot-host",
        ca_cert=ca_cert,
    )
    assert ok is True, reason


def test_old_ip_san_only_certificate_is_marked_for_refresh(tmp_path, monkeypatch) -> None:
    pki = tmp_path / "pki"
    monkeypatch.setattr(platform_setup, "PKI", pki)
    ca_key, ca_cert = platform_setup.make_ca()
    address = ipaddress.ip_address("192.168.50.10")
    common_ips = [ipaddress.ip_address("127.0.0.1"), address]

    for service, common_name in (("api", "iot-host"), ("broker", "broker")):
        key, cert = platform_setup.make_leaf(
            common_name=common_name,
            ca_key=ca_key,
            ca_cert=ca_cert,
            dns_names=["localhost", service, "iot-host"],
            ip_addresses=common_ips,
            server_auth=True,
            client_auth=False,
        )
        platform_setup.write_private_key(pki / service / f"{service}.key", key)
        platform_setup.write_certificate(pki / service / f"{service}.crt", cert)

    ok, reason = platform_setup.service_certificates_compatible(
        public_ip="192.168.50.10",
        hostname="iot-host",
        ca_cert=ca_cert,
    )
    assert ok is False
    assert "missing DNS SAN(s): 192.168.50.10" in reason
