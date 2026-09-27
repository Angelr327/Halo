/*
  Dual HC-SR04 bench test - Arduino Uno. No libraries needed.

  Wiring for this standalone test:
    Both VCC pins -> breadboard + rail -> Arduino 5V
    Both GND pins -> breadboard - rail -> Arduino GND
    Left:  Trig -> D7,  Echo -> D6
    Right: Trig -> D11, Echo -> D10

  Upload, then Tools > Serial Monitor at 9600 baud. Each line shows LEFT and
  RIGHT distances with separate echo success rates. Sensors are pinged one at
  a time with a quiet gap to reduce interference. The on-board LED (pin 13)
  lights when either sensor sees something closer than 50 cm.
  Close the Serial Monitor before running the helmet software (only one program
  can use the port). helmet_arduino.ino uses the same sensor pins, so no rewiring
  is needed to switch back to it.
*/
const uint8_t NUM_SENSORS = 2;
const uint8_t PIN_TRIG[NUM_SENSORS] = {7, 11};  // left, right
const uint8_t PIN_ECHO[NUM_SENSORS] = {6, 10};
const unsigned long TIMEOUT_US = 25000;   // ~4.3 m; no echo within this = nothing in range
const unsigned long PING_GAP_MS = 65;     // quiet time after each reading, including right -> left
const int NEAR_CM = 50;

void setup() {
  for (uint8_t i = 0; i < NUM_SENSORS; i++) {
    pinMode(PIN_TRIG[i], OUTPUT);
    pinMode(PIN_ECHO[i], INPUT);
    digitalWrite(PIN_TRIG[i], LOW);
  }
  pinMode(LED_BUILTIN, OUTPUT);
  digitalWrite(LED_BUILTIN, LOW);
  Serial.begin(9600);
  Serial.println(F("HC-SR04 test: LEFT Trig=D7 Echo=D6; RIGHT Trig=D11 Echo=D10."));
  Serial.println(F("Move your hand 5-200 cm in front of each sensor."));
}

long readCm(uint8_t sensor) {
  digitalWrite(PIN_TRIG[sensor], LOW);
  delayMicroseconds(2);
  digitalWrite(PIN_TRIG[sensor], HIGH);    // 10 us pulse starts a measurement
  delayMicroseconds(10);
  digitalWrite(PIN_TRIG[sensor], LOW);
  unsigned long us = pulseIn(PIN_ECHO[sensor], HIGH, TIMEOUT_US);
  return us ? (long)(us / 58) : -1;        // 58 us per cm (sound goes there and back)
}

void loop() {
  static unsigned long pings[NUM_SENSORS] = {0};
  static unsigned long echoes[NUM_SENSORS] = {0};
  bool near = false;

  for (uint8_t sensor = 0; sensor < NUM_SENSORS; sensor++) {
    long cm = readCm(sensor);
    pings[sensor]++;
    if (cm > 0) echoes[sensor]++;
    if (cm > 0 && cm < NEAR_CM) near = true;

    if (sensor > 0) Serial.print(F(" | "));
    Serial.print(sensor == 0 ? F("LEFT distance: ") : F("RIGHT distance: "));
    if (cm < 0) {
      Serial.print(F("  no echo  "));
    } else {
      Serial.print(cm);
      Serial.print(F(" cm  "));
      for (long i = 0; i < min(cm, 200L) / 5; i++) Serial.print('#');   // bar: longer = farther
    }
    Serial.print(F("   ("));
    Serial.print(echoes[sensor] * 100 / pings[sensor]);
    Serial.print(F("% echoes)"));
    delay(PING_GAP_MS);
  }
  Serial.println();

  digitalWrite(LED_BUILTIN, near ? HIGH : LOW);
}
