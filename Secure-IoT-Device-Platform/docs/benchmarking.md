# Benchmarking and Measurement

The platform includes lightweight measurement hooks so performance data can be collected without changing the protocol under test.

## Emitted metrics

Physical ESP32 firmware emits records such as:

```text
[METRIC] p256_key_ms=87
[METRIC] p256_csr_total_ms=132 free_heap=213424 stack_watermark=17052
[METRIC] challenge_http_ms=41
[METRIC] enroll_http_ms=56
[METRIC] provisioning_total_ms=244
```

Simulated devices emit a single record containing equivalent host-side timings:

```text
[CLED-SIM-0001] [METRIC] p256_csr_total_ms=1.843 challenge_http_ms=8.302 enroll_http_ms=11.944 provisioning_total_ms=24.151
```

The physical and simulated timings should not be compared as equivalent processor benchmarks. Their value is to characterize each execution environment and to make fleet-scale effects observable.

## Extract metrics to CSV

The standard-library parser scans `.log`, `.txt`, and `.out` files recursively:

```bash
python scripts/extract_metrics.py logs simulated_state --output metrics.csv --summary-output metrics-summary.csv
```

Outputs:

- `metrics.csv`: one row per `[METRIC]` record;
- `metrics-summary.csv`: count, minimum, mean, median, p95, and maximum for each metric.

Generated `metrics*.csv` exports are ignored by Git and the Docker build context by default. If benchmark results are intentionally published, sanitize identifiers and add a deliberate report under `docs/` rather than committing raw deployment logs.

## Recommended physical-device campaign

For each ESP32 product profile:

1. erase/provision from factory state;
2. repeat at least 10 times on a controlled network;
3. record:
   - P-256 key generation time;
   - P-256 + CSR total time;
   - free heap after identity generation;
   - stack high-water mark;
   - challenge HTTP latency;
   - enrollment HTTP latency;
   - total provisioning time;
4. report board model, Arduino-ESP32/PlatformIO versions, Wi-Fi RSSI, host OS, and server hardware.

Do not average away failures. Report failed enrollments separately with their failure reason.

## Recommended simulated-fleet campaign

Use progressively larger fleets, for example:

```text
1 -> 10 -> 25 -> 50 -> 100 -> 200 simulated devices
```

For each scale point record:

- number successfully provisioned;
- time until all expected identities are online;
- enrollment latency distribution;
- broker reconnect behavior;
- host CPU and memory utilization;
- API error rate/timeouts;
- database size and operation latency if relevant.

The Simulation Manager caps each fleet-start request at 200 devices. Multiple fleets/families can coexist if host capacity permits.

## Security-related measurements

Useful additional measurements include:

- time from certificate revocation to MQTT disconnect/rejection;
- reconnect time for valid clients after broker security restart;
- rate of rejected unauthorized topic operations;
- recovery time after temporary provisioning-service outage.

## Reporting results

A good public report includes the environment and confidence limits rather than only a single best-case number. Example table structure:

| Metric | n | Mean | Median | p95 | Max | Environment |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| P-256 + CSR | - | - | - | - | - | ESP32 / firmware build |
| Enrollment total | - | - | - | - | - | ESP32 + local WLAN |
| Simulator enrollment | - | - | - | - | - | Host CPU / Docker |

The repository deliberately ships without invented benchmark values. Populate this table only with measurements collected from the final test setup.
