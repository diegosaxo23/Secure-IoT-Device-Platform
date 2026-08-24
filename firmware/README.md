# ESP32 Firmware

The repository contains one application firmware project per supported product family:

```text
esp32/CromaLED_Gateway
esp32/AREA_LZ7_Gateway
esp32/AS7341_Gateway
```

The families do **not** share one binary. The reusable element is the identity/bootstrap/MQTT security pattern integrated into each product application.

## Firmware vs. device identity

Tracked source contains no per-device bootstrap secret and no installation-specific CA certificate. During manufacturing, `scripts/factory_program_esp32.py` creates installation-specific common build inputs inside a local `.factory-build-cache/` directory. These can include:

- product family and firmware version;
- host Wi-Fi IPv4 for API/MQTT/time endpoints;
- the common IoT Wi-Fi SSID/password;
- the public platform Root CA;
- the public signed-time verification key.

That cache is ignored by Git and excluded from release archives.

The per-device `device_id` and bootstrap secret are injected **after flashing** over the controlled factory serial protocol and stored in NVS. They are never compiled into the reusable product image.

The operational EC P-256 private key is generated locally during first enrollment and persisted with the device certificate in LittleFS.

See [`esp32/README.md`](esp32/README.md) for profile details and `../docs/firmware_integration.md` for the integration model.


## Measurement hooks

First enrollment emits `[METRIC]` records for P-256 key generation, P-256/CSR completion, challenge HTTP latency, enrollment HTTP latency, and total provisioning time. The metric lines are diagnostic only and do not change the authenticated protocol.

CromaLED emits them before UART0 is handed to the legacy lamp, so measurement output cannot corrupt normal 9200-baud application traffic.
