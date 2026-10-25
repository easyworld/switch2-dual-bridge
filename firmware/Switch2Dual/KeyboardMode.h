#pragma once
namespace keyboardMode {
// A single boot keyboard interface, eight-byte reports, no report IDs or CDC.
class BootKeyboard {
public:
  struct Endpoint { bool ready() { return dualUsb::ready(dualUsb::Keyboard); } } hid;
  volatile uint8_t &leds = dualUsb::keyboardLeds;
  bool send(Stroke s) {
    const uint8_t report[8] = {s.modifier,0,s.key,0,0,0,0,0};
    return dualUsb::send(dualUsb::Keyboard,report,sizeof(report),40);
  }
};
BootKeyboard *keyboard = nullptr;

String text, jobId, lastError;
bool busy = false, jis = false, tapJob = false, releasePending = true;
Stroke tap = {};
size_t sent = 0, total = 0;
uint8_t phase = 0;
uint32_t nextAt = 0;
unsigned gapMs = 12, lineMs = 80;

bool authorized() {
  if (server.header("X-Key") == API_KEY) return true;
  server.send(401,"application/json","{\"error\":\"authentication_required\"}"); return false;
}
void fail(int code, const char *message) {
  server.send(code,"application/json",String("{\"error\":\"")+message+"\"}");
}
void stopJob(const char *reason) {
  busy = false; text = ""; lastError = reason;
  releasePending = true; phase = 0;
}
void status() {
  if (!authorized()) return;
  String json = "{\"firmware\":\"2.0.0\",\"mode\":\"keyboard\",\"usb_mounted\":";
  json += tud_mounted() ? "true" : "false";
  json += ",\"hid_ready\":"; json += keyboard->hid.ready() ? "true" : "false";
  json += ",\"mode_switch_supported\":true,\"boot_id\":" + String(bootId);
  json += ",\"switch_pending\":"; json += switchPending ? "true" : "false";
  json += ",\"usb_suspended\":"; json += tud_suspended() ? "true" : "false";
  json += ",\"busy\":"; json += busy ? "true" : "false";
  json += ",\"release_pending\":"; json += releasePending ? "true" : "false";
  json += ",\"sent\":" + String(sent) + ",\"total\":" + String(total);
  json += ",\"job\":\""+jobId+"\",\"error\":\""+lastError+"\"";
  json += ",\"leds\":"+String(keyboard->leds)+",\"ip\":\""+WiFi.softAPIP().toString()+"\"";
  json += ",\"station_connected\":"; json += WiFi.status()==WL_CONNECTED ? "true" : "false";
  json += ",\"station_ip\":\""+WiFi.localIP().toString()+"\"}";
  server.send(200,"application/json",json);
}
bool prepareJob(bool isTap) {
  if (!authorized()) return false;
  if (hostDisconnecting || !dualUsb::enabled()) { fail(409,"host_disconnected"); return false; }
  if (!dualUsb::inputReady(dualUsb::Keyboard)) { fail(409,"usb_connecting"); return false; }
  if (busy || releasePending) { fail(409,"busy_or_releasing"); return false; }
  if (!tud_mounted() || tud_suspended()) { fail(409,"usb_not_ready"); return false; }
  String id = server.header("X-Job");
  if (!id.length() || id.length()>64) { fail(400,"job_id_required"); return false; }
  for (unsigned i=0; i<id.length(); ++i) if (!isAlphaNumeric(id[i]) && id[i]!='-') {
    fail(400,"invalid_job_id"); return false;
  }
  if (id == jobId) { fail(409,"duplicate_job_id"); return false; }
  unsigned gap = server.arg("gap").toInt(), line = server.arg("line").toInt();
  if (gap < 5 || gap > 100 || line > 2000) { fail(400,"invalid_timing"); return false; }
  bool useJis = server.arg("layout") == "jis";
  if (!isTap) {
    const String body = server.arg("plain");
    if (!body.length() || body.length()>16384) { fail(400,"text_size_1_to_16384"); return false; }
    for (unsigned i=0; i<body.length(); ++i) {
      Stroke s;
      if (!mapCharacter(uint8_t(body[i]), useJis, s)) { fail(400,"unsupported_character_ascii_only"); return false; }
    }
    text = body;
  } else {
    int key = server.arg("key").toInt(), mod = server.arg("mod").toInt();
    if (key<4 || key>0xe7 || mod<0 || mod>255) { fail(400,"invalid_key"); return false; }
    tap = {uint8_t(mod),uint8_t(key)};
  }
  jis = useJis; gapMs = gap; lineMs = line; tapJob = isTap;
  total = isTap ? 1 : text.length(); sent = 0; phase = 0; nextAt = millis();
  jobId = id; lastError = ""; busy = true;
  server.send(202,"application/json","{\"accepted\":true,\"job\":\""+id+"\"}");
  return true;
}
void tickKeyboard() {
  const uint32_t now = millis();
  if (releasePending) {
    if (keyboard->hid.ready() && keyboard->send({0,0})) releasePending = false;
    return;
  }
  if (!busy) return;
  if (!tud_mounted() || tud_suspended()) { stopJob("usb_disconnected_or_suspended"); return; }
  if (int32_t(now-nextAt)<0) return;
  if (!keyboard->hid.ready()) {
    if (uint32_t(now-nextAt)>1000) stopJob("usb_endpoint_timeout");
    return;
  }
  if (phase == 0) {
    Stroke s = tap;
    if (!tapJob) mapCharacter(uint8_t(text[sent]), jis, s);
    if (!keyboard->send(s)) { stopJob("usb_press_failed"); return; }
    phase = 1; nextAt = millis()+gapMs;
  } else {
    if (!keyboard->send({0,0})) { stopJob("usb_release_failed"); return; }
    bool newline = !tapJob && text[sent]=='\n';
    ++sent; phase = 0; nextAt = millis()+gapMs+(newline?lineMs:0);
    if (sent==total) { busy=false; text=""; }
  }
}

}
