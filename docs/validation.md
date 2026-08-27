# IoT Device Platform Validation

Release **v1.1.1** separates deterministic regression tests from deployed-system and physical-hardware validation. The repository contains **72 hardware-independent pytest tests**, an eight-control concise security runner, live API/broker security checks, real firmware compilation checks, and automated simulated/physical benchmark campaigns.

## Validation goals

A complete validation should demonstrate all of the following:

- multiple ESP32 units flashed with the same family firmware obtain different `device_id` values and different certificates;
- an unregistered unit cannot complete enrollment;
- an incorrect bootstrap secret fails HMAC verification;
- a challenge cannot be replayed after successful enrollment;
- substituting the CSR invalidates the HMAC-bound enrollment proof;
- a CSR cannot choose the final certified device identity;
- the ESP32 operational certificate and private key persist after reboot;
- CromaLED preserves its UART0 (`Serial`) lamp interface at 9200 baud after secure startup;
- AREA LZ7 preserves its GPIO17 TX / GPIO16 RX DALI mapping;
- AS7341 publishes the expected spectral-channel mapping;
- device MQTT permissions are limited to the authenticated device-specific topic branch;
- revocation updates the CRL, evicts the current session and prevents a revoked certificate from establishing a new MQTT connection;
- `--reset-existing` rotates the bootstrap secret and revokes the old operational certificate;
- the Manufacturing Agent only accepts allowlisted profiles and executes PlatformIO without arbitrary shell execution;
- starting the platform never programs a device; programming begins only after an explicit operator request;
- the Manufacturing dashboard never renders the bootstrap secret;
- Simulation starts no clients until explicitly requested and can stop managed simulator processes cleanly;
- benchmark cleanup removes only simulated devices between 1/10/25/50 scale points, preserving physical registrations and revoked-certificate tombstones;
- project reset preserves revocation tombstones so old credentials cannot become valid again;
- Windows PowerShell UTF-16 metric logs remain parseable;
- redirected serial output containing unsupported cp1252 characters cannot abort the manufacturing process.

## Automated pytest regression suite

Run on Windows:

```text
tests\run-tests.bat
```

or portably:

```bash
PYTHONPATH=server python -m pytest -q
```

The v1.1.1 release suite contains **72 tests**. It is intentionally hardware-independent so it can run deterministically in CI.

For a compact screenshot/report of the principal security regressions:

```text
tests\run-security-tests.bat
```

This executes eight representative controls and exports `security-tests.csv` plus `security-summary.csv`.

## Firmware compilation validation

Python tests cannot prove an ESP32 project still compiles. The separate firmware stage performs real PlatformIO builds of all three gateways:

```text
tests\run-firmware-tests.bat
```

It covers:

```text
CromaLED_Gateway
AREA_LZ7_Gateway
AS7341_Gateway
```

`run-all-tests.bat` executes pytest followed by the three firmware builds.

## Live adversarial checks

With the platform deployed and running:

```text
tests\run-live-bootstrap-tests.bat
tests\run-live-mqtt-acl-test.bat
tests\run-live-revocation-test.bat
```

The bootstrap runner verifies wrong-secret rejection, CSR substitution rejection, server-controlled certificate identity and consumed-session replay rejection against the real HTTPS API.

The MQTT ACL runner authenticates with one provisioned certificate, verifies publication to the device's own telemetry branch, then attempts publication to another device's branch and expects Mosquitto to deny it.

The revocation runner verifies a valid certificate can connect, revokes it, then confirms the old certificate cannot establish a new MQTT/mTLS connection.

## Performance and scale validation

Two automated benchmark launchers are provided:

```text
tests\benchmark-simulated.bat
tests\benchmark-real.bat
```

The simulated campaign creates clean fleets of **1, 10, 25 and 50 devices**, waits for provisioning/MQTT progress, and writes per-fleet metric CSV files plus a global `fleet-summary.csv`. Before every scale point, existing simulated processes/state/registry rows are removed and their certificates are revoked; physical devices are explicitly preserved. Mosquitto is restarted before the next fleet so the updated CRL is active. The final 50-device fleet is left available for dashboard inspection.

The physical campaign repeatedly invokes the normal factory programmer, with **10 runs by default**, preserving complete logs and producing raw/summary metric CSVs for `p256_key_ms`, `p256_csr_total_ms`, `challenge_http_ms`, `enroll_http_ms`, `provisioning_total_ms`, `free_heap` and `stack_watermark`.

## CSV evidence

All Windows launchers generate timestamped output below `validation_results/`. The generated files include:

- pytest PASS/FAIL details and summary;
- concise security PASS/FAIL details and summary;
- live-validation PASS/FAIL evidence;
- simulated-fleet raw metrics, summaries and fleet summary;
- physical per-run results, raw metrics and statistical summary.

CSV files are generated automatically, including header-only metric reports when a campaign fails before producing valid samples. Every `.bat` launcher pauses at completion so the final result remains visible.

## Hardware evidence still required

Automated tests are not presented as a substitute for target-hardware validation. Physical USB flashing, Wi-Fi behavior, CromaLED UART traffic, AREA LZ7 DALI behavior, AS7341 sensor behavior, storage after a real power loss and broker reconnect behavior should still be demonstrated on the relevant hardware where those claims are part of the evaluation.
