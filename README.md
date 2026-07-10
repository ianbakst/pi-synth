# pi-synth

A dedicated, hot-swappable MIDI instrument built on a Raspberry Pi 4. Multiple
audio engines — FluidSynth, sfizz, setBfree, Dexed, Pianoteq — run behind a common
interface and are switched at runtime from a 3.5" touchscreen. Audio flows over
JACK to a PCM5102A I2S DAC; a USB MIDI keyboard is bridged into JACK by
`a2jmidid`. The Python UI is a **control plane only** — it patches the JACK graph
and starts/stops engines; notes and samples never pass through Python.

On a non-Pi machine the UI runs windowed for development (`IS_PI` is auto-detected).

## Hardware

| Component | Part |
|---|---|
| SBC | Raspberry Pi 4 (64-bit, PREEMPT_RT kernel) |
| DAC | Teyleten Robot PCM5102A (I2S, `hifiberry-dac` overlay) |
| Display | 3.5" 800×480 touchscreen (ft5x06) |
| MIDI input | USB MIDI keyboard(s) — hot-plug supported |

## Provisioning: flash the custom image

The supported way to build a unit is to flash the **custom Raspberry Pi OS image**
built by pi-gen — it bakes in the RT kernel, the JACK/engine stack, all systemd
services, and this app, and boots straight to the UI with nothing to run by hand.

See **[os-image/README.md](os-image/README.md)**. In short: drop your prebuilt
RT kernel `.deb` in `os-image/kernel/`, then `cd os-image && ./build.sh`.

## Development

```bash
uv sync
uv run python -m synth_ui.main   # windowed UI (dev machine)
uv run pytest                    # tests
uv run ruff check src/           # lint
```

### Iterating on a running Pi

Once a unit is flashed, push app changes to it without reflashing:

```bash
./deploy.sh <pi-host>    # rsync src -> /home/synth/synth, run tests, restart synth-ui
```

## Project structure

```
src/synth_ui/          the control-plane app
  main.py              entry point (python -m synth_ui.main)
  config.py            paths, screen, ports, colors
  clients/             engine_manager, mod_host_client, socket/synth clients, voice
  ui/                  pygame touchscreen UI (screens, components)
scripts/
  engine-manager.sh    stop current engine, start the new one (called by the UI)
  midi-connect.sh      route the keyboard's JACK MIDI to the active engine
systemd/               the 7 units (jack, a2jmidid, mod-host, synth-ui, ... )
instruments/voices.json  the voice manifest
os-image/              pi-gen custom image build (provisioning)
hardware/              DAC HAT / enclosure design files (Git LFS)
```

See [AUDIO_UPGRADE.md](AUDIO_UPGRADE.md) for the multi-engine audio architecture.
