# Runtime Data Directory

This directory contains only placeholders in the public repository. Docker creates persistent runtime data here after local installation/startup.

Typical state includes:

- `iot_device_platform.db` - SQLite registry, enrollment and device state;
- SQLite WAL/SHM sidecars;
- `broker/` - Mosquitto persistence data.

Bootstrap secrets stored in SQLite are encrypted using the installation-specific master key from `.env`. Do not copy a database to another deployment without understanding the dependency on that key and on the deployment PKI.

Runtime data is ignored by Git and excluded from the Docker build context.
