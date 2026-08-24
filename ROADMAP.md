# Roadmap

The current release is intentionally complete enough to demonstrate the full identity lifecycle while leaving several production-hardening and research directions open.

## Production hardening

- Automated operational certificate renewal authenticated with the currently valid certificate.
- Secure Boot and Flash/NVS Encryption on supported ESP32 targets.
- Secure-element or eFuse-backed operational keys where hardware permits.
- HSM-backed CA private-key operations and stricter service separation.
- Formal certificate-policy profiles and automated expiry/rotation policies.
- Stronger operator identity/RBAC for multi-user deployments.
- Structured audit export and long-term log retention policies.

## Scale and resilience

- Repeatable fleet benchmarks with 10/25/50/100/200 simulated devices.
- Broker/API load characterization and latency percentiles.
- Controlled network-loss, reboot, and service-restart campaigns.
- Multi-broker / high-availability deployment experiments.

## Research directions

- Mixed hardware-in-the-loop environments combining physical luminaires/sensors with simulated fleets.
- Closed-loop lighting control using AS7341 measurements and CromaLED/AREA channel actuation.
- Device-behavior anomaly detection using identity-bound telemetry.
- Evaluation of EST/BRSKI-compatible enrollment paths against the lightweight protocol implemented here.

Roadmap items are not security claims of the current release. Implemented controls and current limitations are documented in `docs/security_review.md`.
