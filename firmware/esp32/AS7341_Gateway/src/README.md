# Firmware Source

This directory contains the compiled ESP32 application, secure bootstrap/MQTT agent, cryptographic helpers, identity storage, and generated-at-build configuration headers. `FactoryBuildConfig.h` is created only temporarily by the manufacturing station and is never packaged with an individual bootstrap secret.
