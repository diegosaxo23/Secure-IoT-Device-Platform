# Security Policy

Secure IoT Device Platform is a research and laboratory platform for device manufacturing, bootstrap trust, X.509 enrollment, MQTT/mTLS operation, revocation, and mixed physical/simulated fleet testing.

## Reporting a vulnerability

Please do **not** publish suspected vulnerabilities, credentials, private keys, or exploit details in a public issue.

For a public GitHub deployment, enable **Private Vulnerability Reporting** in the repository security settings and use that channel for security reports. If that feature is not enabled, contact the repository owner privately before publishing technical details.

A useful report should include the affected component/version, prerequisites, reproducible steps, expected vs. observed behavior, and the security impact. Do not include real deployment secrets in the report.

## Supported version

The supported public release line is:

| Version | Supported |
| --- | --- |
| 1.x | Yes |
| Pre-public development snapshots | No |

## Security scope

The platform explicitly covers network attackers able to observe, inject, modify, or replay traffic and clients attempting to cross device authorization boundaries.

The current research scope does not claim resistance against invasive physical extraction from the ESP32 or compromise of the provisioning host, backend database master key, or CA private key. See [`docs/security_review.md`](docs/security_review.md) for the full trust model and hardening roadmap.
