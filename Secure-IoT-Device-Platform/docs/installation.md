# Installation and Operations Guide

This guide describes the clean public-repository workflow. The repository does not contain deployment credentials, PKI private keys, Wi-Fi passwords, databases, simulator identities, or firmware build caches.

## Windows installation

Windows is the recommended host when physical ESP32 manufacturing is required.

### Prerequisites

- Python 3.10 or newer available in `PATH`.
- Docker Desktop running with Docker Compose support.
- A physical Wi-Fi adapter connected to the same network used by the ESP32 devices.
- USB/serial drivers for the target ESP32 boards.

A default gateway or Internet connection is not required. An isolated access point is supported as long as the host and devices share a reachable private IPv4 subnet.

### One-time setup

Run:

```text
install-platform.bat
```

The installer validates the host, creates local deployment state, asks for the IoT Wi-Fi credentials, and pre-builds the Docker images. It does not start the long-running services at the end.

Generated state includes:

```text
.env
pki/ca/...
pki/api/...
pki/broker/...
pki/control/...
pki/crl/...
pki/time/...
```

The generated files are intentionally ignored by Git.

### Start

```text
start-platform.bat
```

Startup performs network synchronization, starts the host Manufacturing Agent and Docker services, waits for health checks, and prints the dashboard credentials and LAN endpoints.

### Stop

```text
stop-platform.bat
```

Stopping preserves all local identities and runtime data.

## Linux installation

Install Python 3, Docker Engine with the Compose plugin, and ensure the current user can access Docker and the required serial devices.

Then run:

```bash
./install-platform.sh
./start-platform.sh
```

Stop with:

```bash
./stop-platform.sh
```

The automatic Wi-Fi detector expects a Linux Wi-Fi interface detectable through `iw` or `/sys/class/net` and the `ip` command.

## Network ports

Allow the following inbound ports on the trusted IoT/LAN interface when host firewall rules require it:

| Port | Protocol | Purpose |
| ---: | --- | --- |
| 8443 | TCP | HTTPS dashboard, bootstrap and enrollment API |
| 8883 | TCP | MQTT over TLS with client certificates |
| 8091 | TCP | signed local-time service |

Port `8765` is the host Manufacturing Agent and is intended for local/Docker-to-host use rather than general LAN exposure.

## Network changes

On every full start, the platform selects the active physical host Wi-Fi IPv4. If the address changed, service certificates are synchronized to the new address while preserving the installation Root CA, device certificates, database, bootstrap secrets, and dashboard credentials.

## Clean reinstall

A clean release archive contains no runtime state. For a genuinely new deployment, use a fresh clone/extraction and run the installer there.

Do not delete `.env` while keeping an existing PKI/database unless you deliberately intend to recover or reset the installation. `.env` contains keys required to interpret encrypted bootstrap-secret records.

## Troubleshooting

### No active Wi-Fi adapter detected

Confirm that the host is connected through a physical Wi-Fi interface and has a private IPv4 address. A gateway is not required.

### Docker daemon unavailable

Start Docker Desktop or the Docker service and verify:

```text
docker info
docker compose version
```

### Physical programming unavailable

The platform can still run without the Manufacturing Agent. For programming, confirm that PlatformIO and pyserial are installed and that the host can access the selected COM/serial device.

### Device can join Wi-Fi but cannot reach provisioning

Verify host firewall rules and test the signed-time endpoint and dashboard from another machine on the same subnet. The physical device needs to reach the host IP on ports 8091, 8443, and 8883.
