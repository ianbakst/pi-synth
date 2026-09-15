# MIDI test source

A microcontroller that plays the same notes the same way every time, so the
latency question can be answered with numbers instead of "that felt late."

Playing by hand and watching a terminal can't separate a 5 ms path from a 50 ms
one, can't tell a dropped note from a fumbled one, and can't be repeated
identically after a config change. This can.

## Which board

**Use the Raspberry Pi Pico.** It is the only one of the two that can drive
*both* transports, which is what the investigation actually needs: USB is the
suspect, DIN is the control, and comparing them is only meaningful if the note
schedule is identical on both. One `#define` at the top of the sketch switches
between them; nothing else changes.

The ESP32-C3 works for DIN only. Its USB block is a fixed-function Serial/JTAG
controller and cannot be repurposed into a MIDI device — unlike an S2 or S3,
which have a general USB peripheral. Keep it as a spare.

| | DIN / UART | USB MIDI |
|---|---|---|
| Pi Pico (RP2040/RP2350) | yes | yes — class-compliant, no driver needed |
| ESP32-C3 | yes | no |

## Wiring — DIN

| Pico | ESP32-C3 | Pi |
|---|---|---|
| GP0, pin 1 (TX) | GPIO5 | GPIO15 / pin 10 (RXD0) |
| GND, pin 3 | GND | pin 6 (GND) |

Both ends are 3.3 V logic, so they connect directly. **Do not** route through the
DIN jack's optocoupler, and unplug the DIN jack while testing — otherwise two
things drive the same line.

The C3 pin is not arbitrary: GPIO12–17 are the SPI flash, 18/19 are USB, 20/21
are the UART0 console, and 2/8/9 are boot strapping pins.

## Wiring — USB

A USB cable. That's the point of it.

## Flashing

**Pico**, with earlephilhower's arduino-pico board package. The USB build needs
the TinyUSB stack, which that core already bundles — there is no library to
install, only a different FQBN. Both variants below are known to compile.

```bash
# DIN  (USE_USB_MIDI 0)
arduino-cli compile --fqbn rp2040:rp2040:rpipico .

# USB  (USE_USB_MIDI 1) — note the usbstack option; without it the sketch
# will not find Adafruit_TinyUSB.h
arduino-cli compile --fqbn rp2040:rp2040:rpipico:usbstack=tinyusb .
```

In the IDE the same switch is **Tools → USB Stack → Adafruit TinyUSB**.

To upload, the Pico must be in its bootloader: unplug it, hold **BOOTSEL**, plug
it in, release. It mounts as a drive called `RPI-RP2`; copy the `.uf2` there, or
point arduino-cli at it:

```bash
arduino-cli upload --fqbn rp2040:rp2040:rpipico -p /Volumes/RPI-RP2 .
```

BOOTSEL is only needed to interrupt firmware that is already running. **Flashing
replaces whatever is on the board**, and the original is unrecoverable without
its `.uf2` — check what is on the Pico before wiping it.

**ESP32-C3** (DIN only):

```bash
arduino-cli compile --fqbn esp32:esp32:esp32c3 .
arduino-cli upload  --fqbn esp32:esp32:esp32c3 -p /dev/cu.usbmodem* .
```

The C3 appears as a `usbmodem`/`ttyACM` device, not `ttyUSB` — it has no external
USB serial chip.

Either board starts sending 2 s after power-up and loops forever.

## What it sends

Each pattern is announced by a Program Change so the Pi can label the log.
Every note carries a sequence number (1..127, wrapping) in its **velocity**, so
drops and reordering are visible from the received stream alone — the two clocks
are never compared.

| # | Pattern | What it answers |
|---|---|---|
| 0 | 60 notes, one every 500 ms (30 s) | Is there jitter at rest? |
| 1 | 10 bursts of 16 notes back-to-back, 2 s apart (20 s) | The fast-solo failure, on demand |
| 2 | 500 notes at 40 ms — 25/sec for 20 s | Does it degrade over time? |

One full cycle is about 80 s.

## Measuring on the Pi

Stop everything that holds the MIDI devices open, so the baseline is a quiet
machine:

```bash
sudo systemctl stop synth-ui jack mod-host ttymidi a2jmidid
```

Then read as close to the kernel as userspace can get:

```bash
cd ~/synth/src          # the package lives here; there is no installed copy

# DIN   (USE_USB_MIDI 0)
python3 -m synth_ui.tools.midi_latency --serial /dev/ttyAMA0

# USB   (USE_USB_MIDI 1, Pico)
python3 -m synth_ui.tools.midi_latency --list       # show what's plugged in
python3 -m synth_ui.tools.midi_latency --rawmidi    # uses the only device, or
                                                    # name one: --rawmidi /dev/snd/midiC1D0
```

For the **USB** run the Pico goes in the Pi's USB port, so unplug the keyboard —
that carrier has only one. For the **DIN** run power the Pico from a charger or
laptop instead, leaving the Pi's port free and one less thing in the
measurement.

Both read the device directly: below ALSA's sequencer, below JACK, below
everything in this project. A delay that shows up here is upstream of all of it.
That is the whole point — it says whether to look at our software or at the
kernel and hardware under it.

Output is inter-arrival min/median/p95/max per pattern, the spread, the dropped
count, and peak per-core CPU plus interrupt deltas over the run.

**Read the spread, not the median.** A path that adds a constant 30 ms is
playable; one that swings between 5 ms and 300 ms is not, and that swing is what
this instrument has.

## Interpreting a run

| What you see | What it means |
|---|---|
| Spread in pattern 0 | Sending is exact, so any variation is jitter the path added |
| Drops only in pattern 1 | A buffer overruns under burst — driver or FIFO |
| Spread grows through pattern 2 | Something falls behind and never recovers |
| Clean here but late through JACK | The fault is ours, downstream of this |
| Late here | The fault is below us: the driver, the kernel, or the board |

## Reading the two runs together

This is the comparison the Pico makes possible, and it is decisive either way:

| DIN | USB | Conclusion |
|---|---|---|
| clean | clean | Nothing below us is broken. The fault is in our stack — a2jmidid, ttymidi, JACK, or mod-host — and it is reproducible with this same source feeding the running system. |
| clean | late | USB MIDI specifically: `snd-usb-audio`, xHCI, or the single port on the Waveshare carrier. Also means you have a working instrument today over DIN. |
| late | late | Below both paths — kernel or board. Every USB experiment so far was aimed at the wrong thing, and the stock-kernel swap becomes the next test. |
| late | clean | The UART side, which nothing depends on yet. Move the keyboard to USB and carry on. |

Eight theories have been tried against this bug by ear. Two runs of this settle
which quadrant the fault is in, before any more time goes into guessing.
