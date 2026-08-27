#pragma once

#include <Arduino.h>
#include <esp_arduino_version.h>
#include <soc/gpio_reg.h>
#include <soc/soc.h>

/*
 * Minimal AREA LZ7 DALI direct-arc transmitter.
 *
 * The previous gateway pulled arduino-dali plus TimerInterrupt_Generic into
 * PlatformIO. That dependency chain creates deep nested build paths and was
 * the source of the AREA LZ7 long-path failure on Windows when the repository
 * is extracted below a long directory name.
 *
 * AREA LZ7 only needs direct-arc writes, so this small transmitter keeps the
 * deployed GPIO16/GPIO17 contract and generates the DALI Manchester waveform
 * from an ESP32 hardware timer. No commissioning or third-party timer library
 * is required.
 */
namespace area_lz7_dali {

constexpr uint32_t kHalfBitUs = 417U;  // DALI TE ~= 416.7 us (1200 bit/s)
constexpr uint8_t kFrameHalfBits = 38U;  // start + 16 data bits + stop period

static hw_timer_t *timerHandle = nullptr;
static volatile uint16_t pendingFrame = 0;
static volatile uint8_t halfBitIndex = 0;
static volatile bool transmitting = false;
static uint8_t txPin = 17;
static bool activeLow = true;

static inline void IRAM_ATTR writePhysicalPin(bool high) {
  const uint32_t mask = 1UL << txPin;
  if (high) {
    REG_WRITE(GPIO_OUT_W1TS_REG, mask);
  } else {
    REG_WRITE(GPIO_OUT_W1TC_REG, mask);
  }
}

static inline void IRAM_ATTR writeBusLevel(bool logicalHigh) {
  const bool physicalHigh = activeLow ? !logicalHigh : logicalHigh;
  writePhysicalPin(physicalHigh);
}

static void IRAM_ATTR onTimer() {
  if (!transmitting) {
    writeBusLevel(true);  // idle bus
    return;
  }

  const uint8_t step = halfBitIndex;
  bool logicalHigh = true;

  if (step < 2U) {
    // DALI start bit: logical 1 -> LOW then HIGH in Manchester coding.
    logicalHigh = (step == 1U);
  } else if (step < 34U) {
    const uint8_t dataStep = static_cast<uint8_t>(step - 2U);
    const uint8_t bitOffset = static_cast<uint8_t>(dataStep >> 1U);
    const uint8_t bitIndex = static_cast<uint8_t>(15U - bitOffset);
    const bool bitValue = ((pendingFrame >> bitIndex) & 0x01U) != 0U;
    const bool secondHalf = (dataStep & 0x01U) != 0U;

    // DALI Manchester coding used by the deployed active-low interface:
    // bit 1 = LOW/HIGH, bit 0 = HIGH/LOW.
    logicalHigh = bitValue ? secondHalf : !secondHalf;
  } else {
    // Hold the bus idle-high for four half-bit periods after the frame.
    logicalHigh = true;
  }

  writeBusLevel(logicalHigh);

  const uint8_t next = static_cast<uint8_t>(step + 1U);
  if (next >= kFrameHalfBits) {
    halfBitIndex = 0U;
    transmitting = false;
    writeBusLevel(true);
  } else {
    halfBitIndex = next;
  }
}

static bool begin(uint8_t pin, bool useActiveLow) {
  txPin = pin;
  activeLow = useActiveLow;
  pinMode(txPin, OUTPUT);
  writeBusLevel(true);

#if ESP_ARDUINO_VERSION_MAJOR >= 3
  // Arduino-ESP32 3.x: timerBegin() receives the timer frequency directly.
  timerHandle = timerBegin(1000000U);  // 1 MHz -> alarm value is microseconds
  if (timerHandle == nullptr) return false;
  timerAttachInterrupt(timerHandle, &onTimer);
  timerAlarm(timerHandle, kHalfBitUs, true, 0U);
#else
  // Arduino-ESP32 2.x compatibility for local maintenance builds.
  timerHandle = timerBegin(1U, 80U, true);  // 80 MHz / 80 = 1 MHz
  if (timerHandle == nullptr) return false;
  timerAttachInterrupt(timerHandle, &onTimer, true);
  timerAlarmWrite(timerHandle, kHalfBitUs, true);
  timerAlarmEnable(timerHandle);
#endif

  return true;
}

static bool sendArc(uint8_t shortAddress, uint8_t value) {
  if (transmitting || shortAddress > 63U) return false;

  // DALI direct-arc forward frame for a short address:
  // first byte = short_address << 1 (selector bit = 0), second byte = level.
  const uint8_t addressByte = static_cast<uint8_t>((shortAddress & 0x3FU) << 1U);
  pendingFrame = static_cast<uint16_t>((static_cast<uint16_t>(addressByte) << 8U) | value);
  halfBitIndex = 0U;

  // Set this last so the ISR never sees a partially prepared frame.
  transmitting = true;
  return true;
}

}  // namespace area_lz7_dali

class AreaLz7Device {
 public:
  static constexpr size_t kChannelCount = 6;

  // These pins are fixed by the existing AREA LZ7 PCB and must not be remapped.
  static constexpr uint8_t kDaliTxPin = 17;
  static constexpr uint8_t kDaliRxPin = 16;

  void begin() {
    pinMode(kDaliRxPin, INPUT);
    const bool ok = area_lz7_dali::begin(kDaliTxPin, true);
    Serial.printf("[AREA LZ7] DALI TX %s, RX=%u, TX=%u (PCB fixed)\n",
                  ok ? "ready" : "timer init failed", kDaliRxPin, kDaliTxPin);
  }

  void task() {
    const unsigned long now = millis();
    if (now - lastTransmitMs_ < 100UL) return;
    lastTransmitMs_ = now;

    if (area_lz7_dali::sendArc(static_cast<uint8_t>(nextAddress_),
                               daliLevel(nextAddress_))) {
      nextAddress_ = (nextAddress_ + 1U) % kChannelCount;
    }
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

  uint8_t level(size_t index) const {
    return index < kChannelCount ? levels_[index] : 0;
  }

  uint8_t daliLevel(size_t index) const {
    if (index >= kChannelCount) return 0;
    return static_cast<uint8_t>((static_cast<uint16_t>(levels_[index]) * 254U + 50U) / 100U);
  }

 private:
  uint8_t levels_[kChannelCount] = {0};
  size_t nextAddress_ = 1;
  unsigned long lastTransmitMs_ = 0;
};
