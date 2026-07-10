# pi-synth OS image (pi-gen)

Builds a custom Raspberry Pi OS image that boots straight into the pi-synth
appliance — RT kernel, JACK/engine stack, all seven systemd services, and the UI
— with nothing to run by hand after flashing. This replaces the old `setup.sh`
provisioning of a stock Lite card.

It's a thin custom stage (`stage-pi-synth/`) layered on top of pi-gen's minimal +
Lite stages (`stage0`–`stage2`); the desktop stages 3–5 are never referenced.
pi-gen itself is a **pinned git submodule** (`bookworm-arm64`) — it is never
modified; our stage lives here and is fed to pi-gen by absolute path.

## Prerequisites

- **Docker** running (Docker Desktop on macOS; Apple Silicon builds the arm64
  target natively and fast — Intel falls back to qemu).
- Your **PREEMPT_RT kernel artifacts** harvested from a working RT Pi in
  [`kernel/`](kernel/) — `kernel8.img` + the matching `modules/<KVER>/` tree
  (dtbs/overlays optional). See [kernel/README.md](kernel/README.md).

## Build

```bash
git clone --recursive https://github.com/ianbakst/pi-synth.git
cd pi-synth
# harvest your RT kernel into os-image/kernel/ (see kernel/README.md)
cd os-image
./build.sh
```

(Already cloned without `--recursive`? Run
`git submodule update --init os-image/pi-gen`.)

The image lands in `os-image/pi-gen/deploy/` as `*-pi-synth.img.xz`.

### Iterating

A full build is ~30–45 min. To resume after a failed/edited stage instead of
rebuilding from scratch:

```bash
CONTINUE=1 ./build.sh
```

To skip an expensive stage you've already built while iterating on a later one,
drop a `SKIP` file in it, e.g. `touch stage-pi-synth/02-audio-stack/SKIP`
(remove it before a real build). See the pi-gen README for the full
`SKIP`/`SKIP_IMAGES` workflow.

## Flash & verify

Flash `deploy/*-pi-synth.img.xz` with Raspberry Pi Imager (or `dd`). On first
boot it should come up on the touchscreen with no manual steps. Sanity checks
over SSH (`synth` / password from `config`):

```bash
uname -v                                   # contains PREEMPT_RT
aplay -l | grep -i hifiberry               # DAC present
systemctl is-active jack a2jmidid mod-host synth-ui   # all active
```

Then confirm a USB keyboard plays and switching voices works. For the realtime
floor: `cyclictest -m -Sp99 -i200 -l100000`, and watch JACK's xrun counter over a
sustained voice-switching session.

## What the stage does (ported 1:1 from the old setup.sh)

| Stage | Responsibility |
|---|---|
| `00-base-packages` | apt packages + jackd2 RT-limits debconf preseed |
| `01-realtime-kernel` | install harvested RT `kernel8.img` + modules, `depmod` |
| `02-audio-stack` | build + install mod-host and sfizz (LV2) from source |
| `03-boot-config` | `config.txt` (HiFiBerry DAC, disable BT/onboard audio) + `cmdline.txt` (isolate cores 2,3; quiet boot) |
| `04-pi-synth-app` | vendor app to `/home/synth/synth`, dirs, `default.sf2`, sudoers |
| `05-services` | install 7 units; enable always-on ones; mask stock fluidsynth |
| `06-system-tuning` | RT limits, RAM-only journald, no swap, disable jitter services |

CPU governor and IRQ affinity are applied at runtime by `cpu-performance.service`.

## Configuration

Edit [`config`](config): first user (`synth`), hostname, locale/timezone,
WiFi country, SSH pubkey, image compression. The `synth` username is load-bearing
(hardcoded in the units and `config.py`) — don't change it.
