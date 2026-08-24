#include <Arduino.h>
#include <ArduinoJson.h>

#include "AgentConfig.h"
#include "BootstrapAgent.h"
#include "CromaLED_Device.h"

// Explicit Arduino entry-point declarations keep the .ino preprocessor
// from generating declarations inside an anonymous namespace.
void setup();
void loop();

namespace {

constexpr const char *kChannelNames[CromaLEDDevice::kChannelCount] = {
    "ROYAL_BLUE", "BLUE", "CYAN", "GREEN", "LIME", "LIME2",
    "AMBER", "AMBER2", "RED_ORANGE", "RED", "DEEP_RED"};

// The original product uses UART0 (`Serial`) for the lamp.
CromaLEDDevice lamp(Serial);
bool lampUartActive = false;
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
    for (size_t index = 0; index < CromaLEDDevice::kChannelCount; ++index) {
      if (requested == kChannelNames[index]) return static_cast<int>(index);
    }
  }
  int oneBasedIndex = parameters["channel_index"] | 0;
  if (oneBasedIndex == 0 && parameters["channel"].is<int>()) {
    oneBasedIndex = parameters["channel"].as<int>();
  }
  return (oneBasedIndex >= 1 && oneBasedIndex <= static_cast<int>(CromaLEDDevice::kChannelCount))
             ? oneBasedIndex - 1
             : -1;
}

void appendChannelState(JsonArray target) {
  for (size_t index = 0; index < CromaLEDDevice::kChannelCount; ++index) {
    JsonObject channel = target.add<JsonObject>();
    channel["index"] = index + 1U;
    channel["name"] = kChannelNames[index];
    channel["level"] = lamp.level(index);
    channel["enabled"] = lamp.level(index) > 0;
  }
}

void buildApplicationTelemetry(JsonObject measurements) {
  JsonArray channels = measurements["channels"].to<JsonArray>();
  appendChannelState(channels);
  measurements["temperature_valid"] = lamp.temperatureValid();
  if (lamp.temperatureValid()) measurements["temperature_c"] = lamp.temperatureC();
  measurements["lamp_uart_baud"] = CromaLEDDevice::kLampBaud;
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
    return true;
  }

  if (command == "set_channels") {
    JsonVariantConst raw = parameters["channels"];
    uint8_t levels[CromaLEDDevice::kChannelCount] = {0};
    if (raw.is<JsonArrayConst>()) {
      JsonArrayConst array = raw.as<JsonArrayConst>();
      if (array.size() != CromaLEDDevice::kChannelCount) {
        result["ok"] = false;
        result["error"] = "channels must contain exactly 11 levels";
        return true;
      }
      size_t index = 0;
      for (JsonVariantConst value : array) levels[index++] = clampPercent(value);
    } else if (raw.is<JsonObjectConst>()) {
      for (size_t index = 0; index < CromaLEDDevice::kChannelCount; ++index) {
        levels[index] = lamp.level(index);
      }
      JsonObjectConst object = raw.as<JsonObjectConst>();
      for (JsonPairConst pair : object) {
        String name = pair.key().c_str();
        name.toUpperCase();
        name.replace(" ", "_");
        name.replace("-", "_");
        for (size_t index = 0; index < CromaLEDDevice::kChannelCount; ++index) {
          if (name == kChannelNames[index]) levels[index] = clampPercent(pair.value());
        }
      }
    } else {
      result["ok"] = false;
      result["error"] = "channels must be an 11-value array or a channel-keyed object";
      return true;
    }
    lamp.setChannels(levels, CromaLEDDevice::kChannelCount);
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

  if (command == "get_temperature") {
    result["temperature_valid"] = lamp.temperatureValid();
    if (lamp.temperatureValid()) result["temperature_c"] = lamp.temperatureC();
    return true;
  }

  return false;
}


void activateLegacyLampUart() {
  if (lampUartActive) return;

  // The factory station must see FACTORY_READY / FACTORY_OK and the first
  // bootstrap/MQTT result at 115200. Only after MQTT/mTLS is connected do we
  // stop diagnostics and return UART0 to the physical lamp at 9200 baud.
  Serial.println("[CROMALED] Secure startup complete; handing UART0 to legacy lamp at 9200 baud");
  Serial.flush();
  identityAgent.setSerialLoggingEnabled(false);
  delay(20);
  Serial.end();
  delay(20);
  lamp.begin();
  lampUartActive = true;
}

}  // namespace

void setup() {
  Serial.begin(115200);
  Serial.setTimeout(1000);
  delay(500);
  Serial.println("[APP] CromaLED secure gateway starting");

  if (!identityAgent.begin(IOT_PRODUCT_FAMILY, IOT_FIRMWARE_VERSION,
                           buildApplicationTelemetry, handleApplicationCommand)) {
    Serial.println("[FATAL] Identity/bootstrap agent initialization failed");
    delay(5000);
    ESP.restart();
  }

  if (identityAgent.isMqttConnected()) {
    activateLegacyLampUart();
  } else {
    Serial.println("[CROMALED] MQTT not connected yet; keeping UART0 at 115200 until secure connection succeeds");
  }
}

void loop() {
  // Keep the factory/debug UART available until the first successful MQTT/mTLS
  // connection. This lets the manufacturing station observe DEVICE READY.
  if (!lampUartActive) {
    identityAgent.loop();
    if (identityAgent.isMqttConnected()) {
      activateLegacyLampUart();
    }
    delay(5);
    return;
  }

  lamp.task();
  identityAgent.loop();
  delay(5);
}
