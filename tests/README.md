# Automated Tests

The pytest suite validates protocol, security, PKI, dashboard-control helpers, simulation profiles and manufacturing logic without requiring a physical board.

Coverage includes:

- device-profile mapping;
- bootstrap-secret encryption and HMAC helpers;
- challenge lifetime and replay protection;
- HMAC binding to the CSR digest;
- server-controlled certificate identity;
- PKI profile and service-certificate compatibility;
- MQTT/control and revocation behavior;
- signed local-time tokens;
- simulator application profiles;
- manufacturing profile/build-cache/serial helpers;
- active physical Wi-Fi address selection.

Run:

```bash
python -m pip install -r server/requirements.txt
PYTHONPATH=server pytest -q
```

GitHub Actions runs this suite on every push/pull request and also validates `docker compose config`.

Hardware-only validation still covers physical flashing, eFuse-derived identity, UART behavior, Wi-Fi association, first enrollment on ESP32, persistent reboot behavior and real MQTT/mTLS connection.
