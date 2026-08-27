# AREA LZ7 Secure Gateway Firmware

This PlatformIO project integrates the identity/bootstrap agent with the existing AREA LZ7 lighting application while preserving its deployed DALI interface.

## Hardware/application contract

- DALI TX: GPIO17.
- DALI RX: GPIO16.
- Existing active-low interface behavior is preserved.
- Six logical channels: `BLUE`, `CYAN`, `GREEN`, `LIME`, `AMBER`, `RED`.
- Dashboard/application levels are expressed as 0-100% and translated to the DALI direct-arc range used by the original application.

Application commands include `set_channel`, `set_channels`, `set_all_channels`, and `off`.

## Secure lifecycle

The `area_lz7` profile uses the same manufacturing identity, signed-time, HMAC-bound CSR enrollment, persistent X.509 identity, and MQTT/mTLS lifecycle as the other product families. Only the product application adapter differs.

## v1.1.1 build compatibility

The AREA LZ7 gateway no longer depends on `arduino-dali` / `TimerInterrupt_Generic`.
Its application only requires DALI direct-arc writes, so v1.1.1 uses a compact
ESP32 hardware-timer transmitter for the existing GPIO17 TX path while keeping
GPIO16 reserved as the deployed DALI RX input. This removes the deep legacy
DALI/timer dependency tree that produced excessively long PlatformIO paths on
Windows. The transmitter contains compatibility branches for Arduino-ESP32 2.x
and 3.x timer APIs.
