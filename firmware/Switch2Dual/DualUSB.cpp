// Project-local two-interface HID driver, built against the installed TinyUSB ABI.
// HID control/endpoint handling follows TinyUSB's hid_device.c (MIT; see LICENSE).
#include "DualUSB.h"
#include "esp32-hal-tinyusb.h"
#include "device/usbd_pvt.h"
#include "SwitchDescriptor.h"
#include <atomic>

namespace dualUsb {
static const uint8_t keyboardDescriptor[] = { TUD_HID_REPORT_DESC_KEYBOARD() };
volatile uint8_t keyboardLeds = 0;
static bool keyboardFirst = false;
static bool linkActive = false;
static uint32_t linkSince = 0;
static std::atomic<bool> usbEnabled{true};

struct InterfaceState {
  uint8_t itf = 0, epIn = 0, epOut = 0, protocol = HID_PROTOCOL_REPORT, idle = 0;
  volatile bool opened = false, txSuccess = false;
  volatile uint32_t sent = 0, failed = 0, descriptorRequests = 0, controlRequests = 0;
  volatile uint32_t lastSent = 0;
  const tusb_hid_descriptor_hid_t *hidDescriptor = nullptr;
  SemaphoreHandle_t mutex = nullptr, completed = nullptr;
  alignas(4) uint8_t tx[64] = {}, rx[64] = {}, control[64] = {}, last[8] = {};
};
static InterfaceState interfaces[2];

static int byInterface(uint8_t itf) {
  for (int i = 0; i < 2; ++i) if (interfaces[i].itf == itf) return i;
  return -1;
}
static int byEndpoint(uint8_t ep) {
  for (int i = 0; i < 2; ++i)
    if (interfaces[i].opened && (interfaces[i].epIn == ep || interfaces[i].epOut == ep)) return i;
  return -1;
}

static void reset(uint8_t) {
  const uint8_t neutralPad[8] = {0,0,15,128,128,128,128,0};
  for (unsigned index = 0; index < 2; ++index) {
    auto &s = interfaces[index];
    s.opened = false;
    s.protocol = HID_PROTOCOL_REPORT;
    s.idle = 0;
    s.txSuccess = false;
    s.hidDescriptor = nullptr;
    memset(s.last, 0, sizeof(s.last));
    if (index == Pad) memcpy(s.last, neutralPad, sizeof(neutralPad));
  }
  keyboardLeds = 0;
}
static void init() { reset(0); }
static bool deinit() { reset(0); return true; }

static uint16_t open(uint8_t rhport, const tusb_desc_interface_t *desc, uint16_t maxLen) {
  if (desc->bInterfaceClass != TUSB_CLASS_HID || desc->bNumEndpoints != 2) return 0;
  const int index = byInterface(desc->bInterfaceNumber);
  if (index < 0 || maxLen < TUD_HID_INOUT_DESC_LEN) return 0;
  auto &s = interfaces[index];
  const uint8_t *p = tu_desc_next(desc);
  if (tu_desc_type(p) != HID_DESC_TYPE_HID) return 0;
  s.hidDescriptor = reinterpret_cast<const tusb_hid_descriptor_hid_t *>(p);
  p = tu_desc_next(p);
  if (!usbd_open_edpt_pair(rhport, p, 2, TUSB_XFER_INTERRUPT, &s.epOut, &s.epIn)) return 0;
  s.opened = true;
  if (!usbd_edpt_xfer(rhport, s.epOut, s.rx, sizeof(s.rx), false)) return 0;
  return TUD_HID_INOUT_DESC_LEN;
}

static bool control(uint8_t rhport, uint8_t stage, const tusb_control_request_t *request) {
  if (request->bmRequestType_bit.recipient != TUSB_REQ_RCPT_INTERFACE) return false;
  const int index = byInterface(uint8_t(request->wIndex));
  if (index < 0) return false;
  auto &s = interfaces[index];
  if (stage == CONTROL_STAGE_SETUP) ++s.controlRequests;
  if (request->bmRequestType_bit.type == TUSB_REQ_TYPE_STANDARD) {
    if (stage != CONTROL_STAGE_SETUP) return true;
    if (request->bRequest != TUSB_REQ_GET_DESCRIPTOR) return false;
    const uint8_t type = tu_u16_high(request->wValue);
    if (type == HID_DESC_TYPE_HID && s.hidDescriptor)
      return tud_control_xfer(rhport, request, (void *)s.hidDescriptor, s.hidDescriptor->bLength);
    if (type == HID_DESC_TYPE_REPORT) {
      ++s.descriptorRequests;
      const uint8_t *descriptor = index == Keyboard ? keyboardDescriptor : switchDescriptor;
      const uint16_t length = index == Keyboard ? sizeof(keyboardDescriptor) : sizeof(switchDescriptor);
      return tud_control_xfer(rhport, request, (void *)descriptor, length);
    }
    return false;
  }
  if (request->bmRequestType_bit.type != TUSB_REQ_TYPE_CLASS) return false;
  switch (request->bRequest) {
    case HID_REQ_CONTROL_GET_REPORT:
      if (stage == CONTROL_STAGE_SETUP) {
        if (tu_u16_low(request->wValue) != 0) return false;
        const uint8_t type = tu_u16_high(request->wValue);
        if (type == HID_REPORT_TYPE_INPUT) {
          memcpy(s.control, s.last, 8);
          return tud_control_xfer(rhport, request, s.control, 8);
        }
        if (type == HID_REPORT_TYPE_OUTPUT && index == Keyboard) {
          s.control[0] = keyboardLeds;
          return tud_control_xfer(rhport, request, s.control, 1);
        }
        return false;
      }
      return true;
    case HID_REQ_CONTROL_SET_REPORT:
      if (request->wLength > sizeof(s.control) || tu_u16_low(request->wValue) != 0) return false;
      if (stage == CONTROL_STAGE_SETUP)
        return tud_control_xfer(rhport, request, s.control, request->wLength);
      if (stage == CONTROL_STAGE_ACK && index == Keyboard && request->wLength &&
          tu_u16_high(request->wValue) == HID_REPORT_TYPE_OUTPUT) keyboardLeds = s.control[0];
      return true;
    case HID_REQ_CONTROL_SET_IDLE:
      if (stage == CONTROL_STAGE_SETUP) {
        s.idle = tu_u16_high(request->wValue);
        return tud_control_status(rhport, request);
      }
      return true;
    case HID_REQ_CONTROL_GET_IDLE:
      if (stage == CONTROL_STAGE_SETUP) {
        s.control[0] = s.idle;
        return tud_control_xfer(rhport, request, s.control, 1);
      }
      return true;
    case HID_REQ_CONTROL_SET_PROTOCOL:
      if (stage == CONTROL_STAGE_SETUP) {
        if (request->wValue > 1) return false;
        s.protocol = uint8_t(request->wValue);
        return tud_control_status(rhport, request);
      }
      return true;
    case HID_REQ_CONTROL_GET_PROTOCOL:
      if (stage == CONTROL_STAGE_SETUP) {
        s.control[0] = s.protocol;
        return tud_control_xfer(rhport, request, s.control, 1);
      }
      return true;
    default: return false;
  }
}

static bool transfer(uint8_t rhport, uint8_t ep, xfer_result_t result, uint32_t count) {
  const int index = byEndpoint(ep);
  if (index < 0) return false;
  auto &s = interfaces[index];
  if (ep == s.epIn) {
    s.txSuccess = result == XFER_RESULT_SUCCESS && count == 8;
    if (s.txSuccess) { ++s.sent; s.lastSent = millis(); } else ++s.failed;
    xSemaphoreGive(s.completed);
  } else {
    if (index == Keyboard && result == XFER_RESULT_SUCCESS && count) keyboardLeds = s.rx[0];
    return usbd_edpt_xfer(rhport, s.epOut, s.rx, sizeof(s.rx), false);
  }
  return true;
}

static const usbd_class_driver_t driver = {
  .name = "Independent Keyboard+Pad HID",
  .init = init, .deinit = deinit, .reset = reset, .open = open,
  .control_xfer_cb = control, .xfer_cb = transfer, .xfer_isr = nullptr, .sof = nullptr
};

static uint16_t loadDescriptor(uint8_t *dst, uint8_t *itf) {
  const uint8_t padString = tinyusb_add_string_descriptor("Switch Gamepad");
  const uint8_t keyString = tinyusb_add_string_descriptor("Boot Keyboard");
  for (uint8_t n = 0; n < 2; ++n) {
    const bool isKeyboard = keyboardFirst ? n == 0 : n == 1;
    const uint8_t ep = n + 1;
    const uint8_t descriptor[] = {
      TUD_HID_INOUT_DESCRIPTOR(*itf, isKeyboard ? keyString : padString,
        isKeyboard ? HID_ITF_PROTOCOL_KEYBOARD : HID_ITF_PROTOCOL_NONE,
        isKeyboard ? sizeof(keyboardDescriptor) : sizeof(switchDescriptor), ep,
        uint8_t(0x80 | ep), 64, 1)
    };
    memcpy(dst + n * TUD_HID_INOUT_DESC_LEN, descriptor, sizeof(descriptor));
    ++*itf;
  }
  return 2 * TUD_HID_INOUT_DESC_LEN;
}

bool begin(bool first) {
  keyboardFirst = first;
  for (int i = 0; i < 2; ++i) {
    auto &s = interfaces[i];
    s.itf = keyboardFirst ? 1 - i : i;
    s.mutex = xSemaphoreCreateMutex();
    s.completed = xSemaphoreCreateBinary();
    if (!s.mutex || !s.completed) return false;
  }
  return tinyusb_enable_interface(USB_INTERFACE_CUSTOM, 2 * TUD_HID_INOUT_DESC_LEN, loadDescriptor) == ESP_OK;
}

bool enabled() { return usbEnabled.load(std::memory_order_acquire); }
bool mounted() {
  return enabled() && tud_mounted() && interfaces[Pad].opened && interfaces[Keyboard].opened;
}
bool setEnabled(bool value) {
  if (value == enabled()) return true;
  linkActive = false;
  if (!value) {
    // Prevent the independent USB task from submitting more reports.
    usbEnabled.store(false, std::memory_order_release);
    return tud_disconnect();
  }
  // Cached GET_REPORT data must never contain a key from the previous session.
  reset(0);
  if (!tud_connect()) return false;
  usbEnabled.store(true, std::memory_order_release);
  return true;
}

bool ready(Channel channel) {
  auto &s = interfaces[channel];
  return enabled() && tud_mounted() && !tud_suspended() && s.opened && !usbd_edpt_busy(0, s.epIn);
}

bool send(Channel channel, const void *report, uint16_t length, uint32_t timeoutMs) {
  auto &s = interfaces[channel];
  if (length != 8 || !s.mutex || xSemaphoreTake(s.mutex, pdMS_TO_TICKS(timeoutMs)) != pdTRUE) return false;
  bool success = false;
  if (ready(channel) && usbd_edpt_claim(0, s.epIn)) {
    xSemaphoreTake(s.completed, 0);
    s.txSuccess = false;
    memcpy(s.tx, report, 8); memcpy(s.last, report, 8);
    if (usbd_edpt_xfer(0, s.epIn, s.tx, 8, false)) {
      if (xSemaphoreTake(s.completed, pdMS_TO_TICKS(timeoutMs)) == pdTRUE) success = s.txSuccess;
    } else usbd_edpt_release(0, s.epIn);
  }
  xSemaphoreGive(s.mutex);
  return success;
}

void observeLink() {
  const bool active = mounted() && !tud_suspended();
  if (!active) linkActive = false;
  else if (!linkActive) { linkActive = true; linkSince = millis(); }
}
bool inputReady(Channel channel) {
  return linkActive && mounted() && !tud_suspended() && interfaces[channel].opened &&
         interfaces[channel].sent > 0 && uint32_t(millis() - linkSince) >= 1000;
}
void tickKeyboardIdle() {
  auto &s = interfaces[Keyboard];
  if (s.idle && ready(Keyboard) && uint32_t(millis() - s.lastSent) >= uint32_t(s.idle) * 4) {
    uint8_t report[8]; memcpy(report, s.last, sizeof(report)); send(Keyboard, report, sizeof(report));
  }
}
String diagnostic(Channel channel) {
  auto &s = interfaces[channel];
  String result = "{\"interface\":" + String(s.itf) + ",\"ep_in\":" + String(s.epIn);
  result += ",\"opened\":"; result += s.opened ? "true" : "false";
  result += ",\"input_ready\":"; result += inputReady(channel) ? "true" : "false";
  result += ",\"reports_ok\":" + String(s.sent) + ",\"reports_failed\":" + String(s.failed);
  result += ",\"report_descriptor_requests\":" + String(s.descriptorRequests);
  result += ",\"control_requests\":" + String(s.controlRequests) + ",\"protocol\":" + String(s.protocol) + "}";
  return result;
}
}

extern "C" const usbd_class_driver_t *usbd_app_driver_get_cb(uint8_t *count) {
  *count = 1;
  return &dualUsb::driver;
}
