# FastAPI Application Package

Core modules:

- `main.py` creates the FastAPI application, security headers, HTTPS runtime lifecycle, static files, and routers.
- `config.py` maps `.env` and container environment variables into validated settings.
- `database.py` creates SQLAlchemy sessions and performs lightweight compatibility migrations.
- `models.py` defines devices, bootstrap sessions, MQTT events, commands, and certificate-revocation records.
- `registry.py` registers devices, rotates bootstrap secrets, revokes certificates, and rebuilds the CRL.
- `pki.py` validates CSRs, issues device certificates, and generates CRLs.
- `security.py` implements device ID validation, encrypted bootstrap-secret storage, and HMAC proof helpers.
- `mqtt_service.py` maintains the control-service MQTT connection, stores events, publishes commands, marks stale units offline, and requests broker-wide re-authentication after certificate revocation.
- `device_profiles.py` maps CromaLED, AREA LZ7, and AS7341 application behavior into dashboard controls.
- `project_reset.py` implements the password-protected operational reset. It stops/purges simulation, verifies that Manufacturing is idle without disabling it, revokes existing device certificates, clears runtime database records, and deliberately preserves `.env`, dashboard credentials, the root CA, internal service certificates, and revocation tombstones.
- `schemas.py` defines API request/response models.
- `auth.py` protects administrator endpoints with dashboard credentials.
- `time_utils.py` centralizes UTC handling.

`routers/`, `templates/`, and `static/` implement the HTTP API and operator interface.
