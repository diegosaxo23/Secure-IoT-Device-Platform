#pragma once

#include <Arduino.h>
#include <LittleFS.h>
#include <Preferences.h>

struct BootstrapIdentity {
  String deviceId;
  String bootstrapSecret;
};

struct MqttProvisioningConfig {
  String host;
  uint16_t port = 8883;
  String clientId;
  String statusTopic;
  String telemetryTopic;
  String commandTopic;
  String responseTopic;
};

struct OperationalCredentials {
  String privateKeyPem;
  String certificatePem;
  String caCertificatePem;
  String certificateSerial;
  String certificateNotAfter;
  MqttProvisioningConfig mqtt;
};

class IdentityStorage {
 public:
  bool begin(String &error);

  bool hasBootstrapIdentity();
  bool loadBootstrapIdentity(BootstrapIdentity &identity, String &error);
  bool storeBootstrapIdentity(const BootstrapIdentity &identity, String &error);
  bool clearBootstrapIdentity(String &error);

  bool hasOperationalCredentials();
  bool loadOperationalCredentials(OperationalCredentials &credentials, String &error);
  bool storeOperationalCredentials(const OperationalCredentials &credentials, String &error);
  bool clearOperationalCredentials(String &error);

  bool clearAll(String &error);

 private:
  static constexpr const char *kIdentityNamespace = "iot_identity";
  static constexpr const char *kDeviceIdKey = "device_id";
  static constexpr const char *kSecretKey = "secret";

  static constexpr const char *kDirectory = "/identity";
  static constexpr const char *kPrivateKeyPath = "/identity/device.key";
  static constexpr const char *kCertificatePath = "/identity/device.crt";
  static constexpr const char *kCaPath = "/identity/ca.crt";
  static constexpr const char *kConfigPath = "/identity/mqtt.json";
  static constexpr const char *kMarkerPath = "/identity/complete.flag";

  bool ensureDirectory(String &error);
  bool writeFileAtomic(const char *path, const String &data, String &error);
  bool readFile(const char *path, String &data, String &error);
  bool removeIfPresent(const char *path);
};
