# Changelog

This file tracks the public release history. Internal development snapshots used before the first public release are intentionally omitted.

## 1.0.0 - 2026-08-24

First public stable release.

### Identity and security

- Per-device manufacturing bootstrap identity separated from common family firmware.
- One-use HMAC-SHA256 challenge bound to the DER CSR digest.
- On-device EC P-256 key generation and PKCS#10 CSR creation.
- Server-controlled X.509 subject derived from the authenticated Device ID.
- HTTPS server authentication with installation-specific Root CA.
- Signed local-time bootstrap for isolated networks without public NTP.
- MQTT/mTLS authentication with certificate-derived username and effective Client ID.
- Per-device MQTT ACL isolation.
- CRL-based individual certificate revocation and broker re-authentication.

### Hardware and application integration

- CromaLED: 11 channels, independent Current/Setpoint control, live lamp temperature, legacy UART0 hand-off at 9200 baud.
- AREA LZ7: 6-channel application integration and existing DALI transport behavior.
- AS7341: multispectral telemetry profile.

### Manufacturing and operations

- Host-side Manufacturing Agent with allowlisted product profiles.
- One-time clean installer plus separate start/stop commands for Windows and Linux.
- Smart per-product PlatformIO build cache.
- Automatic physical Wi-Fi endpoint synchronization and service-certificate refresh.
- Dashboard manufacturing console and controlled project reset.

### Simulation and validation

- Realistic simulated CromaLED, AREA LZ7 and AS7341 identities using the same bootstrap/X.509/MQTT path as physical devices.
- Explicit P-256/CSR and enrollment metric logging for physical and simulated devices.
- Standard-library metric extractor with CSV and summary output.
- Automated security/workflow tests, GitHub Actions, CodeQL and Dependabot configuration.
- Technical documentation for architecture, protocol, threat model, validation and benchmarking.
