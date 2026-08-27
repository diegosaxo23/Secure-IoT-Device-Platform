#pragma once

#include <Arduino.h>
#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
#include <WiFi.h>

#include "CryptoHelpers.h"
#include "IdentityStorage.h"

using TelemetryBuilder = void (*)(JsonObject measurements);
using CommandHandler = bool (*)(const String &command,
                                JsonObjectConst parameters,
                                JsonObject result);

class BootstrapAgent {
 public:
  BootstrapAgent();

  bool begin(const char *family,
             const char *firmwareVersion,
             TelemetryBuilder telemetryBuilder,
             CommandHandler commandHandler);
  void loop();

  bool isProvisioned() const;
  bool isMqttConnected();
  const String &deviceId() const;

  bool publishTelemetryNow();
  bool publishStatus(const char *reason = "manual");

 private:
  static BootstrapAgent *activeInstance_;

  IdentityStorage storage_;
  BootstrapIdentity bootstrapIdentity_;
  OperationalCredentials operational_;

  WiFiClientSecure mqttTls_;
  PubSubClient mqttClient_;

  String family_;
  String firmwareVersion_;
  TelemetryBuilder telemetryBuilder_ = nullptr;
  CommandHandler commandHandler_ = nullptr;

  bool provisioned_ = false;
  unsigned long lastTelemetryMs_ = 0;
  unsigned long lastStatusMs_ = 0;
  unsigned long lastMqttAttemptMs_ = 0;
  uint32_t sequence_ = 0;

  bool handleFactoryResetPin();
  bool enterFactoryIdentityMode();
  bool validateDeviceId(const String &deviceId) const;
  bool validateBootstrapSecret(const String &secret) const;

  bool connectWifi();
  bool synchronizeClock();
  bool ensureProvisioned();
  bool requestOperationalCredentials();
  bool httpsPostJson(const String &url,
                     const String &requestBody,
                     int &statusCode,
                     String &responseBody,
                     String &error);

  bool configureMqtt();
  bool connectMqtt();
  void maintainMqtt();
  void onMqttMessage(char *topic, byte *payload, unsigned int length);
  static void mqttCallback(char *topic, byte *payload, unsigned int length);

  bool publishResponse(const String &commandId,
                       const String &status,
                       JsonObjectConst result);
  String utcTimestamp() const;
  String bootstrapBaseUrl() const;
  void logError(const char *stage, const String &error) const;
};
