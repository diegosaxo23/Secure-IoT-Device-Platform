# Host Tools and Manufacturing Scripts

The host tools keep operations that require local Wi-Fi discovery, COM/serial access, or PlatformIO outside the Dockerized API.

## Lifecycle scripts

| Script | Role |
| --- | --- |
| `install_platform.py` | One-time prerequisite checks, local PKI/state initialization, IoT Wi-Fi setup and image build |
| `start_platform.py` | Network synchronization, Manufacturing Agent startup, Docker startup and health checks |
| `show_startup_summary.py` | Prints operator-facing URLs, credentials and service endpoints |
| `configure_device_wifi.py` | Optional standalone update of local IoT Wi-Fi configuration |

Root wrappers are provided as `install-platform.bat`, `start-platform.bat`, `stop-platform.bat` on Windows and `.sh` equivalents on Linux.

## Manufacturing

`factory_program_esp32.py` is the complete factory path:

```text
preflight -> smart build/cache -> erase -> flash -> FACTORY_READY
-> register physical Device ID -> inject bootstrap secret -> FACTORY_OK
-> signed time -> HMAC/CSR enrollment -> X.509 -> MQTT/mTLS -> DEVICE READY
```

Profiles are allowlisted and map directly to the three source projects. `factory_provision_esp32.py` is a maintenance path for boards that already contain the common firmware and therefore does not compile/flash.

`manufacturing_agent.py` is the host HTTP bridge used by the Dockerized dashboard. It accepts only authenticated, controlled operations and uses `shell=False` for subprocesses.

## Administration and demos

- `admin.py` provides command-line device administration.
- `demo_device.py` is a protocol-level software client useful for development and negative tests.
- `network_config.py` selects only a usable active physical Wi-Fi IPv4 and supports isolated Wi-Fi networks without a default gateway.
- `setup.py` creates the installation `.env`, PKI, CRL, service identities and signed-time key pair. `--sync-network` updates service certificates when the host Wi-Fi address changes while preserving the Root CA.

## Public-tree safety check

`check_public_tree.py` fails if a source checkout contains common generated deployment artifacts such as `.env`, private keys/certificates, databases, logs, simulator identities, PlatformIO caches, or release ZIPs. GitHub Actions runs it on every push/pull request.

```bash
python scripts/check_public_tree.py
```

## Benchmark extraction

`extract_metrics.py` scans physical Manufacturing logs and simulator logs for explicit `[METRIC]` records and writes both raw and aggregate CSV files. It uses only the Python standard library.

```bash
python scripts/extract_metrics.py logs simulated_state --output metrics.csv --summary-output metrics-summary.csv
```

See [`../docs/benchmarking.md`](../docs/benchmarking.md) for the recommended measurement campaign.

## Host dependencies

Physical manufacturing requires the packages in `requirements-factory.txt`:

```text
pyserial
platformio
```

The normal installer/startup path installs them automatically if missing.
