# Technical Documentation

The root [`README.md`](../README.md) is the main entry point. This directory contains the deeper material required to review, reproduce, integrate, and validate the platform.

| Document | Purpose |
| --- | --- |
| [`installation.md`](installation.md) | Clean installation, startup, shutdown, ports and troubleshooting |
| [`architecture.md`](architecture.md) | Services, trust boundaries, Manufacturing Agent and runtime separation |
| [`protocol.md`](protocol.md) | Exact challenge/HMAC/CSR/X.509 enrollment sequence |
| [`security_review.md`](security_review.md) | Security invariants, threat-model boundaries and future work |
| [`security_validation_matrix.md`](security_validation_matrix.md) | Security claims mapped to automated and hardware evidence |
| [`local_time_bootstrap.md`](local_time_bootstrap.md) | Signed local time for TLS validation without Internet NTP |
| [`firmware_integration.md`](firmware_integration.md) | Reusing the identity agent in ESP32 product firmware |
| [`application_contracts.md`](application_contracts.md) | Family-specific telemetry and command contracts |
| [`topics.md`](topics.md) | MQTT topic hierarchy and ACL expectations |
| [`simulators.md`](simulators.md) | Simulated-device lifecycle and mixed-fleet testing |
| [`benchmarking.md`](benchmarking.md) | P-256/enrollment timing and fleet-scale measurement workflow |
| [`cromaled_uart.md`](cromaled_uart.md) | CromaLED UART0 hand-off and lamp protocol |
| [`validation.md`](validation.md) | Functional, security and scale-validation checklist |

## Recommended reading order

For a security review:

```text
architecture -> protocol -> security_review -> security_validation_matrix
```

For firmware integration:

```text
firmware_integration -> protocol -> relevant firmware product project
```

For simulation / scale work:

```text
simulators -> application_contracts -> topics -> benchmarking
```

