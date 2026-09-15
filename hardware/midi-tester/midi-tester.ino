// Deterministic MIDI source for latency/drop testing on the pi-synth.
//
// Playing a keyboard by hand and watching a terminal can't tell a 5 ms path
// from a 500 ms one, and can't tell "dropped" from "I fumbled it". This sends
// MIDI on an exact schedule so the Pi side (tools/midi_latency.py) can measure
// arrival spacing and spot missing notes with no human in the loop.
//
// Every note carries a sequence number in its VELOCITY (1..127, wrapping), so a
// dropped or reordered note is detectable from the received stream alone —
// nothing has to be synchronised between the two clocks. We never compare the
// sender's clock to the Pi's; we only compare *intervals*, which is enough to
// see jitter and loss.
//
// ---------------------------------------------------------------------------
// TRANSPORT — set this, and nothing else changes.
//
// The patterns below are deliberately shared by both paths. USB is the suspect
// in the latency bug and DIN is the control, and that comparison is only worth
// something if the two runs differ in the transport and in nothing else.
//
//   0 = DIN / UART  (any board: Pico, ESP32-C3, ESP32)
//   1 = USB MIDI    (Raspberry Pi Pico only — see the board notes below)
//
// Defaults to USB because that is the path under suspicion. DIN is the control
// run; on the Pico it needs jumper wires to the Pi's GPIO header, and the
// ESP32-C3 can stand in for it.
// ---------------------------------------------------------------------------
#define USE_USB_MIDI 1

// --- Board notes -----------------------------------------------------------
//
// Raspberry Pi Pico (RP2040/RP2350) — does BOTH transports, which is why it is
//   the better board for this job.
//   Board package: earlephilhower's arduino-pico ("Raspberry Pi Pico/RP2040").
//   For USE_USB_MIDI 1 you must also:
//     - install the "Adafruit TinyUSB Library"
//     - set Tools -> USB Stack -> "Adafruit TinyUSB"
//   It then enumerates as a class-compliant USB MIDI device: Linux binds it
//   with snd-usb-audio, no driver needed, and it appears as /dev/snd/midiC*D*
//   exactly as the keyboard does.
//   DIN wiring: GP0 (pin 1) -> Pi GPIO15 (pin 10); GND (pin 3) -> Pi pin 6.
//
// ESP32-C3 — DIN only. Its USB block is a fixed-function Serial/JTAG
//   controller and cannot be repurposed into a MIDI device.
//   DIN wiring: GPIO5 -> Pi GPIO15 (pin 10); GND -> Pi pin 6.
//   GPIO5 because GPIO12-17 are the SPI flash, 18/19 are USB, 20/21 are the
//   UART0 console, and 2/8/9 are boot strapping pins.
//
// For DIN on either board both ends are 3.3 V logic, so connect them directly.
// Do NOT go through the DIN jack's optocoupler, and unplug the jack while
// testing so two things aren't driving the same line.

#if defined(ARDUINO_ARCH_RP2040)
  #define MIDI_TX_PIN 0           // GP0 = UART0 TX
#else
  #define MIDI_TX_PIN 5           // ESP32-C3: see above
#endif

#define MIDI_BAUD 31250           // the MIDI standard rate, as ttymidi expects

#if USE_USB_MIDI
  #if !defined(ARDUINO_ARCH_RP2040)
    #error "USB MIDI here is Pico-only. Set USE_USB_MIDI 0 for the ESP32."
  #endif
  #include <Adafruit_TinyUSB.h>
  Adafruit_USBD_MIDI usb_midi;
#endif

// Patterns, announced with a Program Change so the Pi can segment the log.
enum Pattern { PAT_METRONOME = 0, PAT_BURST = 1, PAT_SUSTAINED = 2, PAT_COUNT };

static const int  NOTE       = 60;    // middle C for all tests
static const int  NOTE_MS    = 20;    // note-on to note-off
static uint8_t    seq        = 1;     // 1..127, carried in velocity

// --- transport -------------------------------------------------------------
// The only two functions that know which wire is in use.

static void midiWrite(const uint8_t *bytes, size_t n) {
#if USE_USB_MIDI
  // tud_midi_stream_write() writes into a fixed TX FIFO and returns how many
  // bytes it actually took. A burst offers bytes far faster than the host
  // drains them, so it WILL come up short, and anything not written is gone.
  //
  // Ignoring that return value made the sender drop ~35% of every burst and
  // report it as a fault on the Pi. Retry until the FIFO accepts the rest,
  // yielding so the USB stack gets to run and drain it.
  size_t sent = 0;
  uint32_t deadline = millis() + 200;   // never wedge the test on a dead link
  while (sent < n && millis() < deadline) {
    sent += usb_midi.write(bytes + sent, n - sent);
    if (sent < n) yield();
  }
#else
  // Serial.write() blocks until the UART buffer accepts everything, so the
  // UART path has never had this problem.
  Serial1.write(bytes, n);
#endif
}

static void midiFlush() {
#if USE_USB_MIDI
  // Adafruit_USBD_MIDI::flush() is a documented no-op ("MIDI Library does not
  // use flush"). Kept for symmetry; what actually drains the FIFO is the core's
  // periodic tud_task() IRQ, which runs regardless of what this loop is doing.
  usb_midi.flush();
#else
  Serial1.flush();
#endif
}

static void send3(uint8_t status, uint8_t d1, uint8_t d2) {
  const uint8_t msg[3] = { status, d1, d2 };
  midiWrite(msg, 3);
}

// --- patterns --------------------------------------------------------------

// One measured note. Velocity is the sequence number, never 0 (a zero-velocity
// note-on is a note-off, which would corrupt the numbering).
//
// holdMs is a parameter and not a constant because it sets a floor on how fast
// notes can be sent: a 20 ms hold makes 20 ms the tightest possible spacing,
// which would quietly turn the burst test into a slow one. Bursts pass 0 and
// let the transport itself set the spacing.
static void sendNote(int holdMs) {
  send3(0x90, NOTE, seq);
  if (holdMs) delay(holdMs);
  send3(0x80, NOTE, 0);
  seq = (seq % 127) + 1;
}

static void announce(uint8_t pattern) {
  const uint8_t msg[2] = { 0xC0, pattern };   // Program Change, channel 1
  midiWrite(msg, 2);
  midiFlush();
  delay(500);                     // a clear gap either side of the marker
}

// 60 notes, one every 500 ms. Steady state: any variation in arrival spacing on
// the Pi is jitter introduced by the path, since these leave exactly on time.
static void metronome() {
  for (int i = 0; i < 60; i++) {
    uint32_t start = millis();
    sendNote(NOTE_MS);
    while (millis() - start < 500) { /* hold the period exactly */ }
  }
}

// 10 bursts of 16 notes sent back to back, 2 s apart. This is the fast-solo
// case — the one that made notes disappear — concentrated and repeatable.
//
// No hold time: the notes go out as fast as the transport carries them (6 bytes
// per note at 31250 baud is ~1.9 ms), so a burst is ~30 ms of solid traffic.
// That is denser than hands can play, which is the point — it is the case that
// breaks. Over USB the same burst leaves faster still, which is fair: the
// question is what each path does with everything we can hand it.
static void burst() {
  for (int b = 0; b < 10; b++) {
    for (int i = 0; i < 16; i++) sendNote(0);
    midiFlush();
    delay(2000);
  }
}

// 500 notes at 40 ms spacing: 25 notes/sec for 20 s. Continuous pressure rather
// than spikes — fast playing, but sustained long enough that a slow leak (a
// buffer filling, a thread falling behind) has time to show.
static void sustained() {
  for (int i = 0; i < 500; i++) {
    uint32_t start = millis();
    sendNote(NOTE_MS);
    while (millis() - start < 40) { }
  }
}

void setup() {
#if USE_USB_MIDI
  usb_midi.setStringDescriptor("pi-synth test source");
  usb_midi.begin();
  // Wait to be enumerated, then a moment more. Notes sent before the host has
  // bound the device are discarded silently, and would read here as drops.
  while (!TinyUSBDevice.mounted()) delay(10);
  delay(2000);
#else
  #if defined(ARDUINO_ARCH_RP2040)
    Serial1.setTX(MIDI_TX_PIN);
    Serial1.begin(MIDI_BAUD);
  #else
    Serial1.begin(MIDI_BAUD, SERIAL_8N1, -1 /* no RX */, MIDI_TX_PIN);
  #endif
  delay(2000);                    // let the Pi side start capturing
#endif
}

void loop() {
  for (uint8_t p = 0; p < PAT_COUNT; p++) {
    announce(p);
    switch (p) {
      case PAT_METRONOME: metronome(); break;
      case PAT_BURST:     burst();     break;
      case PAT_SUSTAINED: sustained(); break;
    }
    delay(3000);                  // silence between patterns
  }
}
