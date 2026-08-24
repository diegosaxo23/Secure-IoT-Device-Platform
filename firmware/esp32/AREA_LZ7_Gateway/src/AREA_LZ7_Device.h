#pragma once

#include <Arduino.h>
#include <Dali.h>

class AreaLz7Device {
 public:
  static constexpr size_t kChannelCount = 6;

  // These pins are fixed by the existing AREA LZ7 PCB and must not be remapped.
  static constexpr uint8_t kDaliTxPin = 17;
  static constexpr uint8_t kDaliRxPin = 16;

  void begin() {
    Dali.begin(kDaliTxPin, kDaliRxPin, true);
    Serial.printf("[AREA LZ7] DALI bus ready, RX=%u, TX=%u (PCB fixed)\n",
                  kDaliRxPin, kDaliTxPin);
  }

  void task() {
    const unsigned long now = millis();
    if (now - lastTransmitMs_ < 100UL) return;
    lastTransmitMs_ = now;
    Dali.sendArc(static_cast<byte>(nextAddress_), daliLevel(nextAddress_));
    nextAddress_ = (nextAddress_ + 1U) % kChannelCount;
  }

  void setChannel(size_t index, uint8_t percent) {
    if (index >= kChannelCount) return;
    levels_[index] = percent > 100 ? 100 : percent;
  }

  void setAll(uint8_t percent) {
    const uint8_t clamped = percent > 100 ? 100 : percent;
    for (size_t index = 0; index < kChannelCount; ++index) levels_[index] = clamped;
  }

  void setChannels(const uint8_t *levels, size_t count) {
    if (levels == nullptr || count != kChannelCount) return;
    for (size_t index = 0; index < kChannelCount; ++index) {
      levels_[index] = levels[index] > 100 ? 100 : levels[index];
    }
  }

  uint8_t level(size_t index) const { return index < kChannelCount ? levels_[index] : 0; }

  uint8_t daliLevel(size_t index) const {
    if (index >= kChannelCount) return 0;
    return static_cast<uint8_t>((static_cast<uint16_t>(levels_[index]) * 254U + 50U) / 100U);
  }

 private:
  uint8_t levels_[kChannelCount] = {0};
  size_t nextAddress_ = 1;
  unsigned long lastTransmitMs_ = 0;
};
