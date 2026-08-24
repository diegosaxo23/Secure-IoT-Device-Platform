# Implemented Bootstrap Protocol

## 1. Initial state

A manufactured unit has a stable `device_id`, a random per-device `bootstrap_secret`, and the public platform CA. It does not yet have an operational private key or device certificate.

## 2. Challenge request

Endpoint:

```text
POST /api/v1/bootstrap/challenge
```

Request:

```json
{"device_id":"CROMALED-AABBCCDDEEFF"}
```

The server verifies that the device exists, is enabled, is not revoked, and is allowed to enroll. It invalidates any older unused challenge for the same device, generates a random nonce, and returns a short-lived session ID.

## 3. Local operational key and CSR

The client generates an EC P-256 private key locally and signs a CSR with that key. The private key remains on the device. The CSR subject is **not trusted as identity**: the server validates the CSR signature and public-key parameters, but the subject proposed by the client is ignored when the operational certificate is built. The certificate CN is derived exclusively from the `device_id` that has already been authenticated by the bootstrap session/HMAC.

## 4. HMAC proof

The device computes SHA-256 over the DER CSR and builds this exact canonical message:

```text
IOT-BOOTSTRAP-V1\n
<device_id>\n
<session_id>\n
<nonce>\n
<csr_sha256>\n
```

HMAC-SHA256 uses the decoded bootstrap secret as the key. The proof is sent as lowercase hexadecimal.

Including the CSR digest binds possession of the bootstrap secret to the requested public key.

## 5. Enrollment

Endpoint:

```text
POST /api/v1/bootstrap/enroll
```

Request fields:

```json
{
  "device_id": "CROMALED-AABBCCDDEEFF",
  "session_id": "...",
  "csr_pem": "-----BEGIN CERTIFICATE REQUEST-----...",
  "proof": "64-hex-character-hmac"
}
```

The server verifies the registered device, session, expiration, retry count, CSR signature/key, and HMAC proof. A session is single-use. The default challenge lifetime is 120 seconds; a new challenge supersedes any older unused challenge for the same device.

## 6. X.509 response

The PKI does not copy the CSR subject. It constructs the certificate subject itself and sets `CN=<authenticated device_id>`. This prevents a device that knows only its own bootstrap secret from requesting another device identity in the CSR.

A successful response includes:

- the device certificate;
- the public CA certificate;
- certificate serial number;
- certificate expiration;
- MQTT host/port;
- client ID;
- status, telemetry, command, and response topics.

## 7. Persistence

The ESP32 stores the operational private key, certificate, CA, and MQTT configuration in LittleFS using atomic file replacement. A completion marker is written only after all required credential files exist.

## 8. Re-enrollment and revocation

Normal provisioned devices cannot request a second challenge unless reprovisioning is explicitly enabled or the bootstrap identity has been reset through administration. Resetting a device revokes the current certificate before returning a new one-time bootstrap secret.

## 9. MQTT operational identity

Mosquitto requires a valid client certificate. `use_identity_as_username true` maps the certificate CN to the MQTT username, and `use_username_as_clientid true` replaces the client-supplied MQTT Client ID with that authenticated username. The effective broker Client ID is therefore the certificate CN. ACL patterns use `%u`, so each device is restricted to `devices/<CN>/...`. The provisioning response and ESP32 firmware additionally require `mqtt.client_id == device_id` and device-specific topic prefixes.

## 10. Security properties

The protocol provides per-device bootstrap authentication, replay resistance through one-use short-lived challenges, CSR substitution resistance, server authentication through the embedded public Root CA, locally generated operational private keys, server-controlled certificate identity, certificate-bound MQTT identity, and a clean transition from bootstrap credentials to X.509/mTLS credentials.
