/*
  Blind-spot helmet firmware — Arduino Nano / Nano Every
  Library: Adafruit NeoPixel (Library Manager -> "Adafruit NeoPixel")

  Wiring (vibration modules have 3 pins: IN/SIG, VCC, GND)
    Left motor module   IN -> D5    VCC -> 5V   GND -> GND
    Right motor module  IN -> D6    VCC -> 5V   GND -> GND
    NeoPixel stick      DIN -> D2 (through 330 ohm if you have one)  5V -> 5V  GND -> GND
    Optional button     D3 -> button -> GND   (press = "what's behind me?")

  Serial 57600 baud, one command per line:
    L<n> R<n> B<n>  haptic on left / right / both: 0 stop, 1 gentle, 2 medium, 3 strong, 4 fault
    M<n>            rear light: 0 normal flash, 1 alert, 2 danger. Host sends this every 250 ms (heartbeat)
    F1 / F0         host reports a fault (camera) / clears it
    X               everything off (bench testing)
    ?               status
  Board -> host: READY, BTN, FAILSAFE, LINK OK, ERR <line>

  Failsafe: no command for 1.5 s -> light returns to a normal flashing bike light
  (fail-visible) and both motors give one long "system down" pattern.
*/
#include <Adafruit_NeoPixel.h>

// ------------------------------------------------------------------ pins & settings
const uint8_t PIN_MOTOR_L = 5;
const uint8_t PIN_MOTOR_R = 6;
const uint8_t PIN_PIXELS  = 2;
const uint8_t PIN_BUTTON  = 3;
const uint8_t PIN_STATUS  = LED_BUILTIN;   // mirrors the rear light: test without the strip
const uint8_t NUM_PIXELS  = 8;
const bool    MOTOR_ACTIVE_HIGH = true;    // set false if your module turns ON with a LOW input
const unsigned long LINK_TIMEOUT_MS = 1500;
const unsigned long BOOT_WARN_MS    = 6000; // no host by then -> one warning buzz

// Rear light patterns. 5 Hz reads as urgent without being a high-frequency strobe
// (keep it modest in a crowded demo room).
const uint16_t PERIOD_MS[3] = {1000, 400, 200};
const uint16_t ON_MS[3]     = { 500, 200, 100};
const uint8_t  RED[3]       = { 110, 190, 255};   // 8 LEDs x red only stays well inside USB power

Adafruit_NeoPixel strip(NUM_PIXELS, PIN_PIXELS, NEO_GRB + NEO_KHZ800);

// ------------------------------------------------------------------ haptic patterns
struct Step { uint8_t duty; uint16_t ms; };        // {0,0} terminates a pattern
const Step PAT_GENTLE[] = {{170, 120}, {0, 0}};
const Step PAT_MEDIUM[] = {{220, 150}, {0, 110}, {220, 150}, {0, 0}};
const Step PAT_STRONG[] = {{255, 220}, {0, 80}, {255, 220}, {0, 80}, {255, 260}, {0, 0}};
const Step PAT_FAULT[]  = {{200, 500}, {0, 200}, {200, 500}, {0, 0}};
const Step PAT_HELLO_L[] = {{190, 180}, {0, 0}};
const Step PAT_HELLO_R[] = {{0, 400}, {190, 180}, {0, 0}};   // leading pause: left first, then right

struct Motor {
  uint8_t pin;
  const Step* pat;
  uint8_t idx;
  uint8_t level;
  unsigned long stepStart;
};
Motor motors[2] = {{PIN_MOTOR_L, nullptr, 0, 0, 0}, {PIN_MOTOR_R, nullptr, 0, 0, 0}};

// ------------------------------------------------------------------ state
uint8_t lightMode = 0;
bool failsafe = false;
bool everConnected = false;
bool bootWarned = false;
bool hostFault = false;
unsigned long lastCmdMs = 0;
bool lastLightOn = false;
int8_t lastLightMode = -1;
char lineBuf[16];
uint8_t lineLen = 0;
bool lastButton = HIGH;
unsigned long buttonChangeMs = 0;

// ------------------------------------------------------------------ motors
void motorWrite(uint8_t pin, uint8_t duty) {
  analogWrite(pin, MOTOR_ACTIVE_HIGH ? duty : 255 - duty);
}

const Step* patternFor(uint8_t level) {
  switch (level) {
    case 1: return PAT_GENTLE;
    case 2: return PAT_MEDIUM;
    case 3: return PAT_STRONG;
    case 4: return PAT_FAULT;
    default: return nullptr;
  }
}

void startPattern(Motor& m, const Step* p, uint8_t level) {
  // A weaker pattern never cuts off a stronger one that is still playing.
  if (p != nullptr && m.pat != nullptr && level < m.level) return;
  m.pat = p;
  m.idx = 0;
  m.level = level;
  m.stepStart = millis();
  motorWrite(m.pin, p ? p[0].duty : 0);
}

void updateMotor(Motor& m, unsigned long now) {
  if (m.pat == nullptr) return;
  if (now - m.stepStart < m.pat[m.idx].ms) return;
  m.idx++;
  if (m.pat[m.idx].ms == 0) {                 // end of pattern
    m.pat = nullptr;
    m.level = 0;
    motorWrite(m.pin, 0);
    return;
  }
  m.stepStart = now;
  motorWrite(m.pin, m.pat[m.idx].duty);
}

bool playOn(uint8_t which, int level) {
  if (level < 0 || level > 4) return false;
  if (level == 0) {
    motors[which].pat = nullptr;
    motors[which].level = 0;
    motorWrite(motors[which].pin, 0);
    return true;
  }
  startPattern(motors[which], patternFor(level), level);
  return true;
}

// ------------------------------------------------------------------ light
void renderLight(unsigned long now) {
  uint8_t mode = failsafe ? 0 : lightMode;
  bool on = (now % PERIOD_MS[mode]) < ON_MS[mode];
  if (on == lastLightOn && (int8_t)mode == lastLightMode) return;   // only redraw on change
  lastLightOn = on;
  lastLightMode = mode;
  uint32_t color = on ? strip.Color(RED[mode], 0, 0) : 0;
  for (uint8_t i = 0; i < NUM_PIXELS; i++) strip.setPixelColor(i, color);
  strip.show();
  digitalWrite(PIN_STATUS, on ? HIGH : LOW);
}

// ------------------------------------------------------------------ serial
void handleLine(const char* s, unsigned long now) {
  char cmd = s[0];
  int arg = (s[1] >= '0' && s[1] <= '9') ? s[1] - '0' : -1;
  bool ok = true;
  switch (cmd) {
    case 'L': ok = playOn(0, arg); break;
    case 'R': ok = playOn(1, arg); break;
    case 'B': ok = playOn(0, arg); if (ok) playOn(1, arg); break;
    case 'M': if (arg >= 0 && arg <= 2) lightMode = arg; else ok = false; break;
    case 'F':
      if (arg == 1 && !hostFault) { playOn(0, 4); playOn(1, 4); }
      if (arg == 0 || arg == 1) hostFault = (arg == 1); else ok = false;
      break;
    case 'X': playOn(0, 0); playOn(1, 0); lightMode = 0; break;
    case '?':
      Serial.print(F("STATUS mode=")); Serial.print(lightMode);
      Serial.print(F(" failsafe=")); Serial.print(failsafe);
      Serial.print(F(" hostFault=")); Serial.println(hostFault);
      break;
    default: ok = false;
  }
  if (ok) {
    lastCmdMs = now;
    everConnected = true;
    if (failsafe) { failsafe = false; Serial.println(F("LINK OK")); }
  } else {
    Serial.print(F("ERR ")); Serial.println(s);
  }
}

void pollSerial(unsigned long now) {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      lineBuf[lineLen] = '\0';
      if (lineLen > 0) handleLine(lineBuf, now);
      lineLen = 0;
    } else if (lineLen < sizeof(lineBuf) - 1) {
      lineBuf[lineLen++] = c;
    } else {
      lineLen = 0;                               // overlong garbage: drop the line
    }
  }
}

// ------------------------------------------------------------------ button
void pollButton(unsigned long now) {
  bool b = digitalRead(PIN_BUTTON);
  if (b != lastButton && now - buttonChangeMs > 30) {   // 30 ms debounce
    buttonChangeMs = now;
    lastButton = b;
    if (b == LOW) Serial.println(F("BTN"));
  }
}

// ------------------------------------------------------------------ main
void setup() {
  pinMode(PIN_MOTOR_L, OUTPUT);
  pinMode(PIN_MOTOR_R, OUTPUT);
  pinMode(PIN_STATUS, OUTPUT);
  pinMode(PIN_BUTTON, INPUT_PULLUP);
  motorWrite(PIN_MOTOR_L, 0);
  motorWrite(PIN_MOTOR_R, 0);
  strip.begin();
  strip.setBrightness(255);     // brightness is set per mode via RED[]
  strip.show();
  Serial.begin(57600);
  // Power-on self-test: you should feel LEFT, then RIGHT. If reversed, swap the IN wires.
  startPattern(motors[0], PAT_HELLO_L, 1);
  startPattern(motors[1], PAT_HELLO_R, 1);
  Serial.println(F("READY"));
}

void loop() {
  unsigned long now = millis();
  pollSerial(now);
  pollButton(now);

  if (everConnected && !failsafe && now - lastCmdMs > LINK_TIMEOUT_MS) {
    failsafe = true;                          // laptop/app died: be a normal bike light
    lightMode = 0;
    playOn(0, 4);
    playOn(1, 4);
    Serial.println(F("FAILSAFE"));
  }
  if (!everConnected && !bootWarned && now > BOOT_WARN_MS) {
    bootWarned = true;                        // helmet on, but the app never started
    playOn(0, 4);
    playOn(1, 4);
  }

  updateMotor(motors[0], now);
  updateMotor(motors[1], now);
  renderLight(now);
}
