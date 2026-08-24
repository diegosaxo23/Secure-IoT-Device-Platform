# AS7341 Secure Gateway Firmware

This PlatformIO project integrates the secure identity agent with an AS7341 multispectral-sensor application.

## Telemetry

The gateway publishes the latest sensor state including:

- F1-F8 spectral channels;
- NIR;
- Clear;
- configured gain;
- sample state and age.

The application command `read_spectrum` returns the latest sample. Common identity-agent commands such as `ping`, `get_status`, and `restart` are shared with the other profiles.

## Secure lifecycle

At first manufactured startup the board receives its individual bootstrap identity in NVS, generates its P-256 operational key locally, creates a CSR, obtains an X.509 certificate, persists it in LittleFS, and connects to MQTT using mTLS.

The vendored Adafruit AS7341 and BusIO sources retain their upstream copyright and license files under `lib/`.
