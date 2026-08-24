# ESP32 Product Projects

Manufacturing supports exactly three allowlisted projects:

| Profile | Project | Role |
| --- | --- | --- |
| `cromaled` | `CromaLED_Gateway/` | 11-channel luminaire + lamp temperature |
| `area_lz7` | `AREA_LZ7_Gateway/` | 6-channel luminaire / DALI application |
| `as7341` | `AS7341_Gateway/` | multispectral sensor |

`scripts/factory_program_esp32.py` binds each profile directly to its project. There is no arbitrary `.bin`, source-directory, or shell-command selector in the dashboard manufacturing path.

All three projects use PlatformIO with the Arduino-ESP32 framework and reuse the same high-level lifecycle:

```text
factory identity in NVS -> signed time -> HTTPS bootstrap
-> local P-256 key/CSR -> X.509 -> LittleFS -> MQTT/mTLS
```

Hardware-specific application behavior remains local to each project. The deployed PCB/protocol is authoritative; security integration must not silently remap application pins or alter an existing device protocol.
