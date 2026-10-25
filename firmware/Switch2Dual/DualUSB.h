#pragma once
#include <Arduino.h>
#include "tusb.h"

namespace dualUsb {
enum Channel : uint8_t { Pad = 0, Keyboard = 1 };
extern volatile uint8_t keyboardLeds;
bool begin(bool keyboardFirst = false);
bool enabled();
bool mounted();
bool setEnabled(bool value);
bool ready(Channel channel);
bool send(Channel channel, const void *report, uint16_t length, uint32_t timeoutMs = 40);
void observeLink();
bool inputReady(Channel channel);
void tickKeyboardIdle();
String diagnostic(Channel channel);
}
