# v1.0.0 - First Public Stable Release

Secure IoT Device Platform v1.0.0 is the first public stable release of the complete ESP32 device-identity testbed.

## Highlights

- Common firmware per product family with per-device manufacturing identity.
- HMAC-SHA256 bootstrap proof bound to the exact CSR digest.
- Local EC P-256 private-key generation and X.509 enrollment.
- Server-controlled certificate identity.
- Signed local time for isolated Wi-Fi networks.
- MQTT/mTLS with certificate-derived identity and per-device ACLs.
- Individual certificate revocation.
- Physical integrations for CromaLED, AREA LZ7 and AS7341.
- Realistic software devices that perform the same identity lifecycle as physical hardware.
- Dashboard fleet management, manufacturing, control and simulation.
- Clean Windows/Linux install/start/stop workflow.
- Benchmark metric logging and CSV extraction.
- Apache-2.0 licensing, security policy, contribution templates, GitHub Actions, CodeQL and Dependabot.

## Validation performed for the release package

The public package is validated with the Python security/workflow test suite, Python source compilation, YAML parsing, Compose configuration validation when Docker is available, and a clean-tree scan for deployment-specific secret material.

Hardware-dependent behavior such as USB flashing, Wi-Fi association, CromaLED UART traffic, AREA DALI traffic, sensor acquisition, power-cycle persistence and final physical MQTT/mTLS connectivity must be validated on the target hardware setup.

## Security scope

The release addresses network-level bootstrap, enrollment, certificate identity, MQTT authorization and revocation. It does not claim resistance against invasive physical extraction from the ESP32 or compromise of the provisioning host / CA private key. See `docs/security_review.md`.
