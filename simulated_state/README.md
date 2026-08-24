# Simulated Device State

The public repository contains no simulated-device identity material.

When Simulation Lab creates devices, each simulator can persist its private key, certificate, CA and MQTT configuration below this directory. That persistence allows a simulator to restart and reconnect using the same operational identity, closely matching the lifecycle of a physical device.

Generated simulator state is ignored by Git and excluded from the Docker build context.
