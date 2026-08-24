# Dashboard Templates

Jinja2 templates in this directory implement the operator UI.

- `base.html` defines the common shell and navigation. Manual **Register** is intentionally the final navigation item.
- `index.html` shows fleet inventory, live status refresh, and the protected **Project maintenance / Reset Project Data** control.
- `device.html` shows device identity, certificate state, live telemetry, Current/Setpoint lighting controls, CromaLED temperature, commands, MQTT events, and certificate revocation.
- `register.html` supports secondary/manual registration and displays the bootstrap secret exactly once.
- `manufacturing.html` shows device/profile selection plus the live ESP32 programming/provisioning console. A board is never flashed until **Program Device** is pressed.
- `simulation.html` exposes **Enable/Disable Simulation**, fleet creation, and stop controls. No simulated device starts automatically.

The dashboard is an administration interface protected by HTTP Basic authentication. Destructive forms also use CSRF protection; project reset requires the dashboard password again.

The device page also live-refreshes TX commands and RX MQTT responses in **Control → Recent commands**.
