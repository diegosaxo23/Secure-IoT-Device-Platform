#pragma once

#include <Adafruit_AS7341.h>
#include <Arduino.h>

class AS7341Device {
 public:
  bool begin() {
    if (!sensor_.begin()) {
      Serial.println("[AS7341] Sensor not detected");
      ready_ = false;
      return false;
    }
    sensor_.setATIME(35);
    sensor_.setASTEP(5000);
    sensor_.setGain(AS7341_GAIN_256X);
    lastReadMs_ = millis();
    sensor_.startReading();
    ready_ = true;
    Serial.println("[AS7341] Sensor initialized");
    return true;
  }

  void task() {
    if (!ready_) return;
    const unsigned long now = millis();
    const bool timedOut = now - lastReadMs_ >= kReadTimeoutMs;
    if (!sensor_.checkReadingProgress() && !timedOut) return;

    if (timedOut) {
      Serial.println("[AS7341] Read timeout; restarting acquisition");
      sensor_.startReading();
      lastReadMs_ = now;
      return;
    }

    sensor_.getAllChannels(readings_);
    sampleValid_ = true;
    lastSampleMs_ = now;
    lastReadMs_ = now;
    sensor_.startReading();
  }

  bool sampleValid() const { return sampleValid_; }
  uint16_t value(size_t index) const { return index < 12 ? readings_[index] : 0; }
  unsigned long sampleAgeMs() const { return sampleValid_ ? millis() - lastSampleMs_ : 0; }
  uint16_t gainCode() const { return 8; }
  uint16_t gainMultiplier() const { return 256; }

 private:
  static constexpr unsigned long kReadTimeoutMs = 2000UL;
  Adafruit_AS7341 sensor_;
  uint16_t readings_[12] = {0};
  unsigned long lastReadMs_ = 0;
  unsigned long lastSampleMs_ = 0;
  bool ready_ = false;
  bool sampleValid_ = false;
};
