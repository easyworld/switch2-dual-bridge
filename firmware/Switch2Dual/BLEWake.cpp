#include "BLEWake.h"
#include "WakeConfig.h"
#include <BLEDevice.h>
#include <BLEAdvertising.h>
#include <esp_mac.h>
#include <atomic>

#if !defined(CONFIG_NIMBLE_ENABLED)
#error "BLE wake requires the NimBLE backend from Arduino ESP32 core 3.3.7"
#endif

namespace bleWake {
namespace {
enum class Init { Pending, Ready, Failed };
std::atomic<Init> initialization{Init::Pending};
BLEAdvertising *advertising = nullptr;
const char *initError = "";
const char *lastError = "";
String actualAddress;
uint32_t sentCount = 0;

void initialize(void *) {
  // Publish pointers, address and errors only after initialization completes.
  // BLEDevice::init waits for host sync; keep that wait off the USB/HTTP loop.
  if (esp_iface_mac_addr_set(wakeConfig::address, ESP_MAC_BT) != ESP_OK) {
    initError = "ble_mac_failed";
  } else if (!BLEDevice::init("Switch2 Wake")) {
    initError = "ble_init_failed";
  } else {
    char expected[18];
    snprintf(expected, sizeof(expected), "%02x:%02x:%02x:%02x:%02x:%02x",
             wakeConfig::address[0], wakeConfig::address[1], wakeConfig::address[2],
             wakeConfig::address[3], wakeConfig::address[4], wakeConfig::address[5]);
    actualAddress = BLEDevice::getAddress().toString();
    if (actualAddress != expected) {
      initError = "ble_mac_mismatch";
    } else {
      advertising = BLEDevice::getAdvertising();
      advertising->setMinInterval(0x0020);
      advertising->setMaxInterval(0x0040);
      // Preserve the reference firmware's NimBLE advertising mode and raw data.
      advertising->setAdvertisementType(0);
      BLEAdvertisementData data;
      data.addData(String(reinterpret_cast<const char *>(wakeConfig::advertisement),
                          sizeof(wakeConfig::advertisement)));
      if (!advertising->setAdvertisementData(data)) initError = "ble_data_failed";
    }
  }
  initialization.store(*initError ? Init::Failed : Init::Ready, std::memory_order_release);
  vTaskDelete(nullptr);
}
}

void begin() {
  if (xTaskCreatePinnedToCore(initialize, "ble-wake-init", 6144, nullptr, 1, nullptr, 0) != pdPASS) {
    initError = "ble_task_failed";
    initialization.store(Init::Failed, std::memory_order_release);
  }
}

const char *start() {
  const Init init = initialization.load(std::memory_order_acquire);
  if (init == Init::Pending) return "ble_initializing";
  if (init == Init::Failed) return initError;
  if (advertising->isAdvertising()) return "wake_busy";
  // NimBLE duration is in seconds and expires inside the stack, independently
  // of HTTP requests and keyboard input. Never retry an unconfirmed broadcast.
  if (!advertising->start(1)) return lastError = "ble_start_failed";
  lastError = "";
  ++sentCount;
  return nullptr;
}

bool stop() {
  if (initialization.load(std::memory_order_acquire) != Init::Ready) return true;
  if (!advertising->isAdvertising()) return true;
  if (!advertising->stop()) {
    lastError = "ble_stop_failed";
    return false;
  }
  return true;
}

String statusJson() {
  const Init init = initialization.load(std::memory_order_acquire);
  const bool ready = init == Init::Ready;
  String json = "{\"supported\":true,\"ready\":";
  json += ready ? "true" : "false";
  json += ",\"initializing\":"; json += init == Init::Pending ? "true" : "false";
  json += ",\"advertising\":"; json += ready && advertising->isAdvertising() ? "true" : "false";
  json += ",\"duration_ms\":1000,\"sent_count\":" + String(sentCount);
  json += ",\"address\":\"";
  if (init != Init::Pending) json += actualAddress;
  json += "\",\"error\":\"";
  json += init == Init::Failed ? initError : lastError;
  json += "\"}";
  return json;
}
}
