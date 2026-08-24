# FastAPI Server

The server is the control plane for device identity and fleet operations. It is built from `server/Dockerfile` and served over HTTPS on port `8443` by default.

Main responsibilities:

- authenticated web dashboard;
- device registry and encrypted bootstrap-secret storage;
- challenge creation and HMAC verification;
- CSR validation and server-controlled X.509 issuance;
- enrollment persistence and status transitions;
- CRL generation and certificate revocation;
- MQTT control-service integration;
- Manufacturing Agent proxy operations;
- Simulation Manager control;
- device/fleet telemetry views and application commands.

`app/` is intentionally modular: routers expose the HTTP surface while PKI, registry, database, security and MQTT logic remain separate implementation components.

The deployment PKI is never embedded in the image. It is generated locally under `pki/` and mounted at runtime.
