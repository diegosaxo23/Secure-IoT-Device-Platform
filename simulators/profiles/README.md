# Simulator Application Profiles

- `base.py` defines shared profile behavior and level validation.
- `cromaled.py` models eleven lighting channels and synthetic runtime measurements.
- `area_lz7.py` models six lighting channels and application/DALI-style level data.
- `as7341.py` models deterministic synthetic spectral bands and gain.
- `__init__.py` maps profile names to implementations.

These modules do not implement bootstrap or TLS. They provide application behavior to `simulated_device.py`, which owns identity and transport.

