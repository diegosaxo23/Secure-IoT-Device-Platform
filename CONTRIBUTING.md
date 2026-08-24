# Contributing

Contributions that improve protocol correctness, ESP32 portability, security validation, reproducibility, simulation, documentation, or test coverage are welcome.

## Development setup

1. Fork or clone the repository.
2. Never commit `.env`, generated PKI material, databases, logs, simulator identities, `.pio`, or `.factory-build-cache` state.
3. Install server/test dependencies:

   ```bash
   python -m pip install -r server/requirements.txt
   ```

4. Run tests:

   ```bash
   PYTHONPATH=server pytest -q
   ```

   PowerShell:

   ```powershell
   $env:PYTHONPATH = "server"
   pytest -q
   ```

5. Validate Compose changes:

   ```bash
   docker compose config --quiet
   ```

## Pull requests

Keep security-sensitive changes small enough to review. Explain:

- the component and trust boundary affected;
- the security property that changes;
- backward-compatibility implications;
- tests or physical validation performed.

Changes to enrollment, PKI, MQTT authorization, revocation, manufacturing, persistent identity state, or signed time should include regression tests whenever practical.

For ESP32 changes, identify the affected product profile (`CromaLED`, `AREA LZ7`, or `AS7341`) and the PlatformIO / Arduino-ESP32 environment used for validation.

## Third-party code

Do not remove upstream copyright or license notices. New vendored dependencies must include their original license text and source information. See [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

## Security issues

Do not open a public issue for a vulnerability that could enable credential disclosure, unauthorized enrollment, identity spoofing, ACL bypass, or private-key compromise. Follow [`SECURITY.md`](SECURITY.md).
