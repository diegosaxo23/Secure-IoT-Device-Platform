# CromaLED UART0 integration

The CromaLED gateway keeps the product lamp interface on the ESP32 global Arduino `Serial` object.

## Physical interface

The CromaLED application uses:

```cpp
Serial.begin(9200, SERIAL_8N1);
```

No RX/TX remapping is supplied, so the physical lamp interface remains on UART0 and the ESP32 default UART0 pins. The secure-gateway integration therefore does not move the lamp protocol to UART2/GPIO16/GPIO17.

## Channel protocol

The firmware defines 11 channels:

1. ROYAL_BLUE
2. BLUE
3. CYAN
4. GREEN
5. LIME
6. LIME2
7. AMBER
8. AMBER2
9. RED_ORANGE
10. RED
11. DEEP_RED

Every 400 ms it sends 11 zero-padded percentage values at 9200 baud. Each value is followed by `-`, and the frame ends with a newline.

Example:

```text
100-000-050-000-000-000-000-000-000-000-025-\n
```

## UART ownership during secure manufacturing

UART0 is also the USB/factory serial interface. A controlled hand-over is therefore used:

1. UART0 @ 115200: flash/factory protocol (`FACTORY_READY`, identity injection, `FACTORY_OK`).
2. UART0 @ 115200: Wi-Fi, signed time, HTTPS bootstrap, CSR/X.509 and first MQTT/mTLS connection diagnostics.
3. After MQTT/mTLS is confirmed: serial diagnostics are disabled.
4. UART0 is restarted at 9200 baud and handed to the physical CromaLED lamp application.

This preserves both the manufacturing flow and the product wiring without mixing diagnostic text into the 9200-baud lamp protocol during normal operation.
