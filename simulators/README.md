# Simulation Subsystem

The simulator subsystem creates software devices that use the **real platform identity and MQTT security flow** rather than bypassing it.

Each simulated device receives its own bootstrap identity, generates a local P-256 key and CSR, obtains an X.509 certificate, persists its operational identity, and connects to the real Mosquitto broker using mTLS.

## Components

- `manager.py` - internal Simulation Manager API used by the dashboard;
- `simulated_device.py` - shared secure device lifecycle;
- `simulate_fleet.py` - CLI fleet launcher/reference tool;
- `profiles/` - CromaLED, AREA LZ7 and AS7341 application behavior;
- `Dockerfile` - simulator-manager service image.

Simulation is disabled by default. The authenticated dashboard explicitly enables it and controls the number/profile of instances. Runtime credentials are written only under `simulated_state/`, which is ignored by Git.

This design supports mixed fleets containing physical and simulated identities and is suitable for enrollment-latency measurements, ACL tests, command/control development and scale experiments.
