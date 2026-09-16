# pi-synth

A dedicated MIDI instrument: a Raspberry Pi in a box with a touchscreen, an I2S
DAC and a keyboard plugged into it. You pick a **rig** — an instrument plus an
effects chain plus a level — and play.

This file covers the hardware, the boot configuration and the rules that must
not be broken. It deliberately does **not** describe the software in detail:
that changes, and the source is the truth. Start here, then:

| For | Read |
|---|---|
| How instruments switch and audio is routed | [docs/engine-architecture.md](docs/engine-architecture.md) |
| How the voice library is described and validated | [docs/voice-library.md](docs/voice-library.md) |
| Building a bootable card | [os-image/README.md](os-image/README.md) |
| Diagnosing MIDI timing | [hardware/midi-tester/README.md](hardware/midi-tester/README.md) |

## Architecture in one paragraph

Everything that makes sound is an **LV2 plugin inside one `mod-host` process**.
A rig's signal chain is instrument → effects → master chain (trim + limiter) →
DAC, wired as a JACK graph. The Python UI is a *control plane*: it patches that
graph and sends commands to mod-host over a TCP socket. **Notes never pass
through Python.** If the UI crashes, audio keeps playing.

```
USB keyboard ──> a2jmidid ──┐
                            ├──> JACK MIDI ──> mod-host ──> JACK audio ──> I2S DAC
DIN jack ──> ttymidi ───────┘                  │
                                    instrument ─> effects ─> master chain
```

## Hardware

- **SBC:** Raspberry Pi CM5 on a Waveshare nano Base-B (earlier units: Pi 4).
  64-bit Pi OS Lite, custom PREEMPT_RT kernel (6.12.x).
- **DAC:** Teyleten Robot PCM5102A I2S
  - BCK → GPIO18 (pin 12), DIN → GPIO21 (pin 40), LCK → GPIO19 (pin 35)
  - VIN → 5V (pin 2), GND → pin 6, SCK pad shorted to ground
  - `dtoverlay=hifiberry-dac`; ALSA device `hw:sndrpihifiberry`
    (**by name — the number shifts at boot**)
- **Display:** Waveshare 4.3" DSI touchscreen, 800×480, ft5x06 touch controller
  at `/dev/input/event4`. Driven by `dtoverlay=vc4-kms-dsi-7inch` — the **7-inch**
  overlay on a 4.3-inch panel, which is what Waveshare's own documentation
  specifies: the panel shares the official 7" display's timings and resolution.
  The mismatched name is correct; do not "fix" it to a 4.3-inch overlay.
  Backlight is controllable at `/sys/class/backlight/0-0045` (0–255); the image
  installs a udev rule giving the `video` group write access so the UI can dim
  it. See `clients/backlight.py`.
- **User account:** `synth`

### MIDI inputs

Two, both bridged into JACK (see docs/engine-architecture.md, "MIDI ingress").

**USB keyboard** → `a2jmidid`.

> ⚠️ **The Roland FP-10 must have its Bluetooth turned OFF.** Hold `[FUNCTION]`
> and press the Bluetooth key (see its reference manual's key chart). With
> Bluetooth on, the piano batches its USB MIDI into clumps of ~14 messages
> arriving every ~2 s — which presents as seconds of latency and notes vanishing
> during fast passages. It is the piano's firmware, not this Pi: measured with
> `tools/midi_latency.py` against a Pico on the same USB controller, which
> delivered 1.0 notes per read and 500 of 500 notes at 25 notes/sec.
>
> The FP-10's Memory Backup does **not** persist the Bluetooth setting, so check
> it survives a power cycle. Its Auto Off also defaults to 30 minutes.

**5-pin DIN MIDI IN** on UART0 RX, GPIO15 (pin 10), 31250 baud → `ttymidi`. IN
only; GPIO14/TXD0 is unused. Needs `dtparam=uart0=on` on CM5 (on Pi 4,
`dtoverlay=disable-bt` does it), and the kernel serial console must be kept off
those pins — see below.

## Boot configuration

### `/boot/firmware/config.txt`

```
dtparam=i2s=on
dtoverlay=hifiberry-dac
camera_auto_detect=0
dtoverlay=disable-bt
dtparam=audio=off
```

`display_auto_detect=0` **disabled the touchscreen** — do not add it back.

On CM5, additionally `dtparam=uart0=on`, which enables UART0/ttyAMA0 on
GPIO14/15 for the DIN jack. Bluetooth is irrelevant on BCM2712 — it has its own
UART, so `dtoverlay=disable-bt` frees nothing there (it only matters pre-Pi-5).

Do **not** load `dtoverlay=midi-uart0-pi5`: it skews the UART clock so a
*requested* 38400 lands on 31250, and `ttymidi.service` already asks for a true
31250.

### `/boot/firmware/cmdline.txt`

Appended to the single existing line:

```
isolcpus=1,2,3 nohz_full=1,2,3 rcu_nocbs=1,2,3 usbcore.autosuspend=-1
```

`console=serial0,115200` must be **removed** (pi-gen's base cmdline ships it).
`serial0` is UART0 — the MIDI pins — so leaving it there puts kernel boot output
at 115200 and a systemd-generated `serial-getty` login prompt on top of the
incoming MIDI stream. Dropping the `console=` argument is also what removes the
getty; no separate mask is needed.

Both are written idempotently by `os-image/stage-pi-synth/03-boot-config`.

## System tuning

Already configured; do not change without measuring:

- PREEMPT_RT kernel from `rpi-6.12.y`
- CPU governor locked to `performance` (systemd unit)
- IRQ affinity pinned to core 0, off the isolated audio cores
- Swap disabled
- `@audio - rtprio 99` and `@audio - memlock unlimited` in limits.conf;
  `synth` is in the `audio` group
- Unnecessary services disabled. **Exception:** avahi-daemon stays enabled, so
  the board is reachable as `<hostname>.local` instead of by IP. It does no
  hardware polling and never touches the isolated cores.

## Rules that must not be broken

1. **Notes never pass through Python.** The UI patches the JACK graph and talks
   to mod-host; it is never in the audio or MIDI data path.
2. **Audio survives a UI crash.** `mod-host` and `jack` are independent systemd
   services. The UI is not their parent and must never become one.
3. **Core allocation**: JACK on core 1, instruments and effects on 2–3, all RT.
   The UI runs on core 0 with the OS at normal priority, `Nice=5` — never on an
   isolated core. See docs/engine-architecture.md.
4. **The audio device is named, never numbered**: `hw:sndrpihifiberry`.
5. **Touch events are FINGERDOWN/FINGERMOTION/FINGERUP** with normalized
   coordinates; `ui/event.py` converts them to pixels once, at the edge.
6. **mod-host instance ranges are fixed**: instruments 0–9 (9 is the scratch
   slot), effects 10–89, master chain 90+. Prefix-matching JACK client names
   across these ranges is a bug — `effect_9` must not match `effect_90`.

## Working on it

```bash
uv sync
uv run pytest                     # ~400 tests, no hardware needed
uv run ruff check src/ tests/     # must stay clean
SDL_VIDEODRIVER=dummy uv run python -m synth_ui.main   # UI, windowed, no audio
./deploy.sh                       # rsync + units + restart on the board
```

`deploy.sh` pushes the working tree (not a commit), installs any changed
`systemd/*.service`, copies `instruments/voices.json` into `~/instruments/`, and
prints which board it reached. Flashing a fresh card is `os-image/build.sh`.

### On-device state

These live in `$HOME` on the board and are **not** overwritten by a deploy:

| File | What |
|---|---|
| `~/.synth-rigs.json` | The user's rigs — the actual instrument |
| `~/.synth-trims.json` | Per-voice levels measured by `tools/calibrate_levels` |
| `~/.synth-audio-device` | Selected ALSA card |
| `~/.synth-state` | Last active rig |
| `~/.synth-brightness` | Screen brightness, 0.0–1.0 |

### Tools

All under `python3 -m synth_ui.tools.*`, run on the board:

- `verify_voices` — which voices this unit can actually play; `--inspect <uri>`
  dumps a plugin's control ports and patch properties
- `calibrate_levels` — measures every voice's loudness (EBU R128) and writes
  per-voice trims. `--check` tests each stage of the chain separately
- `midi_latency` — MIDI arrival timing, below ALSA seq and JACK
- `split_soundfont` — one .sf2 of 128 programs → 128 single-voice fonts
- `install_library` — fetch and unpack an SFZ sample library

## Known issues

- JACK period size 64 causes dropouts; 128 is stable (~5 ms).
- `bcm2835-audio` VCHI errors in dmesg are harmless.
- Debian's stock `fluidsynth.service` is masked by the image: we don't run
  fluidsynth, but the package is present for `libfluidsynth` and its unit would
  otherwise take the DAC from JACK.
- Don't disable `wpa_supplicant` — it kills WiFi SSH.
