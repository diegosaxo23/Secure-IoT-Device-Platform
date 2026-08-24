# Runtime PKI Directory

This directory is intentionally empty in the public repository except for this file. A deployment PKI is generated locally by `install-platform.*` / `scripts/setup.py`.

Typical generated material includes:

```text
ca/        Root CA certificate and private key
api/       HTTPS API certificate and private key
broker/    MQTT broker certificate and private key
control/   internal MQTT control-service identity
healthcheck/ broker healthcheck client identity
crl/       certificate revocation list
time/      ECDSA P-256 signed-time key pair
```

The public Root CA and public signed-time verification key are synchronized into local firmware build inputs. Private CA/service/time-signing keys remain server-side.

Everything generated below `pki/` is ignored by Git and excluded from the Docker build context.
