# Benchmarking and Measurement

The platform includes lightweight measurement hooks so performance data can be collected without changing the protocol under test. Release v1.1.1 automates both the 1/10/25/50 simulated-fleet campaign and repeated physical-device provisioning, including raw and summary CSV generation.

## Emitted metrics

Physical ESP32 firmware emits records such as:

```text
[METRIC] p256_key_ms=87
[METRIC] p256_csr_total_ms=132 free_heap=213424 stack_watermark=17052
[METRIC] challenge_http_ms=41
[METRIC] enroll_http_ms=56
[METRIC] provisioning_total_ms=244
```

Simulated devices emit one combined host-side record:

```text
[BENCH-CLED-...] [METRIC] p256_csr_total_ms=1.843 challenge_http_ms=8.302 enroll_http_ms=11.944 provisioning_total_ms=24.151
```

The physical and simulated timings are not equivalent processor benchmarks. They characterize different execution environments and make network/concurrency effects observable.

## Automated simulated-fleet campaign

With the platform already running, Windows users can execute:

```text
tests\benchmark-simulated.bat
```

The default campaign launches fresh CromaLED benchmark identities at:

```text
1 -> 10 -> 25 -> 50 devices
```

For every scale point the script:

1. stops any managed simulated devices and purges their local simulator state;
2. removes **only simulated** devices from the registry while preserving physical units, retaining certificate revocation tombstones;
3. restarts Mosquitto so the refreshed CRL is active before measurement begins;
4. starts fresh simulated clients;
5. performs real registration, P-256/CSR, challenge/HMAC, X.509 enrollment and MQTT/mTLS;
6. staggers client launch slightly to avoid a 50-client TLS connection burst;
7. retries a simulator automatically if it exits before its first MQTT/mTLS connection;
8. reports a watchdog line periodically while waiting, so an unchanged `X/50` count is never silent;
9. stops early if every missing client has exhausted its retries, instead of waiting pointlessly for the global timeout;
10. stops the benchmark clients;
11. writes per-device logs and metric CSV files;
12. creates a global `fleet-summary.csv` with provisioning and time-to-all-connected results.

Cleanup occurs **before** each 1/10/25/50 scale point, so the previous scale does not remain in the dashboard or database and cannot interfere with the next measurement. The final scale is left registered after the campaign for inspection/screenshots. Use `--keep-existing` only when preserving pre-existing simulated identities is intentional.

The administrator credentials, API host and broker host are read from `.env`. Alternate values can be supplied directly, for example:

```powershell
python scripts\benchmark_simulated_fleet.py --sizes 1 10 25 50 --family cromaled --api-url https://192.168.50.10:8443 --mqtt-host 192.168.50.10
```

Benchmark Device IDs still use a dedicated timestamped `BENCH-*` prefix for traceability. By default, pre-existing simulated identities are purged before measurement; physical identities are never removed by this benchmark.

## Automated physical-device campaign

For a physical board, Windows users can run:

```text
tests\benchmark-real.bat
```

The wrapper asks for product profile, COM port and number of repetitions (10 by default). Each repetition invokes the normal `factory_program_esp32.py` path with `--reset-existing`, so the measurement covers a complete re-manufacture/reprovisioning sequence and produces a new operational certificate.

Direct example for CromaLED on COM2:

```powershell
python scripts\benchmark_real_device.py --profile cromaled --port COM2 --runs 10
```

The script streams the normal factory output to the console and simultaneously saves UTF-8 logs. A campaign stops on the first failed run by default; `--keep-going` can be used when failures must be retained as part of a robustness experiment.

The launcher keeps the Windows console open at completion. CSV files are generated automatically after each campaign. Outputs include:

- `logs/physical-XX.txt` — one complete log per run;
- `runs.csv` — PASS/FAIL and merged metric values per run;
- `physical-metrics.csv` — raw `[METRIC]` records;
- `physical-metrics-summary.csv` — count, minimum, mean, median, p95 and maximum.

## Manual metric extraction

Existing logs can still be processed directly:

```bash
python scripts/extract_metrics.py logs simulated_state --output metrics.csv --summary-output metrics-summary.csv
```

The extractor accepts UTF-8 and Windows PowerShell UTF-16/UTF-16LE logs. This specifically covers logs produced by Windows PowerShell 5.x `Tee-Object -FilePath`.

## Recommended reporting

For physical measurements report at least:

- board/product profile and serial port;
- PlatformIO/Arduino-ESP32 version;
- host OS and hardware;
- number of successful/failed repetitions;
- `p256_key_ms`;
- `p256_csr_total_ms`;
- `challenge_http_ms`;
- `enroll_http_ms`;
- `provisioning_total_ms`;
- minimum/observed `free_heap` and `stack_watermark`.

For simulated fleets report:

- requested devices;
- devices provisioned;
- devices connected with MQTT/mTLS;
- time until all expected clients are connected;
- mean/median/p95/max provisioning time;
- errors/timeouts.

Do not average away failed runs. A failed provisioning or missing MQTT connection is part of the result and must remain visible.

### Robustness at the 50-device scale

The simulated client uses Paho's asynchronous initial MQTT connection so the network loop can retry transient TCP/TLS/MQTT connection failures. The benchmark defaults to a 90 s per-client MQTT connection window, two automatic relaunches of a client that exits before its first successful MQTT connection, an 80 ms launch stagger and a 240 s overall scale timeout. While the connected count is unchanged, the watchdog prints the number of active/exhausted clients and the remaining timeout. These values can be tuned with `--mqtt-connect-timeout`, `--client-retries`, `--launch-delay`, `--progress-interval` and `--timeout`.
