#include "BootstrapAgent.h"

#include <time.h>
#include <sys/time.h>

#include <esp_system.h>

#include "AgentConfig.h"
#include "bootstrap_ca.h"

BootstrapAgent *BootstrapAgent::activeInstance_ = nullptr;

BootstrapAgent::BootstrapAgent() : mqttClient_(mqttTls_) {}

bool BootstrapAgent::begin(const char *family,
                           const char *firmwareVersion,
                           TelemetryBuilder telemetryBuilder,
                           CommandHandler commandHandler) {
  family_ = family != nullptr ? family : "generic";
  firmwareVersion_ = firmwareVersion != nullptr ? firmwareVersion : "unknown";
  telemetryBuilder_ = telemetryBuilder;
  commandHandler_ = commandHandler;
  activeInstance_ = this;

  String error;
  if (!storage_.begin(error)) {
    logError("storage.begin", error);
    return false;
  }

  if (handleFactoryResetPin()) {
    delay(500);
  }

  if (!storage_.hasBootstrapIdentity()) {
    if (!enterFactoryIdentityMode()) {
      return false;
    }
  }

  if (!storage_.loadBootstrapIdentity(bootstrapIdentity_, error)) {
    logError("loadBootstrapIdentity", error);
    return false;
  }

  Serial.printf("[IDENTITY] device_id=%s, family=%s\n",
                bootstrapIdentity_.deviceId.c_str(), family_.c_str());

  if (!connectWifi()) {
    return false;
  }
  if (!synchronizeClock()) {
    return false;
  }
  if (!ensureProvisioned()) {
    return false;
  }
  if (!configureMqtt()) {
    return false;
  }
  connectMqtt();
  return true;
}

void BootstrapAgent::loop() {
  if (WiFi.status() != WL_CONNECTED) {
    connectWifi();
  }

  maintainMqtt();
  mqttClient_.loop();

  const unsigned long now = millis();
  if (mqttClient_.connected() && now - lastTelemetryMs_ >= IOT_TELEMETRY_INTERVAL_MS) {
    publishTelemetryNow();
    lastTelemetryMs_ = now;
  }
  if (mqttClient_.connected() && now - lastStatusMs_ >= IOT_STATUS_INTERVAL_MS) {
    publishStatus("periodic");
    lastStatusMs_ = now;
  }
}

bool BootstrapAgent::isProvisioned() const { return provisioned_; }

bool BootstrapAgent::isMqttConnected() { return mqttClient_.connected(); }

const String &BootstrapAgent::deviceId() const { return bootstrapIdentity_.deviceId; }

bool BootstrapAgent::handleFactoryResetPin() {
#if IOT_FACTORY_RESET_PIN >= 0
  pinMode(IOT_FACTORY_RESET_PIN, INPUT_PULLUP);
  delay(30);
  if (digitalRead(IOT_FACTORY_RESET_PIN) == LOW) {
    Serial.println("[FACTORY] Reset pin active. Hold for 3 seconds...");
    const unsigned long start = millis();
    while (digitalRead(IOT_FACTORY_RESET_PIN) == LOW && millis() - start < 3000UL) {
      delay(20);
    }
    if (millis() - start >= 3000UL) {
      String error;
      if (!storage_.clearAll(error)) {
        logError("factory-reset", error);
      } else {
        Serial.println("[FACTORY] Identity and credentials cleared");
      }
      return true;
    }
  }
#endif
  return false;
}

bool BootstrapAgent::validateDeviceId(const String &deviceId) const {
  if (deviceId.length() < 3 || deviceId.length() > 64) {
    return false;
  }
  for (size_t index = 0; index < deviceId.length(); ++index) {
    const char value = deviceId[index];
    const bool allowed = isAlphaNumeric(value) || value == '.' || value == '_' || value == ':' ||
                         value == '-';
    if (!allowed || (index == 0 && !isAlphaNumeric(value))) {
      return false;
    }
  }
  return true;
}

bool BootstrapAgent::validateBootstrapSecret(const String &secret) const {
  if (secret.length() < 43 || secret.length() > 128) {
    return false;
  }
  for (size_t index = 0; index < secret.length(); ++index) {
    const char value = secret[index];
    if (!(isAlphaNumeric(value) || value == '-' || value == '_' || value == '=')) {
      return false;
    }
  }
  return true;
}

bool BootstrapAgent::enterFactoryIdentityMode() {
  const uint64_t chipId = ESP.getEfuseMac();
  char macText[13] = {0};
  snprintf(macText, sizeof(macText), "%04X%08X",
           static_cast<uint16_t>(chipId >> 32U), static_cast<uint32_t>(chipId));

  // The identifier is derived from a unique hardware property and the compiled
  // product family. The manufacturing station therefore cannot accidentally bind a
  // secret intended for another unit to this board.
  String prefix;
  prefix.reserve(family_.length());
  for (size_t index = 0; index < family_.length() && prefix.length() < 40; ++index) {
    const char value = family_[index];
    if (isAlphaNumeric(value)) {
      prefix += static_cast<char>(toupper(value));
    } else if (!prefix.isEmpty() && !prefix.endsWith("-")) {
      prefix += '-';
    }
  }
  while (prefix.endsWith("-")) {
    prefix.remove(prefix.length() - 1);
  }
  if (prefix.isEmpty()) {
    prefix = "ESP32";
  }
  const String expectedDeviceId = prefix + '-' + macText;

  JsonDocument readyDocument;
  readyDocument["protocol"] = "FACTORY-SERIAL-V1";
  readyDocument["device_id"] = expectedDeviceId;
  readyDocument["family"] = family_;
  String readyPayload;
  serializeJson(readyDocument, readyPayload);

  auto announceReady = [&readyPayload]() {
    Serial.print("FACTORY_READY ");
    Serial.println(readyPayload);
    Serial.println("[FACTORY] Waiting for individual identity over Serial at 115200 baud");
  };

  Serial.println();
  announceReady();
  unsigned long lastAnnouncementMs = millis();

  while (true) {
    // Repeat the announcement so the station can open the port after boot without
    // missing the synchronization message.
    if (millis() - lastAnnouncementMs >= 3000UL) {
      announceReady();
      lastAnnouncementMs = millis();
    }

    if (!Serial.available()) {
      delay(20);
      continue;
    }

    String line = Serial.readStringUntil('\n');
    line.trim();
    if (line.isEmpty()) {
      continue;
    }

    JsonDocument document;
    const DeserializationError jsonError = deserializeJson(document, line);
    if (jsonError) {
      Serial.printf("FACTORY_ERROR JSON:%s\n", jsonError.c_str());
      continue;
    }

    const String command = document["command"] | "";
    BootstrapIdentity identity;
    identity.deviceId = document["device_id"] | "";
    identity.bootstrapSecret = document["bootstrap_secret"] | "";
    if (command != "set_identity" || identity.deviceId != expectedDeviceId ||
        !validateDeviceId(identity.deviceId) ||
        !validateBootstrapSecret(identity.bootstrapSecret)) {
      Serial.println("FACTORY_ERROR INVALID_IDENTITY");
      continue;
    }

    String error;
    if (!storage_.storeBootstrapIdentity(identity, error)) {
      Serial.printf("FACTORY_ERROR STORE:%s\n", error.c_str());
      continue;
    }

    Serial.printf("FACTORY_OK %s\n", identity.deviceId.c_str());
    Serial.flush();
    delay(750);
    ESP.restart();
  }
}

bool BootstrapAgent::connectWifi() {
  if (WiFi.status() == WL_CONNECTED) {
    return true;
  }
  if (String(IOT_WIFI_SSID) == "CHANGE_SSID" || String(IOT_WIFI_SSID).isEmpty()) {
    Serial.println("[WIFI] Configure AgentConfig.h before building");
    return false;
  }

  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.persistent(false);
  WiFi.begin(IOT_WIFI_SSID, IOT_WIFI_PASSWORD);
  Serial.printf("[WIFI] Connecting to %s", IOT_WIFI_SSID);

  const unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < IOT_WIFI_CONNECT_TIMEOUT_MS) {
    Serial.print('.');
    delay(250);
  }
  Serial.println();

  if (WiFi.status() != WL_CONNECTED) {
    Serial.printf("[WIFI] Connection failed, state=%d\n", static_cast<int>(WiFi.status()));
    return false;
  }
  Serial.printf("[WIFI] Local IP: %s, RSSI: %d dBm\n",
                WiFi.localIP().toString().c_str(), WiFi.RSSI());
  return true;
}

bool BootstrapAgent::synchronizeClock() {
  const time_t current = time(nullptr);
  if (current > 1700000000) {
    return true;
  }

  if (String(IOT_TIME_SIGNING_PUBLIC_KEY).indexOf("BEGIN PUBLIC KEY") < 0) {
    Serial.println("[TIME] Signed local-time public key is not configured");
    return false;
  }

  char nonceBuffer[33] = {0};
  snprintf(nonceBuffer, sizeof(nonceBuffer), "%08lx%08lx%08lx%08lx",
           static_cast<unsigned long>(esp_random()),
           static_cast<unsigned long>(esp_random()),
           static_cast<unsigned long>(esp_random()),
           static_cast<unsigned long>(esp_random()));
  const String nonce(nonceBuffer);
  const String url = String("http://") + IOT_BOOTSTRAP_HOST + ':' +
                     String(IOT_TIME_SERVICE_PORT) + "/api/v1/time?nonce=" + nonce;

  Serial.printf("[TIME] Requesting signed local time from %s:%u\n",
                IOT_BOOTSTRAP_HOST, static_cast<unsigned int>(IOT_TIME_SERVICE_PORT));

  WiFiClient plainClient;
  HTTPClient http;
  http.setTimeout(IOT_TIME_SYNC_TIMEOUT_MS);
  if (!http.begin(plainClient, url)) {
    Serial.println("[TIME] Could not initialize local-time HTTP request");
    return false;
  }

  const int statusCode = http.GET();
  const String responseBody = statusCode > 0 ? http.getString() : String();
  http.end();
  if (statusCode != 200) {
    Serial.printf("[TIME] Local-time service failed, HTTP=%d\n", statusCode);
    return false;
  }

  JsonDocument document;
  const DeserializationError jsonError = deserializeJson(document, responseBody);
  if (jsonError) {
    Serial.printf("[TIME] Invalid local-time JSON: %s\n", jsonError.c_str());
    return false;
  }

  const String protocol = document["protocol"] | "";
  const String returnedNonce = document["nonce"] | "";
  const uint64_t unixTime64 = document["unix_time"] | 0ULL;
  const String signature = document["signature"] | "";
  if (protocol != "IOT-SIGNED-TIME-V1" || returnedNonce != nonce ||
      unixTime64 <= 1700000000ULL || unixTime64 >= 4102444800ULL || signature.isEmpty()) {
    Serial.println("[TIME] Signed local-time response failed structural validation");
    return false;
  }

  const uint32_t unixTime = static_cast<uint32_t>(unixTime64);
  String canonical = "IOT-SIGNED-TIME-V1\n";
  canonical += nonce;
  canonical += '\n';
  canonical += String(static_cast<unsigned long>(unixTime));
  canonical += '\n';

  String verifyError;
  if (!CryptoHelpers::verifySignedMessage(
          String(IOT_TIME_SIGNING_PUBLIC_KEY), canonical, signature, verifyError)) {
    logError("signed-local-time", verifyError);
    return false;
  }

  struct timeval tv = {};
  tv.tv_sec = static_cast<time_t>(unixTime);
  tv.tv_usec = 0;
  if (settimeofday(&tv, nullptr) != 0 || time(nullptr) <= 1700000000) {
    Serial.println("[TIME] Could not set ESP32 system clock");
    return false;
  }

  Serial.printf("[TIME] Signed local time accepted. UTC=%s\n", utcTimestamp().c_str());
  return true;
}

bool BootstrapAgent::ensureProvisioned() {
  String error;
  if (storage_.hasOperationalCredentials()) {
    if (!storage_.loadOperationalCredentials(operational_, error)) {
      logError("loadOperationalCredentials", error);
      return false;
    }
    if (!CryptoHelpers::validateCertificateAndPrivateKey(
            operational_.certificatePem, operational_.privateKeyPem,
            bootstrapIdentity_.deviceId, error)) {
      logError("validate-local-credentials", error);
      return false;
    }
    provisioned_ = true;
    Serial.printf("[BOOTSTRAP] Persistent credentials found. Serial=%s\n",
                  operational_.certificateSerial.c_str());
    return true;
  }
  return requestOperationalCredentials();
}

String BootstrapAgent::bootstrapBaseUrl() const {
  return String("https://") + IOT_BOOTSTRAP_HOST + ':' + String(IOT_BOOTSTRAP_PORT);
}

bool BootstrapAgent::httpsPostJson(const String &url,
                                   const String &requestBody,
                                   int &statusCode,
                                   String &responseBody,
                                   String &error) {
  if (String(IOT_BOOTSTRAP_ROOT_CA).indexOf("BEGIN CERTIFICATE") < 0) {
    error = "bootstrap_ca.h does not contain the server-generated CA";
    return false;
  }

  WiFiClientSecure tlsClient;
  tlsClient.setCACert(IOT_BOOTSTRAP_ROOT_CA);
  tlsClient.setHandshakeTimeout(20);

  HTTPClient http;
  http.setConnectTimeout(IOT_HTTP_TIMEOUT_MS);
  http.setTimeout(IOT_HTTP_TIMEOUT_MS);
  http.setReuse(false);
  if (!http.begin(tlsClient, url)) {
    error = "HTTPClient could not open the HTTPS URL";
    return false;
  }
  http.addHeader("Content-Type", "application/json");
  http.addHeader("Accept", "application/json");

  statusCode = http.POST(requestBody);
  if (statusCode > 0) {
    responseBody = http.getString();
  } else {
    error = String("HTTPS transport error ") + statusCode + ": " +
            HTTPClient::errorToString(statusCode);
  }
  http.end();
  return statusCode > 0;
}

bool BootstrapAgent::requestOperationalCredentials() {
  const unsigned long provisioningStartMs = millis();
  Serial.println("[BOOTSTRAP] No operational credentials: starting provisioning");

  GeneratedIdentity generated;
  String error;
  Serial.println("[BOOTSTRAP] Generating EC P-256 key and CSR locally");
  if (!CryptoHelpers::generateP256Identity(bootstrapIdentity_.deviceId, generated, error)) {
    logError("generateP256Identity", error);
    return false;
  }

  JsonDocument challengeRequest;
  challengeRequest["device_id"] = bootstrapIdentity_.deviceId;
  String challengeBody;
  serializeJson(challengeRequest, challengeBody);

  int statusCode = 0;
  String responseBody;
  const unsigned long challengeStartMs = millis();
  const String challengeUrl = bootstrapBaseUrl() + "/api/v1/bootstrap/challenge";
  if (!httpsPostJson(challengeUrl, challengeBody, statusCode, responseBody, error)) {
    logError("challenge-https", error);
    return false;
  }
  if (statusCode != HTTP_CODE_CREATED) {
    Serial.printf("[BOOTSTRAP] Challenge rejected HTTP %d: %s\n", statusCode,
                  responseBody.c_str());
    return false;
  }

  Serial.printf("[METRIC] challenge_http_ms=%lu\n", millis() - challengeStartMs);
  JsonDocument challengeResponse;
  DeserializationError jsonError = deserializeJson(challengeResponse, responseBody);
  if (jsonError) {
    logError("challenge-json", jsonError.c_str());
    return false;
  }
  const String protocol = challengeResponse["protocol"] | "";
  const String sessionId = challengeResponse["session_id"] | "";
  const String nonce = challengeResponse["nonce"] | "";
  if (protocol != "IOT-BOOTSTRAP-V1" || sessionId.isEmpty() || nonce.isEmpty()) {
    logError("challenge-fields", "Incomplete challenge response");
    return false;
  }

  String proof;
  if (!CryptoHelpers::calculateBootstrapProof(
          bootstrapIdentity_.bootstrapSecret, bootstrapIdentity_.deviceId, sessionId, nonce,
          generated.csrSha256Hex, proof, error)) {
    logError("calculateBootstrapProof", error);
    return false;
  }

  JsonDocument enrollmentRequest;
  enrollmentRequest["device_id"] = bootstrapIdentity_.deviceId;
  enrollmentRequest["session_id"] = sessionId;
  enrollmentRequest["csr_pem"] = generated.csrPem;
  enrollmentRequest["proof"] = proof;
  String enrollmentBody;
  serializeJson(enrollmentRequest, enrollmentBody);

  const unsigned long enrollmentHttpStartMs = millis();
  const String enrollmentUrl = bootstrapBaseUrl() + "/api/v1/bootstrap/enroll";
  responseBody = "";
  error = "";
  if (!httpsPostJson(enrollmentUrl, enrollmentBody, statusCode, responseBody, error)) {
    logError("enroll-https", error);
    return false;
  }
  if (statusCode != HTTP_CODE_OK) {
    Serial.printf("[BOOTSTRAP] Enrollment rejected HTTP %d: %s\n", statusCode,
                  responseBody.c_str());
    return false;
  }

  Serial.printf("[METRIC] enroll_http_ms=%lu\n", millis() - enrollmentHttpStartMs);
  JsonDocument enrollmentResponse;
  jsonError = deserializeJson(enrollmentResponse, responseBody);
  if (jsonError) {
    logError("enroll-json", jsonError.c_str());
    return false;
  }

  OperationalCredentials received;
  received.privateKeyPem = generated.privateKeyPem;
  received.certificatePem = enrollmentResponse["certificate_pem"] | "";
  received.caCertificatePem = enrollmentResponse["ca_certificate_pem"] | "";
  received.certificateSerial = enrollmentResponse["certificate_serial"] | "";
  received.certificateNotAfter = enrollmentResponse["certificate_not_after"] | "";
  JsonObjectConst mqtt = enrollmentResponse["mqtt"].as<JsonObjectConst>();
  received.mqtt.host = mqtt["host"] | "";
  received.mqtt.port = mqtt["port"] | 8883;
  received.mqtt.clientId = mqtt["client_id"] | "";
  received.mqtt.statusTopic = mqtt["status_topic"] | "";
  received.mqtt.telemetryTopic = mqtt["telemetry_topic"] | "";
  received.mqtt.commandTopic = mqtt["command_topic"] | "";
  received.mqtt.responseTopic = mqtt["response_topic"] | "";

  // The operational MQTT identity must be the same identity authenticated
  // during bootstrap. Mosquitto also binds its effective Client ID to the
  // certificate CN, so mismatched provisioning data is rejected locally too.
  if (received.mqtt.clientId != bootstrapIdentity_.deviceId) {
    logError("mqtt-client-id", "Provisioned MQTT client_id does not match device_id");
    return false;
  }
  const String expectedTopicPrefix = "devices/" + bootstrapIdentity_.deviceId + "/";
  if (!received.mqtt.statusTopic.startsWith(expectedTopicPrefix) ||
      !received.mqtt.telemetryTopic.startsWith(expectedTopicPrefix) ||
      !received.mqtt.commandTopic.startsWith(expectedTopicPrefix) ||
      !received.mqtt.responseTopic.startsWith(expectedTopicPrefix)) {
    logError("mqtt-topics", "Provisioned MQTT topics are outside the authenticated device branch");
    return false;
  }

  if (!CryptoHelpers::validateCertificateAndPrivateKey(
          received.certificatePem, received.privateKeyPem, bootstrapIdentity_.deviceId, error)) {
    logError("validate-issued-certificate", error);
    return false;
  }
  if (!storage_.storeOperationalCredentials(received, error)) {
    logError("storeOperationalCredentials", error);
    return false;
  }

  operational_ = received;
  provisioned_ = true;
  Serial.printf("[METRIC] provisioning_total_ms=%lu\n", millis() - provisioningStartMs);
  Serial.printf("[BOOTSTRAP] Provisioning completed. Serial=%s, MQTT=%s:%u\n",
                operational_.certificateSerial.c_str(), operational_.mqtt.host.c_str(),
                operational_.mqtt.port);
  return true;
}

bool BootstrapAgent::configureMqtt() {
  if (!provisioned_) {
    return false;
  }

  mqttTls_.setCACert(operational_.caCertificatePem.c_str());
  mqttTls_.setCertificate(operational_.certificatePem.c_str());
  mqttTls_.setPrivateKey(operational_.privateKeyPem.c_str());
  mqttTls_.setHandshakeTimeout(20);

  mqttClient_.setServer(operational_.mqtt.host.c_str(), operational_.mqtt.port);
  mqttClient_.setCallback(mqttCallback);
  mqttClient_.setBufferSize(4096);
  mqttClient_.setKeepAlive(45);
  mqttClient_.setSocketTimeout(20);
  return true;
}

bool BootstrapAgent::connectMqtt() {
  if (mqttClient_.connected() || WiFi.status() != WL_CONNECTED || !provisioned_) {
    return mqttClient_.connected();
  }

  JsonDocument willDocument;
  willDocument["online"] = false;
  willDocument["device_id"] = bootstrapIdentity_.deviceId;
  willDocument["family"] = family_;
  willDocument["reason"] = "unexpected-disconnect";
  String willPayload;
  serializeJson(willDocument, willPayload);

  Serial.printf("[MQTT] Connecting over mTLS to %s:%u\n", operational_.mqtt.host.c_str(),
                operational_.mqtt.port);
  const bool connected = mqttClient_.connect(
      operational_.mqtt.clientId.c_str(), operational_.mqtt.statusTopic.c_str(), 1, true,
      willPayload.c_str());
  if (!connected) {
    Serial.printf("[MQTT] Connection rejected, state=%d\n", mqttClient_.state());
    char tlsError[160] = {0};
    const int tlsCode = mqttTls_.lastError(tlsError, sizeof(tlsError));
    if (tlsCode != 0) {
      Serial.printf("[MQTT] TLS error %d: %s\n", tlsCode, tlsError);
    }
    return false;
  }

  mqttClient_.subscribe(operational_.mqtt.commandTopic.c_str(), 1);
  publishStatus("connected");
  lastStatusMs_ = millis();
  Serial.printf("[MQTT] Connected. Subscribed to %s\n",
                operational_.mqtt.commandTopic.c_str());
  return true;
}

void BootstrapAgent::maintainMqtt() {
  if (mqttClient_.connected()) {
    return;
  }
  const unsigned long now = millis();
  if (now - lastMqttAttemptMs_ < IOT_MQTT_RECONNECT_MS) {
    return;
  }
  lastMqttAttemptMs_ = now;
  connectMqtt();
}

void BootstrapAgent::mqttCallback(char *topic, byte *payload, unsigned int length) {
  if (activeInstance_ != nullptr) {
    activeInstance_->onMqttMessage(topic, payload, length);
  }
}

void BootstrapAgent::onMqttMessage(char *topic, byte *payload, unsigned int length) {
  if (String(topic) != operational_.mqtt.commandTopic) {
    return;
  }
  if (length == 0 || length > 4095) {
    Serial.println("[MQTT] Empty or oversized command");
    return;
  }

  String body;
  body.reserve(length + 1U);
  for (unsigned int index = 0; index < length; ++index) {
    body += static_cast<char>(payload[index]);
  }

  JsonDocument commandDocument;
  const DeserializationError jsonError = deserializeJson(commandDocument, body);
  if (jsonError) {
    Serial.printf("[MQTT] Invalid command JSON: %s\n", jsonError.c_str());
    return;
  }

  const String commandId = commandDocument["command_id"] | "unknown";
  const String command = commandDocument["command"] | "";
  JsonObjectConst parameters = commandDocument["parameters"].as<JsonObjectConst>();

  JsonDocument resultDocument;
  JsonObject result = resultDocument.to<JsonObject>();
  String status = "unsupported";

  if (command == "ping") {
    result["pong"] = true;
    status = "completed";
  } else if (command == "get_status") {
    result["online"] = true;
    result["firmware"] = firmwareVersion_;
    result["uptime_s"] = millis() / 1000UL;
    result["free_heap"] = ESP.getFreeHeap();
    status = "completed";
    publishStatus("command");
  } else if (command == "restart") {
    result["action"] = "restart";
    status = "accepted";
  } else if (commandHandler_ != nullptr && commandHandler_(command, parameters, result)) {
    status = "completed";
  } else {
    result["command"] = command;
  }

  publishResponse(commandId, status, result);
  if (command == "restart") {
    mqttClient_.loop();
    delay(750);
    ESP.restart();
  }
}

bool BootstrapAgent::publishStatus(const char *reason) {
  if (!mqttClient_.connected()) {
    return false;
  }

  JsonDocument document;
  document["online"] = true;
  document["device_id"] = bootstrapIdentity_.deviceId;
  document["family"] = family_;
  document["firmware"] = firmwareVersion_;
  document["uptime_s"] = millis() / 1000UL;
  document["free_heap"] = ESP.getFreeHeap();
  document["wifi_rssi"] = WiFi.RSSI();
  document["timestamp"] = utcTimestamp();
  document["reason"] = reason != nullptr ? reason : "unknown";

  String payload;
  serializeJson(document, payload);
  return mqttClient_.publish(operational_.mqtt.statusTopic.c_str(), payload.c_str(), true);
}

bool BootstrapAgent::publishTelemetryNow() {
  if (!mqttClient_.connected()) {
    return false;
  }

  JsonDocument document;
  document["device_id"] = bootstrapIdentity_.deviceId;
  document["family"] = family_;
  document["firmware"] = firmwareVersion_;
  document["timestamp"] = utcTimestamp();
  document["uptime_s"] = millis() / 1000UL;
  document["sequence"] = ++sequence_;
  JsonObject measurements = document["measurements"].to<JsonObject>();
  if (telemetryBuilder_ != nullptr) {
    telemetryBuilder_(measurements);
  }
  // Common runtime fields are appended after application telemetry so product
  // code cannot accidentally overwrite identity-agent health metrics.
  measurements["free_heap"] = ESP.getFreeHeap();
  measurements["wifi_rssi"] = WiFi.RSSI();

  String payload;
  serializeJson(document, payload);
  return mqttClient_.publish(operational_.mqtt.telemetryTopic.c_str(), payload.c_str(), false);
}

bool BootstrapAgent::publishResponse(const String &commandId,
                                     const String &status,
                                     JsonObjectConst result) {
  if (!mqttClient_.connected()) {
    return false;
  }
  JsonDocument document;
  document["command_id"] = commandId;
  document["status"] = status;
  document["device_id"] = bootstrapIdentity_.deviceId;
  document["timestamp"] = utcTimestamp();
  document["result"] = result;

  String payload;
  serializeJson(document, payload);
  return mqttClient_.publish(operational_.mqtt.responseTopic.c_str(), payload.c_str(), false);
}

String BootstrapAgent::utcTimestamp() const {
  const time_t now = time(nullptr);
  if (now <= 1700000000) {
    return "";
  }
  struct tm utcTime;
  gmtime_r(&now, &utcTime);
  char buffer[25] = {0};
  strftime(buffer, sizeof(buffer), "%Y-%m-%dT%H:%M:%SZ", &utcTime);
  return String(buffer);
}

void BootstrapAgent::logError(const char *stage, const String &error) const {
  Serial.printf("[ERROR] %s: %s\n", stage != nullptr ? stage : "unknown", error.c_str());
}
