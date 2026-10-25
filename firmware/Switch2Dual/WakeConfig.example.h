#pragma once
#include <stdint.h>

// Replace with the Bluetooth address and complete raw advertisement captured
// from your own paired controller. These placeholders do not wake a console.
namespace wakeConfig {
constexpr uint8_t address[6] = {0, 0, 0, 0, 0, 0};
constexpr uint8_t advertisement[31] = {};
static_assert(sizeof(advertisement) == 31, "Wake advertisement must be exactly 31 bytes");
}
