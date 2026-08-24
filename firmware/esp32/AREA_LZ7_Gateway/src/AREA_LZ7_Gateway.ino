#include <Arduino.h>
#include <ArduinoJson.h>

#include "AgentConfig.h"
#include "AREA_LZ7_Device.h"
#include "BootstrapAgent.h"

// Explicit Arduino entry-point declarations keep the .ino preprocessor
// from generating declarations inside an anonymous namespace.
void setup();
void loop();

namespace {

constexpr const char *kChannelNames[AreaLz7Device::kChannelCount] = {
    "BLUE", "CYAN", "GREEN", "LIME", "AMBER", "RED"};

AreaLz7Device lamp;
BootstrapAgent identityAgent;

uint8_t clampPercent(JsonVariantConst value) {
  int parsed = value.is<int>() ? value.as<int>() : 0;
  if (parsed < 0) parsed = 0;
  if (parsed > 100) parsed = 100;
  return static_cast<uint8_t>(parsed);
}

int findChannel(JsonObjectConst parameters) {
  if (parameters["channel"].is<const char *>()) {
    String requested = parameters["channel"].as<const char *>();
    requested.trim();
    requested.toUpperCase();
    requested.replace(" ", "_");
    requested.replace("-", "_");
    for (size_t index = 0; index < AreaLz7Device::kChannelCount; ++index) {
      if (requested == kChannelNames[index]) return static_cast<int>(index);
    }
  }
  int oneBasedIndex = parameters["channel_index"] | 0;
  if (oneBasedIndex == 0 && parameters["channel"].is<int>()) {
    oneBasedIndex = parameters["channel"].as<int>();
  }
  return (oneBasedIndex >= 1 && oneBasedIndex <= static_cast<int>(AreaLz7Device::kChannelCount))
             ? oneBasedIndex - 1
             : -1;
}

void appendChannelState(JsonArray target) {
  for (size_t index = 0; index < AreaLz7Device::kChannelCount; ++index) {
    JsonObject channel = target.add<JsonObject>();
    channel["index"] = index + 1U;
    channel["name"] = kChannelNames[index];
    channel["level"] = lamp.level(index);
    channel["dali_level"] = lamp.daliLevel(index);
    channel["enabled"] = lamp.level(index) > 0;
  }
}

void buildApplicationTelemetry(JsonObject measurements) {
  JsonArray channels = measurements["channels"].to<JsonArray>();
  appendChannelState(channels);
  measurements["dali_rx_pin"] = AreaLz7Device::kDaliRxPin;
  measurements["dali_tx_pin"] = AreaLz7Device::kDaliTxPin;
}

bool handleApplicationCommand(const String &command,
                              JsonObjectConst parameters,
                              JsonObject result) {
  if (command == "set_channel") {
    const int channel = findChannel(parameters);
    if (channel < 0 || !parameters["level"].is<int>()) {
      result["ok"] = false;
      result["error"] = "channel and integer level are required";
      return true;
    }
    const uint8_t level = clampPercent(parameters["level"]);
    lamp.setChannel(static_cast<size_t>(channel), level);
    result["ok"] = true;
    result["channel"] = kChannelNames[channel];
    result["level"] = level;
    result["dali_level"] = lamp.daliLevel(static_cast<size_t>(channel));
    return true;
  }

  if (command == "set_channels") {
    JsonArrayConst array = parameters["channels"].as<JsonArrayConst>();
    if (array.isNull() || array.size() != AreaLz7Device::kChannelCount) {
      result["ok"] = false;
      result["error"] = "channels must contain exactly 6 levels";
      return true;
    }
    uint8_t levels[AreaLz7Device::kChannelCount] = {0};
    size_t index = 0;
    for (JsonVariantConst value : array) levels[index++] = clampPercent(value);
    lamp.setChannels(levels, AreaLz7Device::kChannelCount);
    result["ok"] = true;
    JsonArray state = result["channels"].to<JsonArray>();
    appendChannelState(state);
    return true;
  }

  if (command == "set_all_channels") {
    if (!parameters["level"].is<int>()) {
      result["ok"] = false;
      result["error"] = "integer level is required";
      return true;
    }
    const uint8_t level = clampPercent(parameters["level"]);
    lamp.setAll(level);
    result["ok"] = true;
    result["level"] = level;
    return true;
  }

  if (command == "off") {
    lamp.setAll(0);
    result["ok"] = true;
    result["off"] = true;
    return true;
  }

  return false;
}

}  // namespace

void setup() {
  Serial.begin(115200);
  Serial.setTimeout(1000);
  delay(500);
  Serial.println("[APP] AREA LZ7 secure gateway starting");

  // GPIO16/17 are part of the deployed PCB wiring. Do not remap them in software.
  lamp.begin();

  if (!identityAgent.begin(IOT_PRODUCT_FAMILY, IOT_FIRMWARE_VERSION,
                           buildApplicationTelemetry, handleApplicationCommand)) {
    Serial.println("[FATAL] Identity/bootstrap agent initialization failed");
    delay(5000);
    ESP.restart();
  }
}

void loop() {
  lamp.task();
  identityAgent.loop();
  delay(5);
}
