/*
  Blind-spot helmet firmware — Arduino Uno / Nano / Nano Every (same pin numbers)
  Library: Adafruit NeoPixel (Library Manager -> "Adafruit NeoPixel")

  Wiring (see README "Wiring (Arduino Uno + Pi)"). Put 5V and GND on the breadboard rails.
    Left vibration motor   D5 -> 1k -> NPN base (or module IN)   motor between 5V and collector, diode across it
    Right vibration motor  D10 -> same
    Left buzzer            D9  (3-pin module: I/O pin; bare buzzer: through an NPN like the motors)
    Right buzzer           D11
    Ultrasonic SL (left side, pointing out ~5 deg back)   TRIG D6   ECHO D7
    Ultrasonic SR (right side, pointing out ~5 deg back)  TRIG D8   ECHO D12
    Ultrasonic BL (back, left of centre)     TRIG A0   ECHO A1
    Ultrasonic BR (back, right of centre)    TRIG A2   ECHO A3
      (only sensors marked true in SONAR_FITTED are pinged; the rest report -1)
    NeoPixel stick (optional rear light)     DIN D2 (through 330 ohm if you have one)
    Optional button                          D3 -> button -> GND   (press = "what's behind me?")
  The Pi connects with the Uno's USB cable only (power + serial). No other wires between them.

  Serial 57600 baud, one command per line:
    L<n> R<n> B<n>  haptic on left / right / both: 0 stop, 1 gentle, 2 medium, 3 strong, 4 fault
    M<n>            rear light: 0 normal flash, 1 alert, 2 danger. Host sends this every 250 ms (heartbeat)
    F1 / F0         host reports a fault (camera) / clears it
    Z1 / Z0         buzzers on / muted (they follow STRONG and FAULT haptic patterns on the same side)
    X               everything off (bench testing)
    ?               status
  Board -> host: READY, BTN, FAILSAFE, LINK OK, ERR <line>,
                 U <SL> <SR> <BL> <BR>   ultrasonic distances in cm, -1 = no echo / not fitted
                                         (one sensor fitted: ~33 times/s; all four: ~8 times/s)

  Failsafe: no command for 1.5 s -> light returns to a normal flashing bike light
  (fail-visible) and both motors give one long "system down" pattern.
*/
#include <Adafruit_NeoPixel.h>

// ------------------------------------------------------------------ pins & settings
const uint8_t PIN_MOTOR_L = 5;
const uint8_t PIN_MOTOR_R = 10;          // Timer1 PWM: unaffected by tone() for passive buzzers
const uint8_t PIN_PIXELS  = 2;
const uint8_t PIN_BUTTON  = 3;
const uint8_t PIN_STATUS  = LED_BUILTIN;   // mirrors the rear light: test without the strip
const uint8_t PIN_BUZZ_L  = 9;
const uint8_t PIN_BUZZ_R  = 11;
const bool    BUZZER_PASSIVE = false;       // true for bare passive buzzers (need a tone; Uno plays one at a time)
const uint16_t BUZZ_HZ    = 2300;
const uint8_t BUZZ_MIN_LEVEL = 3;           // buzzers join STRONG (3) and FAULT (4) patterns only

// Ultrasonic sensors (HC-SR04), pinged one at a time so they don't hear each other's echoes
const uint8_t NUM_SONAR = 4;
const uint8_t SONAR_TRIG[NUM_SONAR] = {6, 8, A0, A2};    // SL, SR, BL, BR
const uint8_t SONAR_ECHO[NUM_SONAR] = {7, 12, A1, A3};
const bool    SONAR_FITTED[NUM_SONAR] = {true, false, false, false};  // set true as you wire each one
const unsigned long SONAR_TIMEOUT_US = 18000;   // ~3 m round trip; further = no echo
const unsigned long SONAR_GAP_MS     = 30;      // one ping every 30 ms, shared by the fitted sensors
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
  uint8_t buzz;          // buzzer on the same side
  const Step* pat;
  uint8_t idx;
  uint8_t level;
  unsigned long stepStart;
};
Motor motors[2] = {{PIN_MOTOR_L, PIN_BUZZ_L, nullptr, 0, 0, 0}, {PIN_MOTOR_R, PIN_BUZZ_R, nullptr, 0, 0, 0}};

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
bool buzzEnabled = true;
int sonarCm[NUM_SONAR] = {-1, -1, -1, -1};
uint8_t sonarIdx = 0;
unsigned long lastPingMs = 0;

// ------------------------------------------------------------------ motors
void motorWrite(uint8_t pin, uint8_t duty) {
  analogWrite(pin, MOTOR_ACTIVE_HIGH ? duty : 255 - duty);
}

void buzzWrite(uint8_t pin, bool on) {
  if (BUZZER_PASSIVE) {
    if (on) tone(pin, BUZZ_HZ); else noTone(pin);
  } else {
    digitalWrite(pin, on ? HIGH : LOW);
  }
}

// Motor and its same-side buzzer move together; the buzzer only for strong/fault patterns.
void outWrite(Motor& m, uint8_t duty) {
  motorWrite(m.pin, duty);
  buzzWrite(m.buzz, buzzEnabled && duty > 0 && m.level >= BUZZ_MIN_LEVEL);
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
  outWrite(m, p ? p[0].duty : 0);
}

void updateMotor(Motor& m, unsigned long now) {
  if (m.pat == nullptr) return;
  if (now - m.stepStart < m.pat[m.idx].ms) return;
  m.idx++;
  if (m.pat[m.idx].ms == 0) {                 // end of pattern
    m.pat = nullptr;
    m.level = 0;
    outWrite(m, 0);
    return;
  }
  m.stepStart = now;
  outWrite(m, m.pat[m.idx].duty);
}

bool playOn(uint8_t which, int level) {
  if (level < 0 || level > 4) return false;
  if (level == 0) {
    motors[which].pat = nullptr;
    motors[which].level = 0;
    outWrite(motors[which], 0);
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
    case 'Z': if (arg == 0 || arg == 1) buzzEnabled = (arg == 1); else ok = false; break;
    case 'X': playOn(0, 0); playOn(1, 0); lightMode = 0; break;
    case '?':
      Serial.print(F("STATUS mode=")); Serial.print(lightMode);
      Serial.print(F(" failsafe=")); Serial.print(failsafe);
      Serial.print(F(" hostFault=")); Serial.print(hostFault);
      Serial.print(F(" buzz=")); Serial.println(buzzEnabled);
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

// ------------------------------------------------------------------ ultrasonic
// One sensor per call, round-robin. pulseIn blocks for at most SONAR_TIMEOUT_US (18 ms),
// short enough that haptic timing and the serial heartbeat are unaffected.
void pollSonar(unsigned long now) {
  if (now - lastPingMs < SONAR_GAP_MS) return;
  lastPingMs = now;
  uint8_t tries = 0;                              // skip sensors that aren't fitted
  while (!SONAR_FITTED[sonarIdx] && tries++ < NUM_SONAR) sonarIdx = (sonarIdx + 1) % NUM_SONAR;
  if (!SONAR_FITTED[sonarIdx]) return;            // none fitted
  uint8_t i = sonarIdx;
  digitalWrite(SONAR_TRIG[i], LOW);
  delayMicroseconds(2);
  digitalWrite(SONAR_TRIG[i], HIGH);
  delayMicroseconds(10);
  digitalWrite(SONAR_TRIG[i], LOW);
  unsigned long us = pulseIn(SONAR_ECHO[i], HIGH, SONAR_TIMEOUT_US);
  sonarCm[i] = us ? (int)(us / 58) : -1;        // 58 us per cm (sound there and back)
  // report after the last fitted sensor in the round (unfitted ones always read -1)
  uint8_t last = 0;
  for (uint8_t k = 0; k < NUM_SONAR; k++) if (SONAR_FITTED[k]) last = k;
  sonarIdx = (sonarIdx + 1) % NUM_SONAR;
  if (i == last) {
    Serial.print(F("U"));
    for (uint8_t k = 0; k < NUM_SONAR; k++) { Serial.print(' '); Serial.print(sonarCm[k]); }
    Serial.println();
  }
}

// ------------------------------------------------------------------ main
void setup() {
  pinMode(PIN_MOTOR_L, OUTPUT);
  pinMode(PIN_MOTOR_R, OUTPUT);
  pinMode(PIN_STATUS, OUTPUT);
  pinMode(PIN_BUTTON, INPUT_PULLUP);
  pinMode(PIN_BUZZ_L, OUTPUT);
  pinMode(PIN_BUZZ_R, OUTPUT);
  for (uint8_t k = 0; k < NUM_SONAR; k++) {
    pinMode(SONAR_TRIG[k], OUTPUT);
    pinMode(SONAR_ECHO[k], INPUT_PULLUP);   // unplugged sensor reads a steady HIGH -> -1, not noise
  }
  motorWrite(PIN_MOTOR_L, 0);
  motorWrite(PIN_MOTOR_R, 0);
  buzzWrite(PIN_BUZZ_L, false);
  buzzWrite(PIN_BUZZ_R, false);
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
  pollSonar(now);

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
