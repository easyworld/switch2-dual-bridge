#pragma once
#include <stdint.h>
#include <string.h>

struct __attribute__((packed)) PadReport {
  uint16_t buttons = 0;
  uint8_t dpad = 15;
  uint8_t lx = 128, ly = 128, rx = 128, ry = 128;
  uint8_t padding = 0;
};
static_assert(sizeof(PadReport) == 8, "Switch report must be eight bytes");

// Caller holds the state lock. This class never performs USB or network I/O.
class ControllerLogic {
public:
  PadReport report;
  uint32_t epoch = 1, generation = 0, acceptedAt = 0, releaseAt = 0;
  uint32_t reportsOk = 0, reportsFailed = 0;
  uint16_t holdMs = 0;
  bool busy = false, delivered = false, completed = false;
  bool releasePending = true, finishing = false;
  char error[48] = {}, job[65] = {};

  bool start(const PadReport &input, uint16_t hold, const char *id,
             uint32_t expectedEpoch, uint32_t now) {
    if (expectedEpoch != epoch || busy || releasePending) return false;
    report = input;
    holdMs = hold;
    acceptedAt = now;
    busy = true;
    delivered = completed = finishing = false;
    error[0] = 0;
    strncpy(job, id, sizeof(job) - 1);
    job[sizeof(job) - 1] = 0;
    ++generation;
    return true;
  }

  void cancel(const char *reason) {
    busy = delivered = completed = finishing = false;
    report = PadReport{};
    releasePending = true;
    strncpy(error, reason, sizeof(error) - 1);
    error[sizeof(error) - 1] = 0;
    ++epoch;
    ++generation;
  }

  void tick(uint32_t now, bool mounted, bool suspended) {
    if (!busy) return;
    if (!mounted || suspended) {
      cancel("usb_disconnected_or_suspended");
    } else if (!delivered && uint32_t(now - acceptedAt) >= 1000) {
      cancel("usb_press_timeout");
    } else if (delivered && int32_t(now - releaseAt) >= 0) {
      busy = false;
      finishing = releasePending = true;
      report = PadReport{};
      ++generation;
    }
  }

  void acknowledge(uint32_t sentGeneration, uint32_t now, bool success) {
    if (success) ++reportsOk; else ++reportsFailed;
    if (sentGeneration != generation || !success) return;
    if (busy && !delivered) {
      delivered = true;
      releaseAt = now + holdMs;
    } else if (!busy && releasePending) {
      releasePending = false;
      if (finishing) {
        completed = true;
        finishing = false;
      }
    }
  }
};
