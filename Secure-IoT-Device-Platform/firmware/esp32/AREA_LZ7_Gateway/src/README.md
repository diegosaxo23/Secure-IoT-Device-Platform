# AREA LZ7 Source Files

- `AREA_LZ7_Gateway.ino`: application entry point and MQTT command/telemetry adapter.
- `AREA_LZ7_Device.h`: six-channel DALI control using the PCB-fixed GPIO17 TX and GPIO16 RX pins.
- `BootstrapAgent.*`: serial manufacturing, HMAC bootstrap, certificate enrollment, MQTT/mTLS, common status, and common commands.
- `IdentityStorage.*`: persistent bootstrap identity and operational credentials.
- `CryptoHelpers.*`: P-256 key/CSR generation, HMAC proof, and certificate/key validation.
- `AgentConfig.h`: common family/network settings only.
- `bootstrap_ca.h`: public platform CA synchronized by the manufacturing script.
