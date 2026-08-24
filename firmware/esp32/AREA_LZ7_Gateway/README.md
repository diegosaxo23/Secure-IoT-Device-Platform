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
