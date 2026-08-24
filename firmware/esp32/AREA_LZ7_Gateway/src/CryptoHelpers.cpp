#include "CryptoHelpers.h"

#include <vector>

#include <esp_system.h>

#include <mbedtls/base64.h>
#include <mbedtls/ecp.h>
#include <mbedtls/error.h>
#include <mbedtls/md.h>
#include <mbedtls/pem.h>
#include <mbedtls/pk.h>
#include <mbedtls/sha256.h>
#include <mbedtls/version.h>
#include <mbedtls/x509_crt.h>
#include <mbedtls/x509_csr.h>

namespace {

constexpr const char *kProtocolId = "IOT-BOOTSTRAP-V1";
constexpr const char *kCsrPemHeader = "-----BEGIN CERTIFICATE REQUEST-----\n";
constexpr const char *kCsrPemFooter = "-----END CERTIFICATE REQUEST-----\n";

String bytesToHex(const uint8_t *data, size_t length) {
  static constexpr char kHex[] = "0123456789abcdef";
  String output;
  output.reserve(length * 2);
  for (size_t index = 0; index < length; ++index) {
    output += kHex[(data[index] >> 4) & 0x0F];
    output += kHex[data[index] & 0x0F];
  }
  return output;
}

bool decodeBase64Url(const String &input,
                     std::vector<uint8_t> &decoded,
                     String &error) {
  String normalized = input;
  normalized.trim();
  normalized.replace('-', '+');
  normalized.replace('_', '/');
  while ((normalized.length() % 4U) != 0U) {
    normalized += '=';
  }

  // A 256-bit secret occupies 32 bytes. Keep headroom for future
  // extensions, but abnormally large inputs are rejected.
  if (normalized.isEmpty() || normalized.length() > 256) {
    error = "Invalid base64url bootstrap secret";
    return false;
  }

  decoded.assign((normalized.length() * 3U) / 4U + 4U, 0);
  size_t outputLength = 0;
  const int result = mbedtls_base64_decode(
      decoded.data(), decoded.size(), &outputLength,
      reinterpret_cast<const unsigned char *>(normalized.c_str()), normalized.length());
  if (result != 0) {
    error = String("Could not decode bootstrap secret: ") +
            CryptoHelpers::mbedTlsError(result);
    return false;
  }
  decoded.resize(outputLength);
  if (decoded.size() < 32) {
    error = "Bootstrap secret must contain at least 256 bits";
    return false;
  }
  return true;
}

bool decodeSignatureBase64Url(const String &input,
                              std::vector<uint8_t> &decoded,
                              String &error) {
  String normalized = input;
  normalized.trim();
  normalized.replace('-', '+');
  normalized.replace('_', '/');
  while ((normalized.length() % 4U) != 0U) {
    normalized += '=';
  }
  if (normalized.isEmpty() || normalized.length() > 256) {
    error = "Invalid base64url signature";
    return false;
  }

  decoded.assign((normalized.length() * 3U) / 4U + 4U, 0);
  size_t outputLength = 0;
  const int result = mbedtls_base64_decode(
      decoded.data(), decoded.size(), &outputLength,
      reinterpret_cast<const unsigned char *>(normalized.c_str()), normalized.length());
  if (result != 0) {
    error = String("Could not decode signed-time signature: ") +
            CryptoHelpers::mbedTlsError(result);
    return false;
  }
  decoded.resize(outputLength);
  if (decoded.empty()) {
    error = "Signed-time signature is empty";
    return false;
  }
  return true;
}

bool sha256(const unsigned char *data, size_t length, uint8_t digest[32], String &error) {
  // Arduino-ESP32 2.x ships mbedTLS 2.x, where mbedtls_sha256() is the
  // deprecated void wrapper and mbedtls_sha256_ret() is the error-returning API.
  // mbedTLS 3.x removed the _ret suffix. Keep both paths so this source also
  // remains usable with newer ESP32 cores.
#if MBEDTLS_VERSION_MAJOR >= 3
  const int result = mbedtls_sha256(data, length, digest, 0);
#else
  const int result = mbedtls_sha256_ret(data, length, digest, 0);
#endif
  if (result != 0) {
    error = String("SHA-256 failure: ") + CryptoHelpers::mbedTlsError(result);
    return false;
  }
  return true;
}

int esp32HardwareRandom(void *, unsigned char *output, size_t length) {
  if (output == nullptr && length != 0U) {
    return -1;
  }
  esp_fill_random(output, length);
  return 0;
}

}  // namespace

namespace CryptoHelpers {

String mbedTlsError(int code) {
  char buffer[160] = {0};
  mbedtls_strerror(code, buffer, sizeof(buffer));
  return String(buffer);
}

bool generateP256Identity(const String &deviceId,
                          GeneratedIdentity &identity,
                          String &error) {
#if !defined(MBEDTLS_X509_CSR_WRITE_C) || !defined(MBEDTLS_PEM_WRITE_C) || \
    !defined(MBEDTLS_PK_WRITE_C)
  error = "The ESP32 core does not enable CSR/PEM writing in mbedTLS";
  return false;
#else
  // P-256 generation and X.509 CSR writing are considerably more stack hungry
  // than the ordinary Arduino loop. The PlatformIO projects therefore reserve a
  // 24 KiB Arduino loop stack. Keep a diagnostic here so a future core/library
  // update can be diagnosed from the manufacturing serial log.
  const unsigned long identityStartMs = millis();
  Serial.printf("[CRYPTO] Before P-256 generation: free_heap=%u, stack_watermark=%u\n",
                static_cast<unsigned int>(ESP.getFreeHeap()),
                static_cast<unsigned int>(uxTaskGetStackHighWaterMark(nullptr)));

  // At this point BootstrapAgent has already connected Wi-Fi. ESP-IDF documents
  // esp_fill_random() as a true-random source while the RF subsystem is enabled,
  // so it can be used directly as the mbedTLS RNG callback without carrying an
  // additional entropy + CTR-DRBG context on the Arduino task stack.
  mbedtls_pk_context key;
  mbedtls_x509write_csr csr;
  mbedtls_pk_init(&key);
  mbedtls_x509write_csr_init(&csr);
  bool success = false;

  do {
    const mbedtls_pk_info_t *ecInfo = mbedtls_pk_info_from_type(MBEDTLS_PK_ECKEY);
    if (ecInfo == nullptr) {
      error = "mbedTLS EC key support is unavailable";
      break;
    }

    int result = mbedtls_pk_setup(&key, ecInfo);
    if (result != 0) {
      error = String("Could not prepare EC key: ") + mbedTlsError(result);
      break;
    }

    result = mbedtls_ecp_gen_key(MBEDTLS_ECP_DP_SECP256R1, mbedtls_pk_ec(key),
                                 esp32HardwareRandom, nullptr);
    if (result != 0) {
      error = String("Could not generate P-256 key: ") + mbedTlsError(result);
      break;
    }
    Serial.printf("[CRYPTO] P-256 private key generated. free_heap=%u, stack_watermark=%u\n",
                  static_cast<unsigned int>(ESP.getFreeHeap()),
                  static_cast<unsigned int>(uxTaskGetStackHighWaterMark(nullptr)));
    Serial.printf("[METRIC] p256_key_ms=%lu\n", millis() - identityStartMs);

    String subject = "CN=" + deviceId;
    result = mbedtls_x509write_csr_set_subject_name(&csr, subject.c_str());
    if (result != 0) {
      error = String("Could not set CSR common name: ") + mbedTlsError(result);
      break;
    }
    mbedtls_x509write_csr_set_key(&csr, &key);
    mbedtls_x509write_csr_set_md_alg(&csr, MBEDTLS_MD_SHA256);

    // One DER representation is generated and hashed. The PEM is then encoded
    // from those exact DER bytes, so the HMAC proof is bound to the exact CSR
    // eventually sent to the provisioning server.
    std::vector<unsigned char> derBuffer(2048, 0);
#if MBEDTLS_VERSION_MAJOR >= 4
    const int derLength =
        mbedtls_x509write_csr_der(&csr, derBuffer.data(), derBuffer.size());
#else
    const int derLength = mbedtls_x509write_csr_der(
        &csr, derBuffer.data(), derBuffer.size(), esp32HardwareRandom, nullptr);
#endif
    if (derLength < 0) {
      error = String("Could not encode CSR as DER: ") + mbedTlsError(derLength);
      break;
    }
    const unsigned char *der = derBuffer.data() + derBuffer.size() - derLength;

    uint8_t digest[32] = {0};
    if (!sha256(der, static_cast<size_t>(derLength), digest, error)) {
      break;
    }

    size_t pemRequired = 0;
    int resultPem = mbedtls_pem_write_buffer(
        kCsrPemHeader, kCsrPemFooter, der, static_cast<size_t>(derLength), nullptr, 0,
        &pemRequired);
    if (resultPem != MBEDTLS_ERR_BASE64_BUFFER_TOO_SMALL || pemRequired == 0) {
      error = String("Could not calculate CSR PEM size: ") + mbedTlsError(resultPem);
      break;
    }
    std::vector<unsigned char> csrPem(pemRequired, 0);
    resultPem = mbedtls_pem_write_buffer(
        kCsrPemHeader, kCsrPemFooter, der, static_cast<size_t>(derLength), csrPem.data(),
        csrPem.size(), &pemRequired);
    if (resultPem != 0) {
      error = String("Could not encode CSR as PEM: ") + mbedTlsError(resultPem);
      break;
    }

    std::vector<unsigned char> keyPem(2048, 0);
    result = mbedtls_pk_write_key_pem(&key, keyPem.data(), keyPem.size());
    if (result != 0) {
      error = String("Could not export private key: ") + mbedTlsError(result);
      break;
    }

    identity.privateKeyPem = String(reinterpret_cast<char *>(keyPem.data()));
    identity.csrPem = String(reinterpret_cast<char *>(csrPem.data()));
    identity.csrSha256Hex = bytesToHex(digest, sizeof(digest));

    if (identity.privateKeyPem.indexOf("PRIVATE KEY") < 0 ||
        identity.csrPem.indexOf("CERTIFICATE REQUEST") < 0 ||
        identity.csrSha256Hex.length() != 64) {
      error = "Generated operational identity is incomplete";
      break;
    }

    Serial.printf("[CRYPTO] CSR and private key ready. free_heap=%u, stack_watermark=%u\n",
                  static_cast<unsigned int>(ESP.getFreeHeap()),
                  static_cast<unsigned int>(uxTaskGetStackHighWaterMark(nullptr)));
    Serial.printf("[METRIC] p256_csr_total_ms=%lu free_heap=%u stack_watermark=%u\n",
                  millis() - identityStartMs,
                  static_cast<unsigned int>(ESP.getFreeHeap()),
                  static_cast<unsigned int>(uxTaskGetStackHighWaterMark(nullptr)));
    success = true;
  } while (false);

  mbedtls_x509write_csr_free(&csr);
  mbedtls_pk_free(&key);
  return success;
#endif
}

bool calculateBootstrapProof(const String &bootstrapSecretBase64Url,
                             const String &deviceId,
                             const String &sessionId,
                             const String &nonceBase64Url,
                             const String &csrSha256Hex,
                             String &proofHex,
                             String &error) {
  std::vector<uint8_t> secret;
  if (!decodeBase64Url(bootstrapSecretBase64Url, secret, error)) {
    return false;
  }

  String canonical;
  canonical.reserve(160 + deviceId.length() + sessionId.length() + nonceBase64Url.length());
  canonical += kProtocolId;
  canonical += '\n';
  canonical += deviceId;
  canonical += '\n';
  canonical += sessionId;
  canonical += '\n';
  canonical += nonceBase64Url;
  canonical += '\n';
  canonical += csrSha256Hex;
  canonical += '\n';

  const mbedtls_md_info_t *mdInfo = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
  if (mdInfo == nullptr) {
    error = "HMAC-SHA256 is not available in mbedTLS";
    return false;
  }

  uint8_t output[32] = {0};
  const int result = mbedtls_md_hmac(
      mdInfo, secret.data(), secret.size(),
      reinterpret_cast<const unsigned char *>(canonical.c_str()), canonical.length(), output);
  if (result != 0) {
    error = String("Could not calculate HMAC-SHA256: ") + mbedTlsError(result);
    return false;
  }
  proofHex = bytesToHex(output, sizeof(output));
  return true;
}

bool verifySignedMessage(const String &publicKeyPem,
                         const String &message,
                         const String &signatureBase64Url,
                         String &error) {
  if (publicKeyPem.indexOf("BEGIN PUBLIC KEY") < 0) {
    error = "Local-time verification public key is not configured";
    return false;
  }

  std::vector<uint8_t> signature;
  if (!decodeSignatureBase64Url(signatureBase64Url, signature, error)) {
    return false;
  }

  uint8_t digest[32] = {0};
  if (!sha256(reinterpret_cast<const unsigned char *>(message.c_str()),
              message.length(), digest, error)) {
    return false;
  }

  mbedtls_pk_context publicKey;
  mbedtls_pk_init(&publicKey);
  const int parseResult = mbedtls_pk_parse_public_key(
      &publicKey, reinterpret_cast<const unsigned char *>(publicKeyPem.c_str()),
      publicKeyPem.length() + 1U);
  if (parseResult != 0) {
    error = String("Could not parse local-time verification key: ") + mbedTlsError(parseResult);
    mbedtls_pk_free(&publicKey);
    return false;
  }

  const int verifyResult = mbedtls_pk_verify(
      &publicKey, MBEDTLS_MD_SHA256, digest, sizeof(digest),
      signature.data(), signature.size());
  mbedtls_pk_free(&publicKey);
  if (verifyResult != 0) {
    error = String("Invalid signed-time response: ") + mbedTlsError(verifyResult);
    return false;
  }
  return true;
}

bool validateCertificateAndPrivateKey(const String &certificatePem,
                                      const String &privateKeyPem,
                                      const String &expectedDeviceId,
                                      String &error) {
  mbedtls_x509_crt certificate;
  mbedtls_pk_context privateKey;
  mbedtls_x509_crt_init(&certificate);
  mbedtls_pk_init(&privateKey);
  bool success = false;

  do {
    int result = mbedtls_x509_crt_parse(
        &certificate, reinterpret_cast<const unsigned char *>(certificatePem.c_str()),
        certificatePem.length() + 1U);
    if (result != 0) {
      error = String("Invalid operational certificate: ") + mbedTlsError(result);
      break;
    }

#if MBEDTLS_VERSION_MAJOR >= 3
    result = mbedtls_pk_parse_key(
        &privateKey, reinterpret_cast<const unsigned char *>(privateKeyPem.c_str()),
        privateKeyPem.length() + 1U, nullptr, 0, esp32HardwareRandom, nullptr);
#else
    result = mbedtls_pk_parse_key(
        &privateKey, reinterpret_cast<const unsigned char *>(privateKeyPem.c_str()),
        privateKeyPem.length() + 1U, nullptr, 0);
#endif
    if (result != 0) {
      error = String("Invalid operational private key: ") + mbedTlsError(result);
      break;
    }

#if MBEDTLS_VERSION_MAJOR >= 3
    result = mbedtls_pk_check_pair(&certificate.pk, &privateKey,
                                   esp32HardwareRandom, nullptr);
#else
    result = mbedtls_pk_check_pair(&certificate.pk, &privateKey);
#endif
    if (result != 0) {
      error = String("Certificate does not match private key: ") + mbedTlsError(result);
      break;
    }

    char subject[256] = {0};
    const int subjectLength =
        mbedtls_x509_dn_gets(subject, sizeof(subject), &certificate.subject);
    if (subjectLength <= 0 || String(subject).indexOf("CN=" + expectedDeviceId) < 0) {
      error = "Issued certificate does not contain the expected device_id";
      break;
    }
    success = true;
  } while (false);

  mbedtls_pk_free(&privateKey);
  mbedtls_x509_crt_free(&certificate);
  return success;
}

}  // namespace CryptoHelpers
