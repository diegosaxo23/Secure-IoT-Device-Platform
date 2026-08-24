# Device Simulators

The software simulators are functional clients, not mocked dashboard rows. Each instance can register, perform the real HMAC bootstrap, generate its own P-256 key/CSR, receive a real X.509 certificate, and connect to Mosquitto with mTLS.

## Supported families

- **CromaLED** models the deployed eleven-channel application and synthetic temperature/activity telemetry.
- **AREA LZ7** models six application channels and derived DALI arc levels.
- **AS7341** generates deterministic synthetic F1-F8, NIR, and CLEAR data for UI and pipeline validation.


## Controlled startup

The `simulator-manager` service starts with normal Docker Compose startup so the operator never needs a separate command to make simulation available. The simulation capability itself starts **disabled**.

From the dashboard **Simulation** page:

1. press **Enable Simulation**;
2. create the desired fleets;
3. stop individual activity with **Stop simulators**;
4. press **Disable Simulation** to stop all instances and reject new fleet starts.

No simulated device starts automatically when Docker starts.

## Persistence

Each simulator stores private keys, certificates, CA data, and operational configuration under its own directory in `simulated_state/`. Restarting an individual simulator reuses the operational identity.

The password-protected **Reset Project Data** action stops all simulators and deletes simulator-generated state directories before clearing the server registry.
