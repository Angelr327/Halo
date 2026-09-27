/*
  HC-SR04 bench test - Arduino Uno. No libraries needed.

  Wiring (same pins as the helmet firmware's left sensor):
    VCC  -> 5V
    Trig -> D7
    Echo -> D8
    GND  -> GND

  Upload, then Tools > Serial Monitor at 9600 baud. You should see a distance
  about 10 times a second. The on-board LED (pin 13) lights when something is
  closer than 50 cm, so you can test it without a computer too.
  Close the Serial Monitor before running the helmet software (only one program
  can use the port), and re-flash helmet_arduino.ino when you're done.
*/
const uint8_t PIN_TRIG = 6;
const uint8_t PIN_ECHO = 7;
const unsigned long TIMEOUT_US = 25000;   // ~4.3 m; no echo within this = nothing in range
const int NEAR_CM = 50;

void setup() {
  pinMode(PIN_TRIG, OUTPUT);
  pinMode(PIN_ECHO, INPUT);
  pinMode(LED_BUILTIN, OUTPUT);
  digitalWrite(PIN_TRIG, LOW);
  Serial.begin(9600);
  Serial.println(F("HC-SR04 test: Trig=D7 Echo=D8. Move your hand 5-200 cm in front of it."));
}

long readCm() {
  digitalWrite(PIN_TRIG, LOW);
  delayMicroseconds(2);
  digitalWrite(PIN_TRIG, HIGH);            // 10 us pulse starts a measurement
  delayMicroseconds(10);
  digitalWrite(PIN_TRIG, LOW);
  unsigned long us = pulseIn(PIN_ECHO, HIGH, TIMEOUT_US);
  return us ? (long)(us / 58) : -1;        // 58 us per cm (sound goes there and back)
}

void loop() {
  static unsigned long pings = 0, echoes = 0;
  long cm = readCm();
  pings++;
  if (cm > 0) echoes++;

  Serial.print(F("distance: "));
  if (cm < 0) {
    Serial.print(F("  no echo  "));
  } else {
    Serial.print(cm);
    Serial.print(F(" cm  "));
    for (long i = 0; i < min(cm, 200L) / 5; i++) Serial.print('#');   // bar: longer = farther
  }
  Serial.print(F("   ("));
  Serial.print(echoes * 100 / pings);
  Serial.println(F("% echoes)"));

  digitalWrite(LED_BUILTIN, (cm > 0 && cm < NEAR_CM) ? HIGH : LOW);
  delay(100);
}
