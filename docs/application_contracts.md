# Application Contracts by Product Family

Identity and transport are shared across product families. Application adapters define only telemetry fields and product-specific commands.

## CromaLED

Channels, in index order:

```text
1 ROYAL_BLUE
2 BLUE
3 CYAN
4 GREEN
5 LIME
6 LIME2
7 AMBER
8 AMBER2
9 RED_ORANGE
10 RED
11 DEEP_RED
```

Supported control commands include `set_channel`, `set_channels`, `set_all_channels`, `off`, and `get_temperature`. A channel level is represented as 0-100 in the platform interface. Telemetry includes the 11 current channel levels and, when valid, `temperature_c` from the CromaLED lamp UART.

Example:

```json
{
  "command": "set_channel",
  "parameters": {"channel":"DEEP_RED","channel_index":11,"level":75}
}
```

## AREA LZ7

Channels:

```text
BLUE CYAN GREEN LIME AMBER RED
```

The simulator exposes a 0-100 application level and an informational DALI-scale value derived from 0-254. The real product adapter decides how platform levels map to hardware output.

## AS7341

Bands:

```text
F1 F2 F3 F4 F5 F6 F7 F8 NIR CLEAR
```

The physical gateway publishes the latest sensor sample under `measurements.spectrum` together with the configured 256x gain and sample age. The simulator reports synthetic values for UI and pipeline validation. They must not be interpreted as calibrated spectral measurements. The application command `read_spectrum` returns the latest sample.

## Common commands

The identity agent can handle transport-level/common commands such as `ping`, `get_status`, and `restart`. Application code should handle only product-specific commands and return whether the command was consumed.
