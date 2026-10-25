#pragma once
#include <Arduino.h>

namespace bleWake {
void begin();
// nullptr means the one-second broadcast started; otherwise an error code.
const char *start();
bool stop();
String statusJson();
}
