#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <Preferences.h>
#include <USB.h>
#include "DualUSB.h"
#include "BLEWake.h"
#include "tusb.h"
#include "esp_wifi.h"
#include "esp_system.h"
#include "ControllerLogic.h"
#include "PadStream.h"
#include "SwitchDescriptor.h"
#include "secrets.h"
#include "keymap.h"

class SwitchHID {
public:
  struct Endpoint { bool ready() { return dualUsb::ready(dualUsb::Pad); } } hid;
  bool send(const PadReport &report) { return dualUsb::send(dualUsb::Pad,&report,sizeof(report),40); }
};
SwitchHID gamepadStorage;
SwitchHID *gamepad = &gamepadStorage;

WebServer server(80);
Preferences preferences;
ControllerLogic state;
PadStream padStream;
portMUX_TYPE stateLock = portMUX_INITIALIZER_UNLOCKED;
char recentJobs[16][65] = {};
unsigned recentNext = 0;
bool logicSelfTest = false;
bool switchPending = false;
bool hostDisconnecting = false;
uint32_t bootId = 0, switchStarted = 0;
String switchTarget;
#include "KeyboardMode.h"


ControllerLogic snapshot() {
  portENTER_CRITICAL(&stateLock);
  ControllerLogic copy = state;
  portEXIT_CRITICAL(&stateLock);
  return copy;
}

void fail(int code, const char *message) {
  server.send(code, "application/json", String("{\"error\":\"") + message + "\"}");
}
bool authorized() {
  if (server.header("X-Key") == API_KEY) return true;
  fail(401, "authentication_required");
  return false;
}

// Decimal-only parsing avoids silently accepting missing/negative/trailing data.
bool number(const char *name, uint32_t low, uint32_t high, uint32_t &result) {
  if (!server.hasArg(name)) return false;
  String value = server.arg(name);
  if (!value.length() || value.length() > 10) return false;
  uint64_t parsed = 0;
  for (unsigned i = 0; i < value.length(); ++i) {
    if (value[i] < '0' || value[i] > '9') return false;
    parsed = parsed * 10 + (value[i] - '0');
    if (parsed > high) return false;
  }
  if (parsed < low) return false;
  result = uint32_t(parsed);
  return true;
}

String padStatusJson() {
  portENTER_CRITICAL(&stateLock);
  const ControllerLogic s = state;
  const bool streamActive = padStream.active;
  const unsigned streamQueued = padStream.count;
  const uint32_t streamNext = padStream.nextSequence;
  const PadReport visibleReport = streamActive ? padStream.report : s.report;
  portEXIT_CRITICAL(&stateLock);
  String json;
  json.reserve(850);
  json = "{\"firmware\":\"2.0.0\",\"mode\":\"gamepad\",\"report_bytes\":8";
  json += ",\"sent\":0,\"total\":0,\"mode_switch_supported\":false,\"boot_id\":" + String(bootId);
  json += ",\"switch_pending\":"; json += switchPending ? "true" : "false";
  json += ",\"input_ready\":"; json += dualUsb::inputReady(dualUsb::Pad) ? "true" : "false";
  json += ",\"logic_self_test\":"; json += logicSelfTest ? "true" : "false";
  json += ",\"usb_mounted\":"; json += dualUsb::mounted() ? "true" : "false";
  json += ",\"usb_suspended\":"; json += tud_suspended() ? "true" : "false";
  json += ",\"hid_ready\":"; json += gamepad->hid.ready() ? "true" : "false";
  json += ",\"stream\":{\"supported\":true,\"active\":";
  json += streamActive ? "true" : "false";
  json += ",\"queued\":" + String(streamQueued) + ",\"next_sequence\":" + String(streamNext);
  json += ",\"lease_ms\":600,\"capacity\":16}";
  json += ",\"legacy_busy\":"; json += s.busy ? "true" : "false";
  json += ",\"busy\":"; json += (s.busy || streamActive) ? "true" : "false";
  json += ",\"release_pending\":"; json += s.releasePending ? "true" : "false";
  json += ",\"delivered\":"; json += s.delivered ? "true" : "false";
  json += ",\"completed\":"; json += s.completed ? "true" : "false";
  json += ",\"epoch\":" + String(s.epoch) + ",\"job\":\"" + s.job + "\",\"error\":\"" + s.error + "\"";
  json += ",\"buttons\":" + String(visibleReport.buttons) + ",\"dpad\":" + String(visibleReport.dpad);
  json += ",\"lx\":" + String(visibleReport.lx) + ",\"ly\":" + String(visibleReport.ly);
  json += ",\"rx\":" + String(visibleReport.rx) + ",\"ry\":" + String(visibleReport.ry);
  json += ",\"reports_ok\":" + String(s.reportsOk) + ",\"reports_failed\":" + String(s.reportsFailed);
  json += ",\"ip\":\"" + WiFi.softAPIP().toString() + "\",\"station_connected\":";
  json += WiFi.status() == WL_CONNECTED ? "true" : "false";
  json += ",\"station_ip\":\"" + WiFi.localIP().toString() + "\"}";
  return json;
}

void pulse() {
  if (!authorized()) return;
  if (hostDisconnecting || !dualUsb::enabled()) { fail(409,"host_disconnected"); return; }
  if (!dualUsb::inputReady(dualUsb::Pad)) { fail(409,"usb_connecting"); return; }
  if (!logicSelfTest) { fail(503, "logic_self_test_failed"); return; }
  // A busy interrupt endpoint is normal while the periodic report is in flight.
  // Accept into ControllerLogic; it waits up to one second for the first send.
  if (!tud_mounted() || tud_suspended()) {
    fail(409, "usb_not_ready"); return;
  }
  String id = server.header("X-Job");
  if (!id.length() || id.length() > 64) { fail(400, "job_id_required"); return; }
  for (unsigned i = 0; i < id.length(); ++i) {
    if (!isAlphaNumeric(id[i]) && id[i] != '-') { fail(400, "invalid_job_id"); return; }
  }
  for (auto &previous : recentJobs) {
    if (id == previous) { fail(409, "duplicate_job_id"); return; }
  }
  uint32_t buttons, dpad, lx, ly, rx, ry, hold, epoch;
  if (!number("buttons", 0, 16383, buttons) || !number("dpad", 0, 15, dpad) ||
      (dpad > 7 && dpad != 15) || !number("lx", 0, 255, lx) ||
      !number("ly", 0, 255, ly) || !number("rx", 0, 255, rx) ||
      !number("ry", 0, 255, ry) || !number("hold", 50, 2000, hold) ||
      !number("epoch", 0, UINT32_MAX, epoch)) {
    fail(400, "invalid_pad_state_or_duration"); return;
  }
  PadReport report;
  report.buttons = buttons; report.dpad = dpad;
  report.lx = lx; report.ly = ly; report.rx = rx; report.ry = ry;
  portENTER_CRITICAL(&stateLock);
  if (padStream.active && padStream.idle()) padStream.stop();
  const bool stale = epoch != state.epoch;
  const bool accepted = !padStream.active && state.start(report, hold, id.c_str(), epoch, millis());
  portEXIT_CRITICAL(&stateLock);
  if (!accepted) { fail(409, stale ? "stale_epoch" : "busy_or_releasing"); return; }
  id.toCharArray(recentJobs[recentNext++ % 16], 65);
  server.send(202, "application/json", String("{\"accepted\":true,\"job\":\"") + id + "\"}");
}

// Form-encoded frames: buttons,dpad,lx,ly,rx,ry,duration;... (at most 8).
void streamInput() {
  if (!authorized()) return;
  if (hostDisconnecting || !dualUsb::enabled()) { fail(409,"host_disconnected"); return; }
  if (!logicSelfTest || !dualUsb::inputReady(dualUsb::Pad)) { fail(409,"usb_not_ready"); return; }
  uint32_t epoch, sequence;
  if (!number("epoch",0,UINT32_MAX,epoch) || !number("sequence",0,UINT32_MAX-1,sequence)) {
    fail(400,"invalid_stream_sequence"); return;
  }
  String id = server.arg("session"), encoded = server.arg("frames");
  if (id.length() != 32 || encoded.length() > 1024) { fail(400,"invalid_stream_packet"); return; }
  for (unsigned i = 0; i < id.length(); ++i) if (!isHexadecimalDigit(id[i])) {
    fail(400,"invalid_stream_session"); return;
  }
  PadStream::Frame frames[8]; unsigned count = 0;
  const char *cursor = encoded.c_str();
  while (*cursor) {
    if (count == 8) { fail(400,"too_many_stream_frames"); return; }
    uint32_t values[7]; const uint32_t maxima[] = {16383,15,255,255,255,255,200};
    for (unsigned field = 0; field < 7; ++field) {
      if (*cursor < '0' || *cursor > '9') { fail(400,"invalid_stream_frame"); return; }
      uint64_t value = 0;
      while (*cursor >= '0' && *cursor <= '9') {
        value = value * 10 + (*cursor++ - '0');
        if (value > maxima[field]) { fail(400,"invalid_stream_frame"); return; }
      }
      values[field] = value;
      if (field < 6 && *cursor++ != ',') { fail(400,"invalid_stream_frame"); return; }
    }
    if ((values[1] > 7 && values[1] != 15) || values[6] < 8) { fail(400,"invalid_stream_frame"); return; }
    auto &frame = frames[count++];
    frame.report.buttons = values[0]; frame.report.dpad = values[1];
    frame.report.lx = values[2]; frame.report.ly = values[3];
    frame.report.rx = values[4]; frame.report.ry = values[5]; frame.duration = values[6];
    if (*cursor && *cursor++ != ';') { fail(400,"invalid_stream_frame"); return; }
  }
  portENTER_CRITICAL(&stateLock);
  const char *error = epoch != state.epoch ? "stale_epoch" :
    state.busy || state.releasePending ? "busy_or_releasing" :
    padStream.accept(id.c_str(),sequence,frames,count,millis());
  const uint32_t next = padStream.nextSequence;
  const unsigned queued = padStream.count;
  if (!error) state.error[0] = 0;
  portEXIT_CRITICAL(&stateLock);
  if (error) { fail(409,error); return; }
  server.send(200,"application/json",String("{\"accepted\":true,\"next_sequence\":") +
    String(next) + ",\"queued\":" + String(queued) + ",\"capacity\":16}");
}

// Runs independently of WebServer: slow/incomplete HTTP cannot extend a hold.
void usbTask(void *) {
  TickType_t lastWake = xTaskGetTickCount();
  for (;;) {
    const uint32_t now = millis();
    const bool mounted = dualUsb::mounted(), suspended = tud_suspended();
    portENTER_CRITICAL(&stateLock);
    if (padStream.active && (!mounted || suspended || padStream.expired(now))) {
      padStream.stop(); state.cancel("stream_expired_or_disconnected");
    }
    const bool streaming = padStream.active;
    if (streaming) padStream.tick(now); else state.tick(now, mounted, suspended);
    const PadReport report = streaming ? padStream.report : state.report;
    const uint32_t generation = streaming ? padStream.generation : state.generation;
    portEXIT_CRITICAL(&stateLock);
    if (mounted && !suspended && gamepad->hid.ready()) {
      const bool success = gamepad->send(report);
      portENTER_CRITICAL(&stateLock);
      if (streaming) {
        padStream.acknowledge(generation, millis(), success);
        if (success) ++state.reportsOk; else ++state.reportsFailed;
      }
      else if (!padStream.active) state.acknowledge(generation, millis(), success);
      portEXIT_CRITICAL(&stateLock);
    }
    vTaskDelayUntil(&lastWake, pdMS_TO_TICKS(8));
  }
}

bool selfTest() {
  ControllerLogic s;
  PadReport input;
  input.buttons = 4;
  if (s.start(input, 100, "test", s.epoch, 0)) return false; // Startup neutral required.
  s.acknowledge(s.generation, 0, true);
  if (!s.start(input, 100, "test", s.epoch, UINT32_MAX - 40)) return false;
  s.acknowledge(s.generation, UINT32_MAX - 40, true);
  s.tick(58, true, false);
  if (!s.busy) return false;
  s.tick(59, true, false); // Exact deadline across millis wrap.
  if (s.busy || !s.releasePending || s.completed || s.report.buttons) return false;
  s.acknowledge(s.generation, 60, true);
  if (!s.completed || s.releasePending) return false;
  const uint32_t oldEpoch = s.epoch;
  s.cancel("cancelled");
  s.acknowledge(s.generation, 61, true);
  if (s.start(input, 100, "stale", oldEpoch, 62)) return false;
  if (!s.start(input, 100, "timeout", s.epoch, 100)) return false;
  const uint32_t oldGeneration = s.generation;
  s.tick(1100, true, false);
  s.acknowledge(oldGeneration, 1101, true);
  if (s.busy || !s.releasePending || s.delivered || strcmp(s.error, "usb_press_timeout")) return false;
  s.acknowledge(s.generation, 1102, true);
  if (!s.start(input, 100, "disconnect", s.epoch, 1103)) return false;
  s.acknowledge(s.generation, 1104, true);
  s.tick(1105, false, false);
  return !s.busy && s.releasePending && s.report.buttons == 0 && s.report.lx == 128;
}



void status() {
  if (!authorized()) return;
  String json;
  json.reserve(1800);
  json = "{\"firmware\":\"dual-1.3.0\",\"mode\":\"dual\",\"usb_interfaces\":2,\"mode_switch_supported\":false";
  json += ",\"boot_id\":" + String(bootId);
  json += ",\"usb_mounted\":"; json += dualUsb::mounted() ? "true" : "false";
  json += ",\"usb_suspended\":"; json += tud_suspended() ? "true" : "false";
  json += ",\"input_ready\":"; json += dualUsb::inputReady(dualUsb::Keyboard) ? "true" : "false";
  json += ",\"hid_ready\":"; json += dualUsb::ready(dualUsb::Keyboard) ? "true" : "false";
  json += ",\"busy\":"; json += keyboardMode::busy ? "true" : "false";
  json += ",\"release_pending\":"; json += keyboardMode::releasePending ? "true" : "false";
  json += ",\"sent\":" + String(keyboardMode::sent) + ",\"total\":" + String(keyboardMode::total);
  json += ",\"job\":\"" + keyboardMode::jobId + "\",\"error\":\"" + keyboardMode::lastError + "\"";
  json += ",\"leds\":" + String(dualUsb::keyboardLeds);
  json += ",\"keyboard_usb\":" + dualUsb::diagnostic(dualUsb::Keyboard);
  json += ",\"gamepad_usb\":" + dualUsb::diagnostic(dualUsb::Pad);
  json += ",\"gamepad\":" + padStatusJson();
  json += ",\"wake\":" + bleWake::statusJson();
  json += ",\"connection_control\":{\"supported\":true,\"usb_enabled\":";
  json += dualUsb::enabled() ? "true" : "false";
  json += "}";
  json += ",\"free_heap\":" + String(ESP.getFreeHeap());
  json += ",\"minimum_free_heap\":" + String(ESP.getMinFreeHeap());
  json += ",\"ip\":\"" + WiFi.softAPIP().toString() + "\",\"station_connected\":";
  json += WiFi.status() == WL_CONNECTED ? "true" : "false";
  json += ",\"station_ip\":\"" + WiFi.localIP().toString() + "\"}";
  server.send(200,"application/json",json);
}

void stopInputs(const char *reason) {
  portENTER_CRITICAL(&stateLock); padStream.stop(); state.cancel(reason); portEXIT_CRITICAL(&stateLock);
  keyboardMode::stopJob(reason);
}

void disconnectHost() {
  if (!authorized()) return;
  hostDisconnecting = true;
  stopInputs("host_disconnected");
  const bool bleStopped = bleWake::stop();
  // Give an awake host a bounded opportunity to receive neutral reports first.
  const uint32_t started = millis();
  while (dualUsb::mounted() && !tud_suspended() &&
         (keyboardMode::releasePending || snapshot().releasePending) &&
         uint32_t(millis() - started) < 250) {
    keyboardMode::tickKeyboard();
    delay(1);
  }
  const bool detached = dualUsb::setEnabled(false);
  hostDisconnecting = false;
  if (!detached) { fail(503,"usb_disconnect_failed"); return; }
  if (!bleStopped) { fail(503,"ble_stop_failed"); return; }
  server.send(200,"application/json","{\"usb_enabled\":false,\"bluetooth_stopped\":true,\"wifi_kept\":true}");
}

void reconnectHost() {
  if (!authorized()) return;
  if (!dualUsb::enabled()) {
    stopInputs("host_reconnected");
    if (!dualUsb::setEnabled(true)) { fail(503,"usb_connect_failed"); return; }
  }
  server.send(200,"application/json","{\"usb_enabled\":true}");
}

void setup() {
  logicSelfTest = selfTest(); state.epoch = esp_random(); bootId = esp_random();
  keyboardMode::keyboard = new keyboardMode::BootKeyboard();
  logicSelfTest = dualUsb::begin(false) && logicSelfTest;
  USB.VID(0x0f0d); USB.PID(0x00c1);
  USB.productName("Switch2 Dual Keyboard Gamepad");
  USB.manufacturerName("Personal HID Project");
  USB.serialNumber("S2DUAL-44BD8DFBFD3C");
  USB.usbClass(0); USB.usbSubClass(0); USB.usbProtocol(0); USB.usbPower(100);
  logicSelfTest = USB.begin() && logicSelfTest;
  if (xTaskCreatePinnedToCore(usbTask,"pad-usb",4096,nullptr,2,nullptr,1) != pdPASS) logicSelfTest = false;
  WiFi.mode(WIFI_AP_STA); WiFi.softAP(AP_SSID,AP_PASSWORD,6,false,1);
  preferences.begin("s2keyboard",true);
  const String ssid = preferences.getString("ssid","");
  if (ssid.length()) {
    WiFi.setAutoReconnect(true);
    WiFi.begin(ssid.c_str(),preferences.getString("password","").c_str());
  }
  preferences.end(); esp_wifi_set_ps(WIFI_PS_NONE);
  bleWake::begin();
  const char *headers[] = {"X-Key","X-Job"}; server.collectHeaders(headers,2);
  server.on("/",HTTP_GET,[]{server.send(200,"text/plain","Switch2 USB Keyboard, Gamepad and BLE Wake. Use remote.py.");});
  server.on("/status",HTTP_GET,status);
  server.on("/disconnect",HTTP_POST,disconnectHost);
  server.on("/connect",HTTP_POST,reconnectHost);
  server.on("/switch2/wakeup",HTTP_POST,[]{
    if (!authorized()) return;
    if (!dualUsb::enabled()) { fail(409,"host_disconnected"); return; }
    const char *error = bleWake::start();
    if (error) { fail(!strcmp(error,"wake_busy") ? 409 : 503,error); return; }
    server.send(202,"application/json","{\"accepted\":true,\"duration_ms\":1000}");
  });
  server.on("/pad",HTTP_POST,pulse);
  server.on("/pad/stream",HTTP_POST,streamInput);
  server.on("/text",HTTP_POST,[]{keyboardMode::prepareJob(false);});
  server.on("/tap",HTTP_POST,[]{keyboardMode::prepareJob(true);});
  server.on("/cancel",HTTP_POST,[]{
    if (!authorized()) return;
    stopInputs("cancelled");
    if (!bleWake::stop()) { fail(503,"ble_stop_failed"); return; }
    server.send(200,"application/json","{\"cancelled\":true}");
  });
  server.onNotFound([]{fail(404,"not_found");});server.begin();
}
void loop() {
  dualUsb::observeLink(); server.handleClient();
  keyboardMode::tickKeyboard(); dualUsb::tickKeyboardIdle(); delay(1);
}
