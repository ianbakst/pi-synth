# MIDI Instrument Project Context

## Overview
A dedicated MIDI instrument built on a Raspberry Pi 4 with FluidSynth as the audio engine, a PCM5102 I2S DAC for audio output, and a 3.5" touchscreen (800x480, ft5x06) for voice selection. The Pi runs Raspberry Pi OS Lite with a custom PREEMPT_RT kernel for low-latency audio.

## Hardware
- **SBC:** Raspberry Pi 4 (running 64-bit Pi OS Lite, kernel 6.12.x)
- **DAC:** Teyleten Robot PCM5102A I2S DAC board
  - Wired via GPIO: BCK→GPIO18 (pin 12), DIN→GPIO21 (pin 40), LCK→GPIO19 (pin 35), VIN→5V (pin 2), GND→pin 6
  - SCK pad shorted to ground (internal clock generation)
  - Bottom jumpers: FLT=L, DEMP=L, XSMT=H (unmuted), FMT=L (I2S)
  - Output pins: ROUT (right), LROUT (left), AGND (×2)
  - Uses `dtoverlay=hifiberry-dac` in config.txt
  - Audio device is `hw:2` in ALSA
- **Display:** 3.5" touchscreen, 800×480 resolution, ft5x06 touch controller at `/dev/input/event4`
- **MIDI:** USB MIDI keyboard, appears as ALSA sequencer client 24
- **Audio output:** Unbalanced from DAC → DI box recommended for balanced/XLR connections

## RT Kernel
- Built from `rpi-6.12.y` branch of `github.com/raspberrypi/linux`
- Used `bcm2711_rt_defconfig` (RT support is built into 6.12, no separate patch needed)
- Built natively on Pi 4: `make -j4 Image.gz modules dtbs`
- Installed as `/boot/firmware/kernel8.img` (Pi bootloader handles gzipped images natively)
- Verified with `uname -a` showing PREEMPT_RT
- Kernel config recommendations: Timer frequency 1000Hz, Full dynticks, disable debug/tracing options for production

## System Tuning
- **CPU governor:** Locked to `performance` via systemd oneshot service
- **CPU isolation:** `isolcpus=2,3` in cmdline.txt — cores 2-3 reserved for FluidSynth
- **Tickless cores:** `nohz_full=2,3 rcu_nocbs=2,3` in cmdline.txt
- **IRQ affinity:** All IRQs pinned to cores 0-1 (bitmask `3`) via startup service
- **Swap:** Disabled (`swapoff -a`, no swap in fstab or systemd)
- **RT permissions:** `/etc/security/limits.conf` has `@audio - rtprio 99` and `@audio - memlock unlimited`
- **User:** `synth` is in `audio` group
- **Disabled services:** avahi-daemon, cron, bluetooth, hciuart, bthelper@hci0, ModemManager, serial-getty@ttyAMA0, systemd-timesyncd, wpa_supplicant (careful — needed for WiFi SSH)
- **Disabled timers:** apt-daily, apt-daily-upgrade, dpkg-db-backup, e2scrub_all, fstrim, logrotate, man-db, rpi-zram-writeback, systemd-tmpfiles-clean
- **Disabled default FluidSynth:** `systemctl --user disable fluidsynth.service` (Pi OS ships one with bad settings)

## Boot Configuration

### /boot/firmware/config.txt additions:
```
dtparam=i2s=on
dtoverlay=hifiberry-dac
camera_auto_detect=0
display_auto_detect=0
dtoverlay=disable-bt
dtparam=audio=off
```

### /boot/firmware/cmdline.txt additions (appended to existing line):
```
isolcpus=2,3 nohz_full=2,3 rcu_nocbs=2,3
```

## FluidSynth Configuration
- Runs with: `chrt -f 80 taskset -c 2,3 fluidsynth -a alsa -o audio.alsa.device=hw:2 -o audio.period-size=128 -o audio.periods=2 -o synth.sample-rate=48000 -o synth.gain=2.0 -m alsa_seq -s <soundfont>`
- Period size 128 is stable on Pi 4 (~5ms latency). Period size 64 (~3ms) works but occasional dropouts.
- Uses TCP shell (`-o shell.port=9800`) for hot-swapping SoundFonts without restart
- MIDI auto-connection: script finds all MIDI clients >15 and connects them to FluidSynth via `aconnect`
- Supports `.sf2` (preferred, raw PCM samples) and `.sf3` (OGG compressed, uses more CPU)

## Project Structure
```
synth/
├── pyproject.toml
├── README.md
├── provision.sh          # Takes fresh Pi OS Lite → working instrument
├── deploy.sh             # Dev machine: rsync + apt sync + test + restart
├── apt-requirements.txt  # System packages: python3-pygame, python3-pytest, fluidsynth
├── .gitignore
├── src/
│   ├── __init__.py
│   ├── main.py               # Entry point
│   ├── config.py             # All hardware settings, colors, layout constants
│   ├── synth_controller.py   # FluidSynth process management + TCP commands
│   ├── ui.py                 # Pygame touchscreen UI
│   └── hardware.py           # Hardware abstraction (planned, not yet implemented)
├── tests/
│   ├── __init__.py
│   └── test_synth_controller.py  # (planned, not yet created)
├── soundfonts/
│   └── .gitkeep
└── systemd/
    ├── midi-instrument.service
    └── cpu-performance.service
```

## Architecture Decisions

### Audio engine as separate process
FluidSynth runs as its own process on isolated cores 2-3. The Python UI runs on cores 0-1 and communicates via TCP socket (port 9800) for voice switching and gain control. This prevents UI activity from ever blocking audio.

### Hardware abstraction (planned)
A `hardware.py` module with `AudioBackend` base class, `AlsaFluidSynthBackend` for the Pi, and `StubBackend` for dev machine testing. Auto-detects environment via `os.path.exists("/sys/firmware/devicetree/base/model")`. This allows the UI to run in a regular pygame window on a dev machine with mock audio.

### Touchscreen input
The ft5x06 touchscreen sends `pygame.FINGERDOWN`, `pygame.FINGERMOTION`, `pygame.FINGERUP` events with normalized 0.0-1.0 coordinates (not mouse events). Coordinates must be multiplied by SCREEN_W/SCREEN_H to get pixel positions. The UI distinguishes taps (voice selection) from drags (list scrolling) using a `finger_moved` flag.

### Voice switching
- Hot-swap via TCP: send `load <path>` to running FluidSynth, then `select 0 1 0 0` and `reset`
- Falls back to full process restart if TCP connection fails
- Last selected voice persisted to `~/.midi-instrument-state`
- SoundFonts scanned recursively from `~/soundfonts/`

### Volume control
- Gain range 0.0 to 5.0 (displayed as 0-100%)
- Updated live via TCP `gain <value>` command — no restart needed

## Development Workflow

### On dev machine:
```bash
uv sync                           # install Python deps
uv run python src/main.py         # runs with StubBackend (once hardware.py is implemented)
uv run pytest                     # run tests
uv run ruff check src/ tests/     # lint
./deploy.sh                       # deploy to Pi, run tests on Pi, restart service
```

### On Pi:
```bash
taskset -c 0,1 python3 src/main.py   # manual run
sudo systemctl restart midi-instrument # restart service
```

### Dependencies:
- Dev machine: managed by uv, defined in pyproject.toml
- Pi: system apt packages only (python3-pygame, fluidsynth, python3-pytest), defined in apt-requirements.txt
- Pin Python version to match Pi (check with `python3 --version` on Pi)
- Pin pygame version to match Pi's apt package (check with `python3 -c "import pygame; print(pygame.ver)"`)

## SoundFont Library

### Recommended SoundFonts (free):
- **GeneralUser GS** (~30MB) — good all-around GM set, 261 presets. Already tested and working.
- **Salamander C5 Lite** (24.5MB) — high quality piano, 7 velocity layers. Download from sites.google.com/view/hed-sounds/salamander-c5-light
- **jRhodes3** — real sampled 1977 Rhodes Mark I, 5 velocity layers. Download from musical-artifacts.com/artifacts/1073
- **HedSound Wurlitzer** (~1MB) — from hedsounds.blogspot.com
- **HedSound B3 Organ** — Hammond B3 presets from hedsounds.blogspot.com
- **hedOrgan** (3.6MB) — pipe organ from hedsounds.blogspot.com
- **Hispasonic Sampled Series** — Rhodes, DX7, Wurlitzer, Clavinet bundle
- **Hohner D6 Clavinet** — from music-sound-lab.com

### GeneralUser GS program numbers:
- 0: Grand Piano
- 4: Tine Electric Piano (Rhodes)
- 5: FM Electric Piano (DX7-style)
- 7: Clavinet
- 16: Tonewheel Organ (Hammond)
- 17: Percussive Organ
- 18: Rock Organ
- 19: Pipe Organ
- 88-95: Synth Pads

## Known Issues / Notes
- Pi 4 headphone jack is PWM-based and noisy — always use the I2S DAC
- `display_auto_detect=0` in config.txt was needed to reduce overhead but initially disabled the touchscreen — it's working now
- FluidSynth's `-i` flag (non-interactive) alone causes immediate exit; must combine with `-s` (server mode) for background operation
- The default FluidSynth user service on Pi OS runs with bad settings (period-size=512, sf3 file) — must be disabled
- Disabling wpa_supplicant kills WiFi SSH access — keep it during development, disable for production/live use
- `bcm2835-audio` VCHI errors in dmesg are harmless (onboard audio trying to init while disabled)

## Future Plans
- Implement hardware.py abstraction layer for dev/Pi parity
- Write comprehensive tests
- Add MIDI program change support for voice switching from keyboard
- Add foot switch / expression pedal support (via Teensy USB MIDI controller)
- Build physical enclosure with balanced output (DRV134 line driver board)
- Consider Yocto/Buildroot custom image for production
- Provisioning script to automate full setup from fresh SD card (provision.sh exists)
