#include "IdentityStorage.h"

#include <ArduinoJson.h>

namespace {
constexpr const char *kLittleFsBasePath = "/littlefs";
constexpr const char *kLittleFsPartitionLabel = "littlefs";
constexpr uint8_t kLittleFsMaxOpenFiles = 10;
}  // namespace

bool IdentityStorage::begin(String &error) {
  // The custom partition table names the filesystem partition "littlefs".
  // Arduino-ESP32 2.x defaults LittleFS.begin() to the partition label
  // "spiffs", so relying on the default would fail with:
  //   partition "spiffs" could not be found
  // Mount the intended partition explicitly. formatOnFail=true formats the
  // empty manufacturing partition on the first boot after a full flash erase.
  if (!LittleFS.begin(true, kLittleFsBasePath, kLittleFsMaxOpenFiles,
                      kLittleFsPartitionLabel)) {
    error = "Could not mount LittleFS partition 'littlefs'";
    return false;
  }
  return ensureDirectory(error);
}

bool IdentityStorage::ensureDirectory(String &error) {
  if (LittleFS.exists(kDirectory)) {
    return true;
  }
  if (!LittleFS.mkdir(kDirectory)) {
    error = "Could not create /identity in LittleFS";
    return false;
  }
  return true;
}

bool IdentityStorage::hasBootstrapIdentity() {
  Preferences preferences;
  if (!preferences.begin(kIdentityNamespace, true)) {
    return false;
  }
  const bool available = preferences.isKey(kDeviceIdKey) && preferences.isKey(kSecretKey) &&
                         preferences.getString(kDeviceIdKey).length() >= 3 &&
                         preferences.getString(kSecretKey).length() >= 40;
  preferences.end();
  return available;
}

bool IdentityStorage::loadBootstrapIdentity(BootstrapIdentity &identity, String &error) {
  Preferences preferences;
  if (!preferences.begin(kIdentityNamespace, true)) {
    error = "Could not open the identity NVS namespace";
    return false;
  }
  identity.deviceId = preferences.getString(kDeviceIdKey, "");
  identity.bootstrapSecret = preferences.getString(kSecretKey, "");
  preferences.end();

  if (identity.deviceId.length() < 3 || identity.bootstrapSecret.length() < 40) {
    error = "Bootstrap identity is missing or incomplete";
    return false;
  }
  return true;
}

bool IdentityStorage::storeBootstrapIdentity(const BootstrapIdentity &identity, String &error) {
  if (identity.deviceId.length() < 3 || identity.deviceId.length() > 64 ||
      identity.bootstrapSecret.length() < 40) {
    error = "Invalid bootstrap identity";
    return false;
  }

  Preferences preferences;
  if (!preferences.begin(kIdentityNamespace, false)) {
    error = "Could not open NVS for writing";
    return false;
  }
  const size_t idWritten = preferences.putString(kDeviceIdKey, identity.deviceId);
  const size_t secretWritten = preferences.putString(kSecretKey, identity.bootstrapSecret);
  preferences.end();

  if (idWritten == 0 || secretWritten == 0) {
    error = "Could not store bootstrap identity in NVS";
    return false;
  }
  return true;
}

bool IdentityStorage::clearBootstrapIdentity(String &error) {
  Preferences preferences;
  if (!preferences.begin(kIdentityNamespace, false)) {
    error = "Could not open NVS to clear the identity";
    return false;
  }
  const bool ok = preferences.clear();
  preferences.end();
  if (!ok) {
    error = "Could not clear the bootstrap identity";
  }
  return ok;
}

bool IdentityStorage::hasOperationalCredentials() {
  return LittleFS.exists(kMarkerPath) && LittleFS.exists(kPrivateKeyPath) &&
         LittleFS.exists(kCertificatePath) && LittleFS.exists(kCaPath) &&
         LittleFS.exists(kConfigPath);
}

bool IdentityStorage::readFile(const char *path, String &data, String &error) {
  File file = LittleFS.open(path, FILE_READ);
  if (!file) {
    error = String("Could not open ") + path;
    return false;
  }
  data = file.readString();
  file.close();
  if (data.isEmpty()) {
    error = String("File is empty: ") + path;
    return false;
  }
  return true;
}

bool IdentityStorage::writeFileAtomic(const char *path, const String &data, String &error) {
  const String temporary = String(path) + ".tmp";
  LittleFS.remove(temporary);

  File file = LittleFS.open(temporary, FILE_WRITE);
  if (!file) {
    error = String("Could not create ") + temporary;
    return false;
  }
  const size_t written = file.print(data);
  file.flush();
  file.close();

  if (written != data.length()) {
    LittleFS.remove(temporary);
    error = String("Incomplete write to ") + temporary;
    return false;
  }

  LittleFS.remove(path);
  if (!LittleFS.rename(temporary, path)) {
    LittleFS.remove(temporary);
    error = String("Could not atomically install ") + path;
    return false;
  }
  return true;
}

bool IdentityStorage::storeOperationalCredentials(const OperationalCredentials &credentials,
                                                   String &error) {
  if (!ensureDirectory(error)) {
    return false;
  }
  if (credentials.privateKeyPem.indexOf("PRIVATE KEY") < 0 ||
      credentials.certificatePem.indexOf("BEGIN CERTIFICATE") < 0 ||
      credentials.caCertificatePem.indexOf("BEGIN CERTIFICATE") < 0 ||
      credentials.mqtt.host.isEmpty() || credentials.mqtt.clientId.isEmpty()) {
    error = "Incomplete operational credentials";
    return false;
  }

  // Remove the marker first. It is recreated only after every credential file has
  // been installed successfully.
  LittleFS.remove(kMarkerPath);

  if (!writeFileAtomic(kPrivateKeyPath, credentials.privateKeyPem, error) ||
      !writeFileAtomic(kCertificatePath, credentials.certificatePem, error) ||
      !writeFileAtomic(kCaPath, credentials.caCertificatePem, error)) {
    return false;
  }

  JsonDocument document;
  document["certificate_serial"] = credentials.certificateSerial;
  document["certificate_not_after"] = credentials.certificateNotAfter;
  JsonObject mqtt = document["mqtt"].to<JsonObject>();
  mqtt["host"] = credentials.mqtt.host;
  mqtt["port"] = credentials.mqtt.port;
  mqtt["client_id"] = credentials.mqtt.clientId;
  mqtt["status_topic"] = credentials.mqtt.statusTopic;
  mqtt["telemetry_topic"] = credentials.mqtt.telemetryTopic;
  mqtt["command_topic"] = credentials.mqtt.commandTopic;
  mqtt["response_topic"] = credentials.mqtt.responseTopic;

  String configuration;
  serializeJson(document, configuration);
  if (!writeFileAtomic(kConfigPath, configuration, error)) {
    return false;
  }
  if (!writeFileAtomic(kMarkerPath, "IOT-BOOTSTRAP-V1\n", error)) {
    return false;
  }
  return true;
}

bool IdentityStorage::loadOperationalCredentials(OperationalCredentials &credentials,
                                                  String &error) {
  if (!hasOperationalCredentials()) {
    error = "Complete operational credentials are not available";
    return false;
  }

  String configuration;
  if (!readFile(kPrivateKeyPath, credentials.privateKeyPem, error) ||
      !readFile(kCertificatePath, credentials.certificatePem, error) ||
      !readFile(kCaPath, credentials.caCertificatePem, error) ||
      !readFile(kConfigPath, configuration, error)) {
    return false;
  }

  JsonDocument document;
  const DeserializationError jsonError = deserializeJson(document, configuration);
  if (jsonError) {
    error = String("Invalid MQTT configuration: ") + jsonError.c_str();
    return false;
  }

  credentials.certificateSerial = document["certificate_serial"] | "";
  credentials.certificateNotAfter = document["certificate_not_after"] | "";
  JsonObjectConst mqtt = document["mqtt"].as<JsonObjectConst>();
  credentials.mqtt.host = mqtt["host"] | "";
  credentials.mqtt.port = mqtt["port"] | 8883;
  credentials.mqtt.clientId = mqtt["client_id"] | "";
  credentials.mqtt.statusTopic = mqtt["status_topic"] | "";
  credentials.mqtt.telemetryTopic = mqtt["telemetry_topic"] | "";
  credentials.mqtt.commandTopic = mqtt["command_topic"] | "";
  credentials.mqtt.responseTopic = mqtt["response_topic"] | "";

  if (credentials.privateKeyPem.indexOf("PRIVATE KEY") < 0 ||
      credentials.certificatePem.indexOf("BEGIN CERTIFICATE") < 0 ||
      credentials.caCertificatePem.indexOf("BEGIN CERTIFICATE") < 0 ||
      credentials.mqtt.host.isEmpty() || credentials.mqtt.clientId.isEmpty() ||
      credentials.mqtt.statusTopic.isEmpty() || credentials.mqtt.telemetryTopic.isEmpty() ||
      credentials.mqtt.commandTopic.isEmpty() || credentials.mqtt.responseTopic.isEmpty()) {
    error = "Stored operational credentials are incomplete";
    return false;
  }
  return true;
}

bool IdentityStorage::removeIfPresent(const char *path) {
  return !LittleFS.exists(path) || LittleFS.remove(path);
}

bool IdentityStorage::clearOperationalCredentials(String &error) {
  bool ok = true;
  ok = removeIfPresent(kMarkerPath) && ok;
  ok = removeIfPresent(kPrivateKeyPath) && ok;
  ok = removeIfPresent(kCertificatePath) && ok;
  ok = removeIfPresent(kCaPath) && ok;
  ok = removeIfPresent(kConfigPath) && ok;
  ok = removeIfPresent("/identity/device.key.tmp") && ok;
  ok = removeIfPresent("/identity/device.crt.tmp") && ok;
  ok = removeIfPresent("/identity/ca.crt.tmp") && ok;
  ok = removeIfPresent("/identity/mqtt.json.tmp") && ok;
  ok = removeIfPresent("/identity/complete.flag.tmp") && ok;
  if (!ok) {
    error = "Could not remove every operational credential file";
  }
  return ok;
}

bool IdentityStorage::clearAll(String &error) {
  String firstError;
  const bool operationalOk = clearOperationalCredentials(firstError);
  String secondError;
  const bool bootstrapOk = clearBootstrapIdentity(secondError);
  if (!operationalOk || !bootstrapOk) {
    error = !firstError.isEmpty() ? firstError : secondError;
    return false;
  }
  return true;
}
