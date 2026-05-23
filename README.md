# MIDI Instrument

A dedicated MIDI instrument built on a Raspberry Pi 4. FluidSynth handles audio synthesis, a PCM5102A I2S DAC handles output, and a 3.5" touchscreen (800×480) lets you switch voices and control volume.

On a non-Pi machine the app runs with a stub audio backend so the UI can be developed without hardware.

## Hardware

| Component | Part |
|---|---|
| SBC | Raspberry Pi 4 |
| DAC | Teyleten Robot PCM5102A (I2S) |
| Display | 3.5" 800×480 touchscreen (ft5x06) |
| MIDI input | USB MIDI keyboard(s) — hot-plug supported |

## Quickstart (dev machine)

```bash
git clone <repo>
cd synth
uv sync
uv run python src/main.py   # runs with stub audio backend
```

## Pi setup

### 1. Clone and install

```bash
git clone <repo> ~/synth
cd ~/synth
sudo apt-get install -y $(cat apt-requirements.txt)
```

### 3. Add SoundFonts

Place `.sf2` or `.sf3` files in `~/soundfonts/`. The UI scans this directory recursively on startup.

### 4. Configure

Edit `config.toml` to match your hardware:

```toml
[audio]
card = "hifiberry"      # matched case-insensitively against /proc/asound/cards
period_size = 128       # lower = less latency; 128 is stable on Pi 4 (~5ms)

[display]
width = 800
height = 480
touch_device = "/dev/input/event4"   # find with: ls /dev/input/event*
```

Set `device = "hw:2"` under `[audio]` to bypass card name resolution if needed.

Override paths via environment variables:
- `SYNTH_CONFIG` — path to a different config file
- `SYNTH_SOUNDFONT_DIR` — soundfont directory (default: `~/soundfonts`)
- `SYNTH_STATE_FILE` — last-selected voice state file (default: `~/.midi-instrument-state`)

### 5. Run manually

```bash
taskset -c 0,1 python3 src/main.py
```

### 6. Install as a service

```bash
sudo cp systemd/cpu-performance.service /etc/systemd/system/
sudo cp systemd/midi-instrument.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cpu-performance.service
sudo systemctl enable --now midi-instrument.service
```

The service expects the repo at `/home/synth/synth` and the user `synth` in the `audio` group. Adjust `WorkingDirectory` and `User` in `systemd/midi-instrument.service` if your setup differs.

## Boot configuration (Pi)

`/boot/firmware/config.txt`:
```
dtparam=i2s=on
dtoverlay=hifiberry-dac
dtoverlay=disable-bt
dtparam=audio=off
camera_auto_detect=0
display_auto_detect=0
```

`/boot/firmware/cmdline.txt` (append to existing line):
```
isolcpus=2,3 nohz_full=2,3 rcu_nocbs=2,3
```

## Project structure

```
src/
  main.py               entry point
  config.py             loads config.toml + env vars, resolves ALSA device
  controller/
    backend.py          AudioBackend ABC, StubBackend, create_backend()
    fluidsynth.py       FluidSynth process management
    socket_client.py    persistent TCP connection to FluidSynth shell
    midi_monitor.py     ALSA hot-plug monitor (libasound via ctypes)
  ui/
    ui.py               Pygame touchscreen UI
    colors.py           colour constants
    layout.py           layout constants
systemd/
  cpu-performance.service
  midi-instrument.service
config.toml             hardware settings (edit this, not config.py)
apt-requirements.txt    system packages (fluidsynth, alsa-utils)
```

## Development

```bash
uv run python src/main.py   # run with stub backend
uv run pytest               # run tests
uv run ruff check src/      # lint
```
