# Validation and Test Suite

Release **v1.1.0** contains **72 hardware-independent pytest tests** plus live integration checks and automated physical/simulated benchmark campaigns.

The default Python suite validates protocol, security, PKI, dashboard-control helpers, simulation profiles, manufacturing logic, Windows tooling and regressions found during physical validation without requiring an ESP32 board.

## Pytest coverage

Coverage includes:

- device-profile mapping for CromaLED, AREA LZ7 and AS7341;
- bootstrap-secret encryption and HMAC helpers;
- challenge lifetime, superseded challenges and replay protection;
- wrong bootstrap-secret rejection;
- HMAC binding to the CSR digest and nonce;
- server-controlled certificate identity when a CSR requests another CN;
- PKI profile, CRL behavior and service-certificate compatibility;
- MQTT certificate identity mapping and per-device ACL rules;
- signed local-time tokens;
- simulator application profiles and provisioning behavior;
- simulated benchmark cleanup that removes prior simulated identities while preserving physical devices and revocation tombstones;
- Windows PowerShell UTF-16/UTF-16LE metric-log extraction;
- Windows cp1252-safe ESP32 serial output during manufacturing;
- manufacturing profile, build-cache and serial helpers;
- active physical Wi-Fi address selection;
- firmware regressions shared by the three ESP32 gateways.

Portable command:

```bash
python -m pip install -r server/requirements.txt
PYTHONPATH=server python -m pytest -q
```

Expected release result:

```text
72 passed
```

## Windows entry point

Run:

```text
tests\run-validation-menu.bat
```

The menu provides every normal, live and benchmark action from one place. All `.bat` launchers remain in `tests/`, use `python -m pytest` / `python -m platformio` where appropriate, and pause before closing so the final result remains visible for inspection or screenshots.

| Launcher | Purpose |
| --- | --- |
| `run-tests.bat` | Complete 72-test pytest suite |
| `run-security-tests.bat` | Concise 8-control PASS/FAIL security report |
| `run-firmware-tests.bat` | Real PlatformIO build of CromaLED, AREA LZ7 and AS7341 |
| `run-all-tests.bat` | Pytest followed by all three firmware builds |
| `run-live-bootstrap-tests.bat` | Wrong secret, CSR substitution, malicious CN and replay against the deployed HTTPS API |
| `run-live-mqtt-acl-test.bat` | Own topic accepted and another device topic rejected by the running Mosquitto broker |
| `run-live-revocation-test.bat` | Valid certificate connects, is revoked, then cannot reconnect |
| `benchmark-simulated.bat` | Clean 1/10/25/50-device simulated campaign; previous simulated devices are purged, physical devices preserved |
| `benchmark-real.bat` | Repeated full physical manufacture/provisioning campaign |

## Concise security report

`run-security-tests.bat` runs eight representative checks:

1. HMAC bound to session, nonce and CSR;
2. wrong secret / CSR substitution / replay enrollment behavior;
3. server-controlled X.509 identity;
4. signed local-time verification;
5. MQTT ACL scoping by authenticated identity;
6. UTF-16 PowerShell metric-log compatibility;
7. cp1252-safe manufacturing console output;
8. shared firmware regression checks.

It writes `security-tests.csv` and `security-summary.csv` in a timestamped directory under `validation_results/security/`.

## Live deployed-system checks

The deterministic pytest suite does not pretend to validate a live broker or USB hardware. Those checks are deliberately separate:

- `scripts/validate_live_bootstrap_security.py` attacks the real HTTPS bootstrap API with a wrong secret, substituted CSR, malicious CSR CN and consumed-session replay;
- `scripts/validate_live_mqtt_acl.py` authenticates with one real certificate, verifies its own telemetry topic is accepted and another device's topic is denied by Mosquitto;
- `scripts/validate_live_revocation.py` verifies a valid certificate can connect, revokes it, then confirms the old certificate cannot reconnect.

This separation keeps CI deterministic while preserving reproducible thesis/demo evidence against the deployed platform.

## Automated benchmark campaigns

`benchmark-simulated.bat` launches clean simulated fleets of **1, 10, 25 and 50 devices**. Before each scale point it stops managed simulators, purges simulated state and simulated registry rows, preserves physical units, revokes any removed simulated certificates, reloads the CRL through a broker restart, and only then starts the next fleet. Each device follows the real registration -> P-256/CSR -> challenge/HMAC -> X.509 -> MQTT/mTLS lifecycle. Per-fleet raw metrics, summary metrics and a global `fleet-summary.csv` are created automatically. The final 50-device fleet remains registered for dashboard inspection; pass `--keep-existing` only when cleanup is intentionally undesired.

`benchmark-real.bat` repeatedly invokes the normal physical factory path (`factory_program_esp32.py`) and records one complete log per run. Ten repetitions are used by default. It creates `runs.csv`, `physical-metrics.csv` and `physical-metrics-summary.csv` automatically.

## CSV reports

Every automated runner writes timestamped machine-readable evidence under `validation_results/`:

- `run-tests.bat` -> `pytest-results.csv`, `validation-summary.csv`, `metadata.csv`;
- `run-security-tests.bat` -> `security-tests.csv`, `security-summary.csv`, `metadata.csv`;
- live validation launchers -> PASS/FAIL CSV plus metadata;
- `benchmark-simulated.bat` -> per-fleet `metrics.csv`, `metrics-summary.csv`, global `fleet-summary.csv`;
- `benchmark-real.bat` -> `runs.csv`, `physical-metrics.csv`, `physical-metrics-summary.csv`.

Metric CSV files are created even when a failed campaign contains no valid `[METRIC]` records; in that case the CSV is header-only and the process returns a failure code.

## CI

GitHub Actions runs the Python suite, validates `docker compose config`, and performs a real PlatformIO build of all three firmware projects. This prevents a green Python suite from hiding an ESP32 compile regression.

Hardware-only validation still covers physical flashing, eFuse-derived identity, CromaLED UART behavior, AREA LZ7 DALI behavior, AS7341 sensor behavior, Wi-Fi association, first enrollment on ESP32 and persistence after a real power cycle.
