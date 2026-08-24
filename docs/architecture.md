# Service Architecture

## Trust boundaries

The platform separates manufacturing, bootstrap/enrollment, operational MQTT, application behavior, and simulation control.

```text
                              OPERATOR
                                 |
                         HTTPS + Basic Auth
                                 |
                                 v
                         +---------------+
                         | FastAPI / UI  |
                         | Registry + PKI|
                         +---+-------+---+
                             |       |
              internal HTTP |       | bearer-token HTTP
                             |       v
                  +----------+--+  Host Manufacturing Agent
                  | Simulation |        |
                  |  Manager   |        | USB / Serial / PlatformIO
                  +------+-----+        v
                         |            ESP32
                         |              |
                         +----HTTPS-----+
                              bootstrap
                                 |
                                 v
                          X.509 identity
                                 |
                                 v
                           MQTT + mTLS
                                 |
                                 v
                            Mosquitto
```

## API service

The FastAPI service provides:

- dashboard authentication;
- device registry administration;
- HMAC challenge and enrollment endpoints;
- CSR validation and X.509 issuance;
- certificate revocation and CRL generation;
- fleet/device pages;
- MQTT command publication;
- Simulation Manager proxy endpoints;
- Manufacturing Agent proxy endpoints;
- password-protected project-data reset.

## Manufacturing Agent

The Manufacturing Agent runs on the host so the Dockerized API does not receive broad USB/COM access. The API authenticates to it using a random bearer token stored in `.env`.

The host agent is launched by the platform start command and accepts only authenticated API requests. It never programs a board by itself; a programming job starts only after the operator submits **Program Device**.

Allowed profiles are exactly `cromaled`, `area_lz7`, and `as7341`. The agent never accepts an arbitrary binary path or arbitrary shell command.

## Simulation Manager

The Simulation Manager is an internal Docker service. Its process starts with Docker but simulation starts **disabled**. The authenticated dashboard explicitly enables or disables it. Disabling simulation terminates all managed simulator processes.

## Broker

Mosquitto requires client certificates and uses the certificate identity as the MQTT username. The ACL grants each device access only to its own topic branch. The broker entrypoint watches the CRL and reloads TLS configuration when the CRL changes.

## Identity stages

A device has two identity stages:

1. **Bootstrap identity**: `device_id` plus the per-device bootstrap secret stored in NVS.
2. **Operational identity**: locally generated P-256 private key plus the CA-issued X.509 client certificate.

The bootstrap identity authorizes certificate enrollment. The operational identity authorizes MQTT/mTLS.

## Project-data reset security boundary

Resetting the project does not regenerate `.env` or the infrastructure PKI. Before device rows are deleted, every currently issued device certificate is added to the revocation set. These certificate serial tombstones are intentionally retained so credentials issued before a reset cannot authenticate again.

Simulation is stopped and purged before the database transaction begins. Manufacturing remains enabled; if an active programming operation is detected, the reset is rejected so runtime data cannot be cleared while a board is being programmed.


## Isolated Wi-Fi / ESP32 access-point mode

The host Wi-Fi adapter may use a static private IPv4 address without a default gateway. API (`8443`), MQTT/mTLS (`8883`) and signed local time (`8091`) remain reachable directly on the local subnet. Internet access is not required for bootstrap.
