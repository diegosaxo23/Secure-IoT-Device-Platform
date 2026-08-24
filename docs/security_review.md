# Security Review and Thesis Scope

This note records the security invariants that should be made explicit in the thesis and distinguishes implemented controls from future work.

## Implemented controls

### Certificate identity is server-controlled

The CSR subject is untrusted input. The enrollment server authenticates a registry `device_id` through the challenge/HMAC exchange, validates the CSR signature and key, and then constructs the X.509 subject itself as:

```text
CN=<authenticated device_id>
```

The CSR public key is preserved, but a requested `CN` is not copied into the certificate.

### MQTT Client ID is certificate-bound

Mosquitto is configured with:

```text
require_certificate true
use_identity_as_username true
use_username_as_clientid true
```

The client-certificate CN becomes the MQTT username and Mosquitto replaces the supplied Client ID with that username. The effective broker Client ID is therefore the authenticated certificate CN. Device ACLs are still based on `%u` and the ESP32 also rejects provisioning data whose `client_id` differs from its authenticated `device_id`.

### Provisioning server authentication

Every product-family firmware contains the **public** platform Root CA generated for the installation. HTTPS bootstrap uses `WiFiClientSecure::setCACert()` and never uses `setInsecure()`. The CA is common to the family firmware/build and is not a per-device secret.

### Anti-replay challenge

The server generates a 256-bit random nonce and a unique session ID. The default TTL is 120 seconds. Challenges are single-use, expired challenges are rejected, and issuing a new challenge invalidates older unused sessions for that device.

The exact HMAC input is:

```text
IOT-BOOTSTRAP-V1\n
<device_id>\n
<session_id>\n
<nonce>\n
<sha256(DER_CSR)>\n
```

Binding the CSR hash to the HMAC prevents substitution of the requested operational public key.

### Revocation and active sessions

Revocation rebuilds the CRL and requests a broker security restart. All MQTT clients must reconnect and re-authenticate; valid clients reconnect, while a revoked certificate is rejected. This avoids the older administrative trick of impersonating a device Client ID, which is no longer compatible with certificate-bound Client IDs.

## Thesis future work / assumed limitations

These are deliberately not required for the current demonstrator but should be stated explicitly.

### Credential renewal

Operational certificates should be renewed before expiry through re-enrollment authenticated with the **currently valid operational certificate**, not by reusing the manufacturing bootstrap secret. Rotation policies, grace periods, and recovery from an expired certificate are future work.

### Clock dependency

Certificate-date validation requires a trustworthy clock. The demonstrator already supplies signed local time for isolated networks, but long-term clock resilience, RTC holdover, and recovery policies remain deployment concerns.

### Physical extraction resistance

The current threat model does not claim resistance against invasive physical access to flash. Production hardening could use ESP32 Secure Boot, Flash Encryption/NVS encryption, eFuse-backed keys, or a secure element such as an ATECC-class device.

## State-of-the-art positioning

The thesis can compare this architecture with EST, BRSKI (RFC 8995), AWS IoT provisioning mechanisms, and Azure Device Provisioning Service. The implemented design is intentionally lighter-weight for the target devices while retaining the core ideas of a bootstrap identity, proof of possession, locally generated operational keys, authenticated enrollment, and per-device operational credentials.

## Validation metrics worth reporting

The public release includes `[METRIC]` instrumentation and `scripts/extract_metrics.py` to support repeatable collection of:

- P-256 key and CSR generation time plus ESP32 heap/stack diagnostics;
- challenge/enrollment HTTP latency and total provisioning time;
- number of concurrent simulated identities successfully provisioned;
- MQTT reconnect/rejection timing after broker restart or revocation;
- ACL isolation tests between distinct device identities.

See [`benchmarking.md`](benchmarking.md) for the measurement procedure. No benchmark values are hard-coded into the repository; published numbers should come from the final controlled test setup.
