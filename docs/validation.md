# IoT Device Platform Validation

A complete validation should demonstrate all of the following:

- multiple ESP32 units flashed with the same family firmware obtain different `device_id` values and different certificates;
- an unregistered unit cannot complete enrollment;
- an incorrect bootstrap secret fails HMAC verification;
- a challenge cannot be replayed after successful enrollment;
- the ESP32 operational certificate and private key persist after reboot;
- CromaLED preserves its legacy UART0 (`Serial`) lamp interface at 9200 baud after secure startup, and AREA LZ7 preserves its deployed GPIO17 TX / GPIO16 RX DALI mapping;
- device MQTT permissions are limited to the device-specific topic branch;
- revocation updates the CRL and prevents a revoked certificate from establishing a new MQTT connection;
- an already-open MQTT session is actively evicted after revocation;
- `--reset-existing` rotates the bootstrap secret and revokes the old operational certificate;
- the start launcher brings up the host Manufacturing Agent and Docker stack together, while programming still requires an explicit Program Device request;
- the Windows launcher starts the complete default Docker stack and reports container status;
- the launcher validates a live host process and port 8765, and the API uses bearer-token authentication for Manufacturing health, port discovery, job status, and programming;
- starting the platform never programs a device; programming begins only after an explicit Program Device request;
- the Manufacturing dashboard never renders the bootstrap secret;
- the Manufacturing Agent refuses profiles other than `cromaled`, `area_lz7`, and `as7341`;
- factory subprocesses execute PlatformIO with `shell=False`;
- the Simulation Manager process starts with Docker but creates no simulator until Simulation is explicitly enabled and a fleet is started;
- disabling Simulation stops all managed simulator processes;
- project reset requires the current dashboard password;
- project reset is rejected while a physical programming operation is in progress;
- project reset removes device/runtime records and simulator state while preserving `.env` and the dashboard password;
- project reset retains certificate revocation tombstones so credentials issued before the reset cannot reconnect;

Automated tests cover server logic and helper behavior. Physical USB flashing, Wi-Fi, DALI/UART application traffic, and sensor behavior require a real ESP32/hardware test fixture.
