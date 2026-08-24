#include <Arduino.h>
#include <ArduinoJson.h>

#include "AgentConfig.h"
#include "AS7341_Device.h"
#include "BootstrapAgent.h"

// Explicit Arduino entry-point declarations keep the .ino preprocessor
// from generating declarations inside an anonymous namespace.
void setup();
void loop();

BootstrapAgent identityAgent;
AS7341Device spectralSensor;

static void appendSpectrum(JsonObject target) {
  target["F1"] = spectralSensor.value(0);
  target["F2"] = spectralSensor.value(1);
  target["F3"] = spectralSensor.value(2);
  target["F4"] = spectralSensor.value(3);
  target["F5"] = spectralSensor.value(6);
  target["F6"] = spectralSensor.value(7);
  target["F7"] = spectralSensor.value(8);
  target["F8"] = spectralSensor.value(9);
  target["NIR"] = spectralSensor.value(10);
  target["CLEAR"] = spectralSensor.value(11);
}

void buildApplicationTelemetry(JsonObject measurements) {
  measurements["sensor_ready"] = spectralSensor.sampleValid();
  measurements["gain"] = spectralSensor.gainMultiplier();
  measurements["gain_code"] = spectralSensor.gainCode();
  measurements["sample_age_ms"] = spectralSensor.sampleAgeMs();
  JsonObject spectrum = measurements["spectrum"].to<JsonObject>();
  appendSpectrum(spectrum);
}

bool handleApplicationCommand(const String &command,
                              JsonObjectConst parameters,
                              JsonObject result) {
  (void)parameters;
  if (command != "read_spectrum") return false;
  result["sensor_ready"] = spectralSensor.sampleValid();
  result["gain"] = spectralSensor.gainMultiplier();
  JsonObject spectrum = result["spectrum"].to<JsonObject>();
  appendSpectrum(spectrum);
  return true;
}

void setup() {
  Serial.begin(115200);
  Serial.setTimeout(1000);
  delay(500);
  Serial.println("[APP] AS7341 secure gateway starting");

  spectralSensor.begin();
  if (!identityAgent.begin(IOT_PRODUCT_FAMILY, IOT_FIRMWARE_VERSION,
                           buildApplicationTelemetry, handleApplicationCommand)) {
    Serial.println("[FATAL] Identity/bootstrap agent initialization failed");
    delay(5000);
    ESP.restart();
  }
}

void loop() {
  spectralSensor.task();
  identityAgent.loop();
  delay(5);
}
