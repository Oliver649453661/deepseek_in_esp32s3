// ESP32 pet v7.1: large shared camera, fixed ground, nonblocking action crossfades.
// Screen wiring: BLK=3V3 CS=10 DC=9 RES=8 SDA=11 SCL=12 VCC=3V3 GND=GND.
#include <Adafruit_GFX.h>
#include <Adafruit_ST7735.h>
#include <TJpg_Decoder.h>
#include <SPI.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <esp_partition.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <freertos/queue.h>
#include <math.h>
#include "secrets.h"
#include "pet_assets.h"

Adafruit_ST7735 tft(10, 9, 8);
GFXcanvas16 canvas(128, 160);
const esp_partition_t* assetPartition = nullptr;
QueueHandle_t balanceUpdates = nullptr;
uint8_t jpegBuffer[PET_MAX_JPEG_BYTES];
uint8_t jpegPrefix[PET_JPEG_PREFIX_BYTES];
uint16_t transitionBuffer[128 * 160];
constexpr unsigned long CHECK_MS = 60000;
constexpr uint8_t LION = 0, HAPPY = 2, ALERT = 4;
constexpr int PET_Y = 12;
constexpr uint8_t TRANSITION_STEPS = 4;
constexpr uint32_t TRANSITION_STEP_MS = 50;

struct PackHeader {
  char magic[8];
  uint32_t version, width, height, actions, frames, length, crc32, quality;
  uint32_t prefixLength, prefixCrc32;
  uint8_t reserved[16];
};
static_assert(sizeof(PackHeader) == 64, "Asset header layout mismatch");

float balance = -1;
uint8_t activeAction = LION;
uint16_t currentFrame = 0, renderedFrames = 0;
uint32_t frameStartedAt = 0, cycleStartedAt = 0, maxRenderMs = 0;
uint32_t decodeErrors = 0;
bool frameNeedsDraw = true, playbackOk = false;
bool hasDisplayedFrame = false, transitionWanted = false, transitionActive = false;
uint8_t transitionStep = 0;
uint32_t transitionStartedAt = 0, maxTransitionStepMs = 0;
int requestedAction = -1;
uint8_t actionBag[PET_ACTION_COUNT], bagRemaining = 0;
bool testPending = false, testMode = false;
uint8_t testIndex = 0, testedActions = 0;
bool buttonRaw = HIGH, buttonStable = HIGH;
uint32_t buttonChangedAt = 0, buttonPressedAt = 0;
char commandBuffer[16];
uint8_t commandLength = 0;

constexpr uint16_t rgb565(uint8_t r, uint8_t g, uint8_t b) {
  return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3);
}
constexpr uint16_t BALANCE_NORMAL = rgb565(0xB8, 0xB8, 0xC0);
constexpr uint16_t BALANCE_LOW = rgb565(0xC8, 0xAD, 0x88);
constexpr uint16_t BALANCE_CRITICAL = rgb565(0xD4, 0x8C, 0x92);

uint32_t crc32(const uint8_t* data, size_t length);
bool drawJpegBlock(int16_t x, int16_t y, uint16_t w, uint16_t h, uint16_t* pixels);
bool loadAssets();
bool renderFrame();
void drawBalance();
void advanceTransition();
void startAction(uint8_t index);
void requestAction(uint8_t index);
void advanceAnimation();
void fillBag(uint8_t skip);
uint8_t pickNext();
void handleButton();
void handleSerial();
void printStatus();
void balanceTask(void* parameter);
void checkBalance();

void setup() {
  Serial.begin(115200);
  Serial.println("\n=== PET v7.1: fixed large camera / portrait 128x148 / smooth transitions ===");
  pinMode(0, INPUT_PULLUP);
  randomSeed(micros());
  SPI.begin(12, -1, 11, 10);
  tft.initR(INITR_BLACKTAB);
  tft.setRotation(0);
  tft.setSPISpeed(40000000);
  canvas.fillScreen(ST77XX_BLACK);
  canvas.setTextSize(1);
  canvas.setTextColor(BALANCE_NORMAL);
  canvas.setCursor(31, 76);
  canvas.print("waking up...");
  tft.drawRGBBitmap(0, 0, canvas.getBuffer(), 128, 160);
  TJpgDec.setJpgScale(1);
  TJpgDec.setSwapBytes(false); // GFXcanvas16 expects native RGB565, not swapped SPI bytes.
  TJpgDec.setCallback(drawJpegBlock);
  playbackOk = loadAssets();
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  balanceUpdates = xQueueCreate(1, sizeof(float));
  if (!balanceUpdates || xTaskCreatePinnedToCore(balanceTask, "balance", 12288,
                                                nullptr, 1, nullptr, 0) != pdPASS) {
    Serial.println("[balance] worker could not start");
  }
  fillBag(LION);
  if (playbackOk) startAction(LION);
  else {
    canvas.fillScreen(ST77XX_BLACK);
    canvas.setCursor(10, 70);
    canvas.print("assets not ready");
    tft.drawRGBBitmap(0, 0, canvas.getBuffer(), 128, 160);
  }
}

void loop() {
  handleButton();
  handleSerial();
  float nextBalance;
  if (balanceUpdates && xQueueReceive(balanceUpdates, &nextBalance, 0) == pdTRUE) {
    if (balance >= 0 && requestedAction < 0 && !testMode && !testPending) {
      if (nextBalance > balance + 0.01f) requestAction(HAPPY);
      else if (nextBalance < balance - 0.01f) requestAction(ALERT);
    }
    balance = nextBalance;
    Serial.println("[balance] updated");
  }
  if (playbackOk) {
    if (transitionActive) advanceTransition();
    else {
      advanceAnimation();
      if (frameNeedsDraw) playbackOk = renderFrame();
    }
  }
  delay(1);
}

uint32_t crc32(const uint8_t* data, size_t length) {
  uint32_t crc = 0xFFFFFFFFU;
  for (size_t i = 0; i < length; i++) {
    crc ^= data[i];
    for (int bit = 0; bit < 8; bit++) crc = (crc >> 1) ^ (0xEDB88320U & (0U - (crc & 1U)));
  }
  return ~crc;
}

bool loadAssets() {
  assetPartition = esp_partition_find_first(ESP_PARTITION_TYPE_DATA,
                                            static_cast<esp_partition_subtype_t>(0x40), "pet_assets");
  PackHeader header{};
  if (!assetPartition || PET_PACK_BYTES > assetPartition->size ||
      esp_partition_read(assetPartition, 0, &header, sizeof(header)) != ESP_OK ||
      memcmp(header.magic, "PETMJPG8", 8) != 0 || header.version != 8 ||
      header.width != PET_FRAME_WIDTH || header.height != PET_FRAME_HEIGHT ||
      header.actions != PET_ACTION_COUNT || header.frames != PET_FRAME_COUNT ||
      header.length != PET_PACK_BYTES || header.crc32 != PET_PACK_CRC32 ||
      header.prefixLength != PET_JPEG_PREFIX_BYTES || header.prefixCrc32 != PET_JPEG_PREFIX_CRC32) {
    Serial.println("[error] asset pack missing or does not match this firmware");
    return false;
  }
  if (esp_partition_read(assetPartition, sizeof(PackHeader), jpegPrefix, sizeof(jpegPrefix)) != ESP_OK ||
      crc32(jpegPrefix, sizeof(jpegPrefix)) != PET_JPEG_PREFIX_CRC32) {
    Serial.println("[error] shared JPEG prefix failed CRC verification");
    return false;
  }
  Serial.printf("[assets] actions=%u frames=%u bytes=%u quality=%lu\n", PET_ACTION_COUNT,
                PET_FRAME_COUNT, PET_PACK_BYTES, static_cast<unsigned long>(header.quality));
  return true;
}

bool drawJpegBlock(int16_t x, int16_t y, uint16_t w, uint16_t h, uint16_t* pixels) {
  if (x < 0 || y < PET_Y || x + w > 128 || y + h > 160) return false;
  canvas.drawRGBBitmap(x, y, pixels, w, h);
  return true;
}

bool renderFrame() {
  uint32_t began = millis();
  const PetAction& action = petActions[activeAction];
  const PetFrame& frame = petFrames[action.firstFrame + currentFrame];
  uint32_t jpegLength = sizeof(jpegPrefix) + frame.length;
  memcpy(jpegBuffer, jpegPrefix, sizeof(jpegPrefix));
  if (jpegLength > sizeof(jpegBuffer) || frame.offset < sizeof(PackHeader) + sizeof(jpegPrefix) ||
      frame.offset + frame.length > PET_PACK_BYTES ||
      esp_partition_read(assetPartition, frame.offset, jpegBuffer + sizeof(jpegPrefix), frame.length) != ESP_OK ||
      crc32(jpegBuffer, jpegLength) != frame.crc32) {
    decodeErrors++;
    Serial.printf("[error] frame read/CRC action=%s frame=%u\n", action.id, currentFrame);
    return false;
  }
  bool blendFirstFrame = currentFrame == 0 && transitionWanted;
  if (blendFirstFrame) memcpy(transitionBuffer, canvas.getBuffer(), sizeof(transitionBuffer));
  canvas.fillScreen(ST77XX_BLACK);
  JRESULT result = TJpgDec.drawJpg(0, PET_Y, jpegBuffer, jpegLength);
  if (result != JDR_OK) {
    decodeErrors++;
    Serial.printf("[error] JPEG action=%s frame=%u result=%d\n", action.id, currentFrame, result);
    return false;
  }
  drawBalance();
  if (blendFirstFrame) {
    transitionActive = true;
    transitionStep = 0;
    transitionStartedAt = millis();
    maxTransitionStepMs = 0;
  } else {
    tft.drawRGBBitmap(0, 0, canvas.getBuffer(), 128, 160);
    renderedFrames++;
    hasDisplayedFrame = true;
  }
  frameNeedsDraw = false;
  uint32_t spent = millis() - began;
  if (spent > maxRenderMs) maxRenderMs = spent;
  return true;
}

void advanceTransition() {
  if (millis() - transitionStartedAt < transitionStep * TRANSITION_STEP_MS) return;
  uint32_t began = millis();
  uint16_t* target = canvas.getBuffer();
  uint8_t remaining = TRANSITION_STEPS - transitionStep;
  // The previous image is progressively blended toward the new image. One extra
  // framebuffer is enough; the target canvas stays untouched. The last step is exact.
  if (remaining == 1) memcpy(transitionBuffer, target, sizeof(transitionBuffer));
  else {
    for (size_t i = 128 * PET_Y; i < 128 * 160; i++) {
      uint16_t a = transitionBuffer[i], b = target[i];
      uint16_t r = ((a >> 11) * (remaining - 1) + (b >> 11) + remaining / 2) / remaining;
      uint16_t g = (((a >> 5) & 63) * (remaining - 1) + ((b >> 5) & 63) + remaining / 2) / remaining;
      uint16_t bl = ((a & 31) * (remaining - 1) + (b & 31) + remaining / 2) / remaining;
      transitionBuffer[i] = (r << 11) | (g << 5) | bl;
    }
  }
  // Balance text never crossfades or ghosts, and can still refresh during a transition.
  canvas.fillRect(0, 0, 128, PET_Y, ST77XX_BLACK);
  drawBalance();
  memcpy(transitionBuffer, target, 128 * PET_Y * sizeof(uint16_t));
  tft.drawRGBBitmap(0, 0, transitionBuffer, 128, 160);
  uint32_t spent = millis() - began;
  if (spent > maxTransitionStepMs) maxTransitionStepMs = spent;
  if (++transitionStep == TRANSITION_STEPS) {
    transitionActive = transitionWanted = false;
    renderedFrames = 1;
    hasDisplayedFrame = true;
    // A transition is extra time: never consume the first frame or shorten the new action.
    frameStartedAt = cycleStartedAt = millis();
    Serial.printf("[transition] index=%u steps=%u elapsed=%lu max_step=%lu\n", activeAction, transitionStep,
                  static_cast<unsigned long>(millis() - transitionStartedAt), static_cast<unsigned long>(maxTransitionStepMs));
  }
}

void drawBalance() {
  if (balance < 0) return;
  uint16_t color = BALANCE_NORMAL;
  if (balance < 3) color = BALANCE_CRITICAL;
  else if (balance < 10) color = BALANCE_LOW;
  canvas.setTextSize(1);
  canvas.setTextColor(color);
  canvas.setCursor(4, 4);
  canvas.print(balance, 1);
}

void startAction(uint8_t index) {
  activeAction = index;
  currentFrame = renderedFrames = 0;
  frameStartedAt = cycleStartedAt = millis();
  maxRenderMs = 0;
  transitionWanted = hasDisplayedFrame;
  frameNeedsDraw = true;
  Serial.printf("[action] index=%u id=%s frames=%u duration=%lu\n", index, petActions[index].id,
                petActions[index].frameCount, static_cast<unsigned long>(petActions[index].durationMs));
}

void requestAction(uint8_t index) {
  if (index >= PET_ACTION_COUNT) return;
  requestedAction = index;
  Serial.printf("[queued] %s after current action\n", petActions[index].id);
}

void fillBag(uint8_t skip) {
  bagRemaining = 0;
  for (uint8_t i = 0; i < PET_ACTION_COUNT; i++) if (i != skip) actionBag[bagRemaining++] = i;
  for (int i = bagRemaining - 1; i > 0; i--) {
    int j = random(i + 1);
    uint8_t swap = actionBag[i]; actionBag[i] = actionBag[j]; actionBag[j] = swap;
  }
}

uint8_t pickNext() {
  if (!bagRemaining) fillBag(255);
  if (bagRemaining > 1 && actionBag[bagRemaining - 1] == activeAction) {
    uint8_t swap = actionBag[0]; actionBag[0] = actionBag[bagRemaining - 1]; actionBag[bagRemaining - 1] = swap;
  }
  return actionBag[--bagRemaining];
}

void advanceAnimation() {
  uint32_t now = millis();
  const PetAction& action = petActions[activeAction];
  if (frameNeedsDraw || now - frameStartedAt < petFrames[action.firstFrame + currentFrame].delayMs) return;
  if (currentFrame + 1 < action.frameCount) {
    currentFrame++;
    frameStartedAt = now;
    frameNeedsDraw = true;
    return;
  }
  Serial.printf("[cycle] index=%u id=%s rendered=%u/%u elapsed=%lu target=%lu max_render=%lu errors=%lu\n",
                activeAction, action.id, renderedFrames, action.frameCount,
                static_cast<unsigned long>(now - cycleStartedAt), static_cast<unsigned long>(action.durationMs),
                static_cast<unsigned long>(maxRenderMs), static_cast<unsigned long>(decodeErrors));
  if (testPending) {
    testPending = false; testMode = true; testIndex = testedActions = 0; requestedAction = -1;
    Serial.printf("[test] begin actions=%u\n", PET_ACTION_COUNT);
    startAction(0);
  } else if (testMode) {
    testedActions++;
    if (++testIndex < PET_ACTION_COUNT) startAction(testIndex);
    else {
      testMode = false;
      Serial.printf("[test] complete actions=%u errors=%lu\n", testedActions, static_cast<unsigned long>(decodeErrors));
      fillBag(LION);
      startAction(LION);
    }
  } else if (requestedAction >= 0) {
    uint8_t next = requestedAction; requestedAction = -1; startAction(next);
  } else startAction(pickNext());
}

void handleButton() {
  bool raw = digitalRead(0);
  uint32_t now = millis();
  if (raw != buttonRaw) { buttonRaw = raw; buttonChangedAt = now; }
  if (raw == buttonStable || now - buttonChangedAt < 25) return;
  buttonStable = raw;
  if (raw == LOW) buttonPressedAt = now;
  else {
    if (testMode || testPending) Serial.println("[test] cancelled by button");
    testMode = testPending = false;
    requestAction(now - buttonPressedAt >= 1000 ? LION : HAPPY);
  }
}

void printStatus() {
  Serial.printf("[status] id=%s frame=%u/%u balance=%s wifi=%d heap=%u errors=%lu\n", petActions[activeAction].id,
                currentFrame + 1, petActions[activeAction].frameCount, balance >= 0 ? "ready" : "pending",
                WiFi.status(), ESP.getFreeHeap(), static_cast<unsigned long>(decodeErrors));
}

void handleSerial() {
  while (Serial.available()) {
    char ch = Serial.read();
    if (ch == '?') printStatus();
    else if (ch == 'T') { testPending = true; Serial.println("[test] queued after current action"); }
    else if (ch == 'X') { testMode = testPending = false; requestedAction = -1; Serial.println("[test] cancelled"); }
    else if (ch == '\n' || ch == '\r') {
      commandBuffer[commandLength] = 0;
      if (commandLength > 1 && commandBuffer[0] == 'P') {
        char* end = nullptr;
        long index = strtol(commandBuffer + 1, &end, 10);
        if (*end == 0 && index >= 0 && index < PET_ACTION_COUNT) {
          testMode = testPending = false;
          requestAction(static_cast<uint8_t>(index));
        }
      }
      commandLength = 0;
    } else if (commandLength < sizeof(commandBuffer) - 1) commandBuffer[commandLength++] = ch;
    else commandLength = 0;
  }
}

void balanceTask(void* parameter) {
  for (int tries = 0; WiFi.status() != WL_CONNECTED && tries < 50; tries++) vTaskDelay(pdMS_TO_TICKS(300));
  while (true) { checkBalance(); vTaskDelay(pdMS_TO_TICKS(CHECK_MS)); }
}

void checkBalance() {
  if (WiFi.status() != WL_CONNECTED) { WiFi.reconnect(); vTaskDelay(pdMS_TO_TICKS(2000)); }
  if (WiFi.status() != WL_CONNECTED) { Serial.println("[balance] waiting for WiFi"); return; }
  WiFiClientSecure client;
  client.setInsecure();
  HTTPClient http;
  http.setConnectTimeout(5000);
  http.setTimeout(5000);
  http.begin(client, "https://api.deepseek.com/user/balance");
  http.addHeader("Authorization", String("Bearer ") + DEEPSEEK_KEY);
  int code = http.GET();
  if (code == 200) {
    JsonDocument doc;
    if (!deserializeJson(doc, http.getString())) {
      JsonVariant value = doc["balance_infos"][0]["total_balance"];
      if (!value.isNull()) {
        float nextBalance = value.as<float>();
        if (isfinite(nextBalance) && nextBalance >= 0) xQueueOverwrite(balanceUpdates, &nextBalance);
      }
    }
  } else Serial.printf("[balance] HTTP result=%d\n", code);
  http.end();
}
