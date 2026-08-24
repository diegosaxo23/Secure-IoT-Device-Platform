#pragma once

#include <Arduino.h>

struct GeneratedIdentity {
  String privateKeyPem;
  String csrPem;
  String csrSha256Hex;
};

namespace CryptoHelpers {

bool generateP256Identity(const String &deviceId, GeneratedIdentity &identity, String &error);

bool calculateBootstrapProof(const String &bootstrapSecretBase64Url,
                             const String &deviceId,
                             const String &sessionId,
                             const String &nonceBase64Url,
                             const String &csrSha256Hex,
                             String &proofHex,
                             String &error);

bool validateCertificateAndPrivateKey(const String &certificatePem,
                                      const String &privateKeyPem,
                                      const String &expectedDeviceId,
                                      String &error);

bool verifySignedMessage(const String &publicKeyPem,
                         const String &message,
                         const String &signatureBase64Url,
                         String &error);

String mbedTlsError(int code);

}  // namespace CryptoHelpers
