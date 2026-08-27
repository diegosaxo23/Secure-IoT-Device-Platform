# Security Validation Matrix

This matrix maps the main security claims of the platform to concrete implementation controls and reproducible evidence. Release v1.1.1 contains 72 hardware-independent pytest tests; live API/broker checks are deliberately separate and produce CSV evidence under `validation_results/`.

| Security property | Threat / failure | Implemented control | Automated evidence | Physical / integration evidence |
| --- | --- | --- | --- | --- |
| Per-device bootstrap identity | One leaked credential compromises the fleet | Random bootstrap secret per registered device | `tests/test_security.py` | Program at least two units and compare registry identities |
| Challenge freshness | Captured authentication is replayed | Unique session ID, 256-bit nonce, TTL, single-use session | `tests/test_security.py`, provisioning tests | `tests/run-live-bootstrap-tests.bat` |
| CSR binding | Valid HMAC is reused with a different public key | HMAC input includes `SHA-256(DER_CSR)` | `test_hmac_is_bound_to_session_nonce_and_csr` | `tests/run-live-bootstrap-tests.bat` |
| Server-controlled certificate identity | Device requests another unit's CN | CSR subject untrusted; issuer builds CN from authenticated `device_id` | `test_csr_subject_is_untrusted_and_certificate_cn_comes_from_authenticated_device` | `tests/run-live-bootstrap-tests.bat` |
| Operational private key locality | Server obtains device private key | P-256 key generated on ESP32 / simulator; only CSR leaves client | PKI/provisioning tests | Packet/log inspection during enrollment |
| Provisioning server authentication | Rogue provisioning endpoint | Firmware validates installation Root CA; no insecure fallback | signed-time/service-certificate tests + source review | Present invalid server certificate and verify failure |
| Trusted time without Internet | TLS date validation bypassed or unavailable | Nonce-bound ECDSA-signed local time before TLS | `tests/test_signed_local_time.py` | Boot in isolated WLAN without public NTP |
| MQTT client authentication | Anonymous/unauthorized client connects | `require_certificate true` and CA validation | broker configuration regression tests | Connect without certificate and verify rejection |
| MQTT identity binding | Client authenticates with one cert but claims another Client ID | CN -> username and `use_username_as_clientid true`; firmware checks `client_id == device_id` | `test_mqtt_identity_is_bound_to_certificate_cn_and_firmware_device_id` | Attempt mismatched Client ID |
| Per-device authorization | One device accesses another device's topics | ACL patterns scoped by authenticated username | `test_mqtt_device_acl_is_scoped_to_authenticated_username` | `tests/run-live-mqtt-acl-test.bat` |
| Revocation | Compromised certificate continues reconnecting | CRL rebuild + broker security restart / re-authentication | `test_revocation_forces_broker_reauthentication_instead_of_spoofing_client_id`, PKI tests | `tests/run-live-revocation-test.bat` |
| Credential persistence | Device re-enrolls or loses identity after reboot | Persistent key/certificate/config storage with validation before use | provisioning/storage logic tests | Power cycle physical ESP32 and verify direct MQTT reconnect |
| Reset safety | Old credentials become valid again after project reset | Revocation tombstones retained | `test_project_reset_keeps_revocation_tombstone` | Reset project and attempt old certificate reconnect |
| Manufacturing command boundary | Dashboard becomes arbitrary command execution | Fixed allowlisted profiles, no arbitrary firmware path, `shell=False` subprocesses | controlled-operations tests | Attempt unsupported profile through agent API |
| Common firmware / unique identity | Per-device builds are required | Per-family firmware shared; identity injected after flash | factory build-cache/profile tests | Flash two units from same build and compare certificates |
| Simulated-device realism | Simulator bypasses security path | Simulator performs registration, P-256, CSR, HMAC, X.509, MQTT/mTLS | simulator profile and provisioning tests | Run mixed physical + simulated fleet |
| Benchmark isolation | Previous simulated fleet contaminates the next scale point | Stop/purge simulated state, revoke/delete simulated registry rows, preserve physical devices, restart broker before next scale | `test_simulated_device_cleanup_preserves_physical_fleet`, benchmark cleanup regression | `tests/benchmark-simulated.bat` 1/10/25/50 campaign |

## Recommended review evidence

For a concise technical review, capture the following artifacts:

1. factory log showing `FACTORY_READY`, `FACTORY_OK`, signed time, P-256/CSR metrics, certificate enrollment, and MQTT connection;
2. two device certificates with different serial numbers and CNs;
3. a rejected malicious-CSR identity test;
4. a rejected cross-device MQTT topic operation;
5. a successful revocation followed by a rejected reconnect;
6. simulator fleet view showing physical and simulated identities together.

The automated suite is intentionally not presented as a substitute for hardware validation. USB flashing, Wi-Fi behavior, UART/DALI application traffic, storage after power loss, and real broker reconnect behavior should be demonstrated on the target hardware.
