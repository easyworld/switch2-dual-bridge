#pragma once
#include <stdint.h>
struct Stroke { uint8_t modifier; uint8_t key; };
// USB HID keyboard usages. US and Japanese JIS punctuation are different.
inline bool mapCharacter(uint8_t c, bool jis, Stroke &out) {
  out = {0, 0};
  if (c >= 'a' && c <= 'z') { out.key = 4 + c - 'a'; return true; }
  if (c >= 'A' && c <= 'Z') { out = {2, uint8_t(4 + c - 'A')}; return true; }
  if (c >= '1' && c <= '9') { out.key = 0x1e + c - '1'; return true; }
  if (c == '0') { out.key = 0x27; return true; }
  if (c == '\n') { out.key = 0x28; return true; }
  if (c == '\t') { out.key = 0x2b; return true; }
  if (c == ' ') { out.key = 0x2c; return true; }
  const char *plain = jis ? "-^@[;:],./\\" : "-=[]\\;'`,./";
  const uint8_t jp[] = {0x2d,0x2e,0x2f,0x30,0x33,0x34,0x32,0x36,0x37,0x38,0x89};
  const uint8_t us[] = {0x2d,0x2e,0x2f,0x30,0x31,0x33,0x34,0x35,0x36,0x37,0x38};
  for (unsigned i=0; plain[i]; ++i) if (c == uint8_t(plain[i])) {
    out.key = jis ? jp[i] : us[i]; return true;
  }
  const char *shift = jis ? "!\"#$%&'()=~`{+*}<>?|_" : "!@#$%^&*()_+{}|:\"~<>?";
  const uint8_t js[] = {0x1e,0x1f,0x20,0x21,0x22,0x23,0x24,0x25,0x26,0x2d,0x2e,0x2f,0x30,0x33,0x34,0x32,0x36,0x37,0x38,0x89,0x87};
  const uint8_t ss[] = {0x1e,0x1f,0x20,0x21,0x22,0x23,0x24,0x25,0x26,0x27,0x2d,0x2e,0x2f,0x30,0x31,0x33,0x34,0x35,0x36,0x37,0x38};
  for (unsigned i=0; shift[i]; ++i) if (c == uint8_t(shift[i])) {
    out = {2, jis ? js[i] : ss[i]}; return true;
  }
  return false;
}
