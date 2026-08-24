# Signed local clock bootstrap

ESP32 devices need a valid wall clock before normal X.509 validity checks can be trusted. This project does not require Internet NTP.

The platform generates a dedicated ECDSA P-256 key pair at installation time. The private key stays under `pki/time/` on the server and the public key is embedded in the common product build by the manufacturing station.

Before HTTPS or MQTT/mTLS, the device generates a fresh random nonce and requests `http://<active-PC-Wi-Fi-IP>:8091/api/v1/time`. The response contains the nonce, Unix time, and an ECDSA/SHA-256 signature over the canonical payload:

```text
IOT-SIGNED-TIME-V1\n
<nonce>\n
<unix_time>\n
```

The ESP32 verifies the signature locally and only then calls `settimeofday()`. TLS then runs normally with CA validation and certificate validity-period checks enabled. `setInsecure()` is not used.

This mechanism is intended for isolated IoT WLANs, including a WLAN provided by an ESP32 configured as an access point. The PC running the platform and the target devices must be on the same reachable WLAN. The startup launcher automatically selects the PC Wi-Fi IPv4 and the factory build embeds that address.


## Isolated Wi-Fi / ESP32 access-point mode

The host Wi-Fi adapter may use a static private IPv4 address without a default gateway. API (`8443`), MQTT/mTLS (`8883`) and signed local time (`8091`) remain reachable directly on the local subnet. Internet access is not required for bootstrap.
