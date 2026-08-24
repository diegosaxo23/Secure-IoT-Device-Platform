# Mosquitto Operational Broker

This directory defines the MQTT broker used after device provisioning.

## Files

- `Dockerfile` - broker image;
- `mosquitto.conf` - TLS/mTLS listener, persistence, logging and CRL configuration;
- `acl` - per-device and control-service authorization;
- `docker-entrypoint-platform.sh` - copies runtime credentials into the container, validates permissions, starts Mosquitto and reacts to CRL changes.

## Identity binding

Physical and simulated devices must present a valid client certificate. The broker uses:

```text
require_certificate true
use_identity_as_username true
use_username_as_clientid true
```

The X.509 Common Name therefore becomes both the authenticated MQTT username and the effective broker Client ID. ACL patterns use that authenticated identity, preventing a client from selecting another device branch simply by changing its MQTT Client ID or topic string.

Revocation updates the CRL and forces active clients to re-authenticate so an already connected revoked device does not retain an operational session indefinitely.
