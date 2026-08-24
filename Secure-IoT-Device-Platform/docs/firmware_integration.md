# Firmware Integration

The three supported product projects contain the application logic and the shared identity/transport layer together:

```text
product application
  channels / DALI / sensor logic
        |
        v
application telemetry + command adapter
        |
        v
factory identity / HMAC bootstrap / P-256 / CSR / X.509 / MQTT mTLS
```

The application layer never needs access to CA private material or the server-side encrypted bootstrap secret store.

## Product projects

```text
CromaLED  -> firmware/esp32/CromaLED_Gateway
AREA LZ7  -> firmware/esp32/AREA_LZ7_Gateway
AS7341    -> firmware/esp32/AS7341_Gateway
```

The Manufacturing profile determines the source directory. `factory_program_esp32.py` does not accept an arbitrary firmware-directory override.

## Manufacturing inputs

Common build inputs include product family, firmware version, bootstrap server host/port, public root CA, and optional common Wi-Fi configuration.

Per-device data includes the hardware-derived `device_id` and the random `bootstrap_secret` returned once by the server. The per-device secret is transferred after flashing and is never placed in a generated C/C++ header or firmware image.

## Runtime state machine

```text
BOOT
 -> load bootstrap identity
 -> load operational credentials if present
 -> connect Wi-Fi
 -> synchronize time
 -> if credentials missing: HMAC bootstrap + CSR enrollment
 -> connect MQTT/mTLS
 -> application loop
```

Failures return to controlled retry/error handling rather than bypassing certificate validation.

## Build dependencies

Manufacturing resolves PlatformIO dependencies before flash erase. ArduinoJson and PubSubClient use direct pinned GitHub archives to avoid dependence on a specific PlatformIO library mirror. AREA LZ7 also resolves its DALI stack from repository archives, while the AS7341 sensor driver and BusIO sources stay local to the AS7341 project. The dependency phase retries once after a transient failure, and the complete dependency/build/upload console is forwarded to the dashboard.
