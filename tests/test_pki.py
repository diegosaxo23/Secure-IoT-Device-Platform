from __future__ import annotations

from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from app.pki import issue_device_certificate, load_and_validate_csr
from app.security import SecurityError


def make_csr(device_id: str) -> tuple[ec.EllipticCurvePrivateKey, str]:
    key = ec.generate_private_key(ec.SECP256R1())
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, device_id)]))
        .sign(key, hashes.SHA256())
    )
    return key, csr.public_bytes(serialization.Encoding.PEM).decode("ascii")


def test_issue_device_certificate(test_ca: tuple[Path, Path, x509.Certificate]) -> None:
    ca_cert_path, ca_key_path, ca_cert = test_ca
    key, csr_pem = make_csr("SENSOR-LUZ-0001")
    csr = load_and_validate_csr(csr_pem, "SENSOR-LUZ-0001")

    issued = issue_device_certificate(
        csr=csr,
        device_id="SENSOR-LUZ-0001",
        ca_cert_path=ca_cert_path,
        ca_key_path=ca_key_path,
        validity_days=30,
    )
    cert = x509.load_pem_x509_certificate(issued.pem.encode("ascii"))

    assert cert.issuer == ca_cert.subject
    assert cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == "SENSOR-LUZ-0001"
    assert ExtendedKeyUsageOID.CLIENT_AUTH in cert.extensions.get_extension_for_class(
        x509.ExtendedKeyUsage
    ).value
    assert cert.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ) == key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def test_csr_subject_is_untrusted_and_certificate_cn_comes_from_authenticated_device(
    test_ca: tuple[Path, Path, x509.Certificate],
) -> None:
    ca_cert_path, ca_key_path, _ca_cert = test_ca
    _key, csr_pem = make_csr("CROMALED-SOMEONE-ELSE")

    # A CSR may ask for any subject. The server parses the signed key request but
    # must never use that subject as the operational identity.
    csr = load_and_validate_csr(csr_pem, "CROMALED-AUTHENTICATED-0001")
    issued = issue_device_certificate(
        csr=csr,
        device_id="CROMALED-AUTHENTICATED-0001",
        ca_cert_path=ca_cert_path,
        ca_key_path=ca_key_path,
        validity_days=30,
    )
    cert = x509.load_pem_x509_certificate(issued.pem.encode("ascii"))
    assert cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == (
        "CROMALED-AUTHENTICATED-0001"
    )


def test_rebuilt_crl_is_readable_by_unprivileged_broker(tmp_path: Path, test_ca: tuple[Path, Path, x509.Certificate]) -> None:
    """mkstemp defaults to 0600; rebuilt CRLs must not keep that private mode."""
    import os
    from app.pki import rebuild_crl

    ca_cert_path, ca_key_path, _ca_cert = test_ca
    output_path = tmp_path / "crl" / "ca.crl"
    rebuild_crl(
        revoked_certificates=[],
        ca_cert_path=ca_cert_path,
        ca_key_path=ca_key_path,
        output_path=output_path,
    )

    assert output_path.exists()
    if os.name != "nt":
        assert output_path.stat().st_mode & 0o777 == 0o644
