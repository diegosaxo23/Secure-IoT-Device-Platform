# CromaLED Source Files

- `CromaLED_Gateway.ino`: application entry point and MQTT command/telemetry adapter.
- `CromaLED_Device.h`: 11-channel lamp protocol over UART0 (`Serial`) at 9200 baud, with controlled hand-off from manufacturing diagnostics.
- `BootstrapAgent.*`: serial manufacturing, HMAC bootstrap, certificate enrollment, MQTT/mTLS, common status, and common commands.
- `IdentityStorage.*`: NVS/LittleFS-backed bootstrap and operational credential storage.
- `CryptoHelpers.*`: P-256 key/CSR generation, HMAC proof, and credential validation.
- `AgentConfig.h`: common family/network settings; individual secrets are never stored here.
- `bootstrap_ca.h`: public platform CA synchronized by the manufacturing script.


## Arduino-ESP32 / mbedTLS compatibility

The PlatformIO project pins `espressif32 @ 7.0.1` and targets the Arduino-ESP32 2.x / mbedTLS 2.x API family. The compatibility guards also keep the crypto helpers portable toward newer mbedTLS APIs.
`CryptoHelpers.cpp` contains version guards for SHA-256, private-key parsing, and public/private-key pair validation APIs that differ across mbedTLS major versions.
`BootstrapAgent::isMqttConnected()` is intentionally non-const because the PubSubClient
2.x `connected()` method is not declared const.

The Heltec Boards Manager URL is not required for CromaLED when compiling with the
Espressif ESP32 core. If the Arduino IDE reports an error downloading
`https://resource.heltec.cn/download/package_heltec_esp32_index.json` and no Heltec
board is being used, remove that URL from **File > Preferences > Additional Boards
Manager URLs**.
