# Third-Party Notices

This repository includes or downloads third-party software. Those components retain their original copyright and license terms.

## Vendored Arduino libraries

### Adafruit AS7341 1.4.1

- Upstream: `https://github.com/adafruit/Adafruit_AS7341`
- Location: `firmware/esp32/AS7341_Gateway/lib/Adafruit_AS7341-master/`
- License: BSD 3-Clause style license; see the included `license.txt`.

### Adafruit BusIO 1.17.0

- Upstream: `https://github.com/adafruit/Adafruit_BusIO`
- Location: `firmware/esp32/AS7341_Gateway/lib/Adafruit_BusIO-master/`
- License: MIT; see the included `LICENSE`.

## PlatformIO-resolved firmware dependencies

The product `platformio.ini` files reference upstream archives for dependencies such as ArduinoJson and PubSubClient. Those packages are downloaded during local dependency resolution and are not redistributed as tracked source in this repository unless explicitly present under a `lib/` directory. AREA LZ7 v1.1.1 uses the ESP32 hardware timer directly for its required DALI direct-arc transmission and therefore no longer pulls the legacy `arduino-dali` / `TimerInterrupt_Generic` dependency chain.

## Container and Python dependencies

Python dependencies are listed in `server/requirements.txt` and `scripts/requirements-factory.txt`. Container base images and Mosquitto packages retain their own upstream licenses.

This notice is informational and does not replace the license texts of any third-party component.
