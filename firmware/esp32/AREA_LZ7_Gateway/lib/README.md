# AREA LZ7 Local Libraries

No additional local library is required for the AREA LZ7 DALI path in v1.1.1.
Direct-arc transmission is implemented in `src/AREA_LZ7_Device.h` using the
ESP32 hardware timer directly. ArduinoJson and PubSubClient remain pinned as
PlatformIO dependencies in `platformio.ini`.
