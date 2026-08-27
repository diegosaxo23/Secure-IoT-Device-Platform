# CromaLED Secure Gateway Firmware

This PlatformIO project combines the secure identity agent with the CromaLED lamp application.

## Lamp UART interface

The physical CromaLED application uses **UART0 (`Serial`) at 9200 baud, 8N1**. The secure gateway preserves that product interface while also using UART0 temporarily during manufacturing.

UART0 runs at `115200` during flashing, factory identity injection and first secure-start diagnostics. After MQTT/mTLS is successfully connected, diagnostic serial output is disabled, UART0 is closed, and the same UART is reopened at `9200` for the lamp protocol.

The application does **not** remap the lamp to UART2/GPIO16/GPIO17.

See [`../../../docs/cromaled_uart.md`](../../../docs/cromaled_uart.md) for the hand-off sequence.

## Application contract

CromaLED controls 11 channels in this exact order:

```text
ROYAL_BLUE
BLUE
CYAN
GREEN
LIME
LIME2
AMBER
AMBER2
RED_ORANGE
RED
DEEP_RED
```

The output frame contains 11 fixed-width decimal values followed by a newline:

```text
DDD-DDD-DDD-DDD-DDD-DDD-DDD-DDD-DDD-DDD-DDD-\n
```

The device also parses lamp temperature from the same UART and publishes it when a valid reading is available.

MQTT application commands include `set_channel`, `set_channels`, `set_all_channels`, `off`, and `get_temperature`. Common identity-agent commands include `ping`, `get_status`, and `restart`.

## Manufacturing

Use the `cromaled` manufacturing profile. The common firmware is flashed first; the physical Device ID and unique bootstrap secret are injected later and remain separate from the reusable binary.
