# Changelog

This file tracks the public release history. Internal development snapshots used before the first public release are intentionally omitted.

## Unreleased

_No unreleased changes._

## 1.1.1 - 2026-08-27

### AREA LZ7 build compatibility and release metadata

- Replaced the AREA LZ7 `arduino-dali` / `TimerInterrupt_Generic` dependency chain with a compact native ESP32 hardware-timer DALI direct-arc transmitter.
- Kept the real AREA LZ7 PlatformIO build enabled in GitHub Actions instead of excluding or disabling the failing firmware test.
- Removed the deep legacy DALI/timer dependency chain that caused AREA LZ7 PlatformIO builds to hit Windows path-length limits in long checkout/extraction paths.
- Reduced AREA LZ7 PlatformIO dependency depth, which also avoids the excessively long nested build paths seen on Windows when the repository is extracted below a long directory name.
- Preserved the deployed AREA LZ7 GPIO contract: DALI TX on GPIO17 and DALI RX reserved on GPIO16.
- Updated platform, firmware, simulator, API and release metadata to v1.1.1.

## 1.1.0 - 2026-08-27

### Validation automation, benchmark isolation and firmware fixes

- Fixed AREA LZ7 and AS7341 compilation regression caused by declaring the PubSubClient `connected()` wrapper as `const`.
- Pinned the AREA LZ7 DALI dependency to a reproducible upstream commit and added real PlatformIO builds for all three gateways to CI.
- Corrected AS7341 Clear/NIR channel mapping (`Clear=10`, `NIR=11`).
- Added adversarial enrollment coverage for wrong bootstrap secrets, superseded challenges, CSR substitution and replay.
- Added MQTT per-device ACL regression coverage plus live broker ACL and revocation validation utilities.
- Fixed metric extraction from Windows PowerShell UTF-16/UTF-16LE logs and prevented malformed serial bytes from aborting redirected manufacturing output on Windows consoles.
- Grouped all Windows validation and benchmark launchers under `tests/`, added an interactive validation menu and ensured every launcher pauses at completion.
- Added automatic CSV output for pytest, security checks, live validation, simulated benchmarks and repeated physical-device benchmarks.
- Added automated 1/10/25/50 simulated-fleet benchmarks and repeated physical-device benchmark campaigns using the normal factory programmer and `[METRIC]` extraction.
- The simulated benchmark now removes the previous simulated fleet before each scale point while preserving physical-device registrations; removed simulated certificates remain represented in the CRL and Mosquitto is restarted before the next measurement.
- Added `admin.py purge-simulated` for targeted simulated-fleet cleanup and regressions covering cleanup, physical-device preservation, benchmark orchestration and current CromaLED UART naming.
- The final 50-device benchmark fleet remains registered for dashboard inspection; `--keep-existing` can explicitly disable cleanup.
- Refreshed README, validation, benchmarking, security-matrix, test and release documentation to describe the complete 72-test suite, 8-control security report, live adversarial checks and automatic CSV workflow.
- Renamed CromaLED UART documentation and internal symbols to describe the current UART0 lamp interface directly while preserving the 9200-baud product behavior.

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

- CromaLED: 11 channels, independent Current/Setpoint control, live lamp temperature, and UART0 lamp hand-off at 9200 baud.
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
