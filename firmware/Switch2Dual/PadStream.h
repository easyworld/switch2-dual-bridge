#pragma once
#include "ControllerLogic.h"

// Ordered transitions, independent from HTTP and legacy finite pulses.
// A transition cannot be skipped until the host has acknowledged its report.
class PadStream {
public:
  static constexpr unsigned Capacity = 16;
  static constexpr uint32_t LeaseMs = 600;
  struct Frame { PadReport report; uint16_t duration = 16; uint32_t queuedAt = 0; };
  Frame queue[Capacity];
  PadReport report;
  char session[33] = {};
  unsigned head = 0, count = 0;
  uint32_t nextSequence = 0, generation = 0, receivedAt = 0, appliedAt = 0;
  bool active = false, acknowledged = false, loaded = false;

  static bool neutral(const PadReport &r) {
    return !r.buttons && r.dpad == 15 && r.lx == 128 && r.ly == 128 && r.rx == 128 && r.ry == 128;
  }
  bool idle() const { return count == 0 && acknowledged && neutral(report); }
  void stop() {
    active = acknowledged = loaded = false; count = head = 0;
    report = PadReport{}; session[0] = 0; ++generation;
  }
  // Returns nullptr on acceptance. Duplicated packets never enqueue twice or
  // extend the lease. A new session must begin at sequence zero.
  const char *accept(const char *id, uint32_t sequence, const Frame *frames,
                     unsigned size, uint32_t now) {
    if (active && strcmp(session, id)) return "stream_owned";
    if (active && sequence == nextSequence - 1) return nullptr;
    if (sequence != (active ? nextSequence : 0)) return "stream_sequence";
    if (size > Capacity - count) return "stream_queue_full";
    if (!active) {
      if (!size) return "stream_empty_start";
      strncpy(session, id, sizeof(session)-1); session[32] = 0;
      active = true; acknowledged = false; nextSequence = 0;
    }
    receivedAt = now; ++nextSequence;
    for (unsigned i = 0; i < size; ++i) {
      Frame &target = queue[(head + count++) % Capacity];
      target = frames[i]; target.queuedAt = now;
    }
    return nullptr;
  }
  // Caller handles expiry by stopping the stream AND incrementing the shared
  // epoch, so delayed packets from an expired session cannot press a key again.
  bool expired(uint32_t now) const {
    return active && (uint32_t(now - receivedAt) >= LeaseMs ||
      (count && uint32_t(now - queue[head].queuedAt) >= 500));
  }
  void tick(uint32_t now) {
    if (!active || !count) return;
    if (loaded) {
      if (!acknowledged || uint32_t(now - appliedAt) < queue[head].duration) return;
      head = (head + 1) % Capacity; --count; loaded = false;
    }
    if (count && !loaded) {
      report = queue[head].report; acknowledged = false; loaded = true; ++generation;
    }
  }
  void acknowledge(uint32_t sentGeneration, uint32_t now, bool success) {
    if (active && count && !acknowledged && sentGeneration == generation && success) {
      acknowledged = true; appliedAt = now;
    }
  }
};
