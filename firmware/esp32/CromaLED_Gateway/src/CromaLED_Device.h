#pragma once

#include <Arduino.h>
#include <HardwareSerial.h>

// Adapter for the original CromaLED lamp protocol.
//
// IMPORTANT: the deployed CromaLED gateway used UART0 (Arduino `Serial`) at
// 9200 baud. It did not use UART2 / GPIO16 / GPIO17. The secure gateway keeps
// UART0 at 115200 only for flashing/factory/bootstrap diagnostics, then hands
// the same UART0 back to the lamp after MQTT/mTLS is operational.
class CromaLEDDevice {
 public:
  static constexpr size_t kChannelCount = 11;
  static constexpr uint32_t kLampBaud = 9200;

  explicit CromaLEDDevice(HardwareSerial &serial) : serial_(serial) {}

  void begin() {
    // Match the original firmware exactly: default UART0 pins, SERIAL_8N1,
    // 9200 baud. Do not remap RX/TX pins here.
    serial_.begin(kLampBaud, SERIAL_8N1);
    serial_.setTimeout(20);
    started_ = true;
    sendFrame();
  }

  bool started() const { return started_; }

  void task() {
    if (!started_) return;
    readTemperatureNonBlocking();
    const unsigned long now = millis();
    if (now - lastTransmitMs_ >= 400UL) {
      lastTransmitMs_ = now;
      sendFrame();
    }
  }

  void setChannel(size_t index, uint8_t percent) {
    if (index >= kChannelCount) return;
    levels_[index] = percent > 100 ? 100 : percent;
    if (started_) sendFrame();
  }

  void setAll(uint8_t percent) {
    const uint8_t clamped = percent > 100 ? 100 : percent;
    for (size_t index = 0; index < kChannelCount; ++index) levels_[index] = clamped;
    if (started_) sendFrame();
  }

  void setChannels(const uint8_t *levels, size_t count) {
    if (levels == nullptr || count != kChannelCount) return;
    for (size_t index = 0; index < kChannelCount; ++index) {
      levels_[index] = levels[index] > 100 ? 100 : levels[index];
    }
    if (started_) sendFrame();
  }

  uint8_t level(size_t index) const { return index < kChannelCount ? levels_[index] : 0; }
  int temperatureC() const { return temperatureC_; }
  bool temperatureValid() const { return temperatureValid_; }

 private:
  void appendLevel(char *frame, size_t offset, uint8_t value) {
    // Exact legacy representation: 000- ... 100-
    frame[offset] = static_cast<char>('0' + (value / 100U));
    frame[offset + 1U] = static_cast<char>('0' + ((value / 10U) % 10U));
    frame[offset + 2U] = static_cast<char>('0' + (value % 10U));
    frame[offset + 3U] = '-';
  }

  void sendFrame() {
    if (!started_) return;

    // Original protocol from CromaLED.h:
    // 11 x "DDD-" followed by '\n'. Example:
    // 100-000-050-000-000-000-000-000-000-000-025-\n
    char frame[kChannelCount * 4U + 2U] = {0};
    for (size_t index = 0; index < kChannelCount; ++index) {
      appendLevel(frame, index * 4U, levels_[index]);
    }
    frame[kChannelCount * 4U] = '\n';
    frame[kChannelCount * 4U + 1U] = '\0';
    serial_.write(reinterpret_cast<const uint8_t *>(frame), kChannelCount * 4U + 1U);
  }

  void commitTemperatureBuffer() {
    if (temperatureLength_ == 0) return;
    temperatureBuffer_[temperatureLength_] = '\0';
    char *end = nullptr;
    const long parsed = strtol(temperatureBuffer_, &end, 10);
    if (end != temperatureBuffer_) {
      temperatureC_ = static_cast<int>(parsed);
      temperatureValid_ = true;
    }
    temperatureLength_ = 0;
  }

  void readTemperatureNonBlocking() {
    const unsigned long now = millis();
    while (serial_.available() > 0) {
      const char value = static_cast<char>(serial_.read());
      lastRxByteMs_ = now;
      if ((value >= '0' && value <= '9') || (value == '-' && temperatureLength_ == 0)) {
        if (temperatureLength_ + 1U < sizeof(temperatureBuffer_)) {
          temperatureBuffer_[temperatureLength_++] = value;
        }
      } else if (value == '\r' || value == '\n') {
        commitTemperatureBuffer();
      }
    }
    if (temperatureLength_ > 0 && now - lastRxByteMs_ >= 25UL) {
      commitTemperatureBuffer();
    }
  }

  HardwareSerial &serial_;
  bool started_ = false;
  uint8_t levels_[kChannelCount] = {0};
  int temperatureC_ = 0;
  bool temperatureValid_ = false;
  unsigned long lastTransmitMs_ = 0;
  unsigned long lastRxByteMs_ = 0;
  char temperatureBuffer_[16] = {0};
  size_t temperatureLength_ = 0;
};
