# pi-synth-os — design handoff (historical)

> **Status: historical.** This is the original design handoff that seeded the
> image build. It has since been implemented — see [`../README.md`](../README.md)
> and [`../stage-pi-synth/`](../stage-pi-synth/). Where the implementation
> deliberately diverged from this document:
>
> - **In-repo, not a separate `pi-synth-os/` repo.** The image build lives here
>   under `os-image/`; the app source is referenced directly (mounted into the
>   build), so no cross-repo vendoring is needed.
> - **Shell-based graph patching, not `JACK-Client` bindings.** The shipped code
>   patches JACK via `scripts/engine-manager.sh` + `scripts/midi-connect.sh`
>   (`jack_lsp`/`jack_connect`) and a mod-host TCP socket — there is no
>   `python-jack` dependency and no venv (only runtime dep is pygame).
> - **RT kernel is harvested from a working RT Pi** — raw artifacts
>   (`kernel8.img` + `/lib/modules/<KVER>`) baked in from `os-image/kernel/`, not
>   a `.deb` and not built in CI (Debian's `linux-image-rt-arm64` doesn't work on
>   Raspberry Pi OS, which uses a Pi-specific kernel + boot path).
> - Stages are `00-base-packages` … `06-system-tuning` (see the README table);
>   Plymouth is deferred as an optional phase-2 stage.
> - **pi-gen is fetched on demand by `build.sh` (pinned commit), not a git
>   submodule.** `os-image/pi-gen/` is gitignored; it's treated as a build tool,
>   not vendored into the repo.
>
> The architecture rationale below (engine model, no-Python-in-note-path, pi-gen
> choice, RT tuning) remains accurate and is why the current design looks as it
> does.

---

Context for picking this up in a fresh conversation. Source project:
https://github.com/ianbakst/pi-synth — a Raspberry Pi hardware synth with
multiple swappable instrument engines (FluidSynth, sfizz, setBfree, Dexed,
Pianoteq). This doc captures the architecture decided so far and the
build-out of a custom `pi-gen` OS image for it. Nothing here has been
pushed to git yet — it's all local scaffold under `pi-synth-os/`.

## Goal, in one sentence

Instruments must be hot-swappable at runtime even though they run on
fundamentally different engines, with the lowest achievable MIDI-to-sound
latency, on a Raspberry Pi OS image trimmed to exactly what this appliance
needs.

## Key architecture decisions (in order arrived at)

1. **Common `Engine` interface, one `EngineManager` behind it.**
   Every engine (FluidSynth, sfizz, setBfree, Dexed, Pianoteq) implements
   the same lifecycle interface (`start`, `stop`, `is_ready`, plus a MIDI
   port identifier) so the UI never needs to know which engine is active.
   `ProcessEngine` (FluidSynth, setBfree, Pianoteq) manages a subprocess;
   `ModHostEngine` (sfizz, Dexed) talks to mod-host's persistent socket
   instead of spawning/killing a process per switch.

2. **No Python in the note path — data plane vs. control plane split.**
   This was a deliberate course-correction: an earlier draft had Python
   objects calling `send_note`. Rejected. Final design: MIDI keyboard →
   `a2jmidid` (ALSA→JACK bridge) → JACK graph → active engine's JACK MIDI
   input → JACK audio out → DAC. Notes and samples never touch Python.
   Python's only job is **graph patching**: on an instrument switch, the
   `EngineManager` calls `jack.connect()`/`jack.disconnect()` (via the
   `JACK-Client` Python bindings, calling into libjack directly — this was
   explicitly chosen over shelling out to `jack_connect`/`aconnect`
   binaries, for lower per-switch overhead) to rewire which engine the
   keyboard's MIDI output is connected to. Connect-new-before-disconnect-
   old ordering avoids a silent gap. `mod-host`-hosted engines (sfizz,
   Dexed) are cheaper still to swap — it's a `remove`/`add` on mod-host's
   own always-running JACK client, not a process restart at all.

3. **Language choice for the UI: stick with Python; a compiled UI
   language would not reduce audio latency**, because the control plane
   was already taken out of the realtime path by decision #2. A compiled
   language (e.g. Rust) *would* help UI frame smoothness, memory/boot
   footprint, and robustness under load — legitimate reasons, just not a
   latency-on-the-note-path reason. Left as an open decision for the user
   to make on those grounds if they want to.

4. **Custom Plymouth boot splash**, replacing the rainbow gradient
   (`disable_splash=1` in `config.txt`, which can only blank it, not
   theme it — that stage is GPU firmware, pre-Linux) and the systemd
   status text (`quiet splash logo.nologo vt.global_cursor_default=0
   loglevel=3` in `cmdline.txt`). Plymouth theme is a `script`-module
   theme (supports static logo or frame-by-frame animation) requiring
   `auto_initramfs=1` in `config.txt` so Plymouth loads early enough.

5. **Custom OS image via `pi-gen`, not a hand-trimmed Lite install or a
   Buildroot/Yocto rebuild.** Reasoning: Buildroot/Yocto is overkill effort
   for a hobby appliance where disk size barely matters; hand-trimming
   Lite after the fact is not reproducible. `pi-gen`'s stage system lets a
   single custom stage (`stage-pi-synth/`) fully replace upstream's
   desktop-oriented stages 3–5, via the `STAGE_LIST` variable in `config`.

6. **Realtime performance tuning, once "lean" was clarified to mean
   *performance*, not disk size:**
   - `PREEMPT_RT` kernel — **user has already confirmed a working RT
     kernel for their specific board and installed it themselves**, so
     the build script for this is intentionally left as an explicit
     either/or fill-in (apt package vs. self-built `.deb`) rather than
     guessed at.
   - `isolcpus=3 nohz_full=3 rcu_nocbs=3` — dedicate one core to
     JACK/engines, keep the Python control plane on the other cores via
     `CPUAffinity=` in its systemd unit (inverse of JACK's).
   - `SCHED_FIFO` priority for JACK (`jackd -R -P89`), `rtprio`/`memlock`
     grants via `/etc/security/limits.d/`.
   - IRQ steering for the audio interface onto the isolated core, via a
     boot-time oneshot systemd service (can't be done at image-build
     time — depends on runtime hardware enumeration).
   - CPU governor `performance`, not `ondemand`.
   - No swap, ever (`dphys-swapfile` purged).
   - Disable Wi-Fi/Bluetooth/HDMI/unused services entirely at the image
     level (`avahi-daemon`, `triggerhappy`, `ModemManager`, `cron`, etc.)
     — framed as removing sources of scheduling jitter, not just saving
     RAM.
   - `journald` set to volatile/RAM-only storage.
   - Ceiling acknowledged as real, not just under-tuned: USB polling
     interval (~1ms) and Pi ARM/GPU shared memory bandwidth are hardware
     floors; an I2S DAC HAT would remove the USB round-trip entirely and
     was flagged as the biggest remaining win if/when the user wants a
     hardware change. Realistic tuned target: 32–64 sample buffers at
     48kHz (~1.5–2.5ms round trip) stable under sustained load.
   - Validation method: `cyclictest -m -Sp99 -i200 -l100000` for
     scheduling floor; watching JACK's own xrun counter over a 10+ minute
     sustained-switching session, not just an idle test, to confirm the
     tuning holds under real load rather than just looking good at rest.

## `pi-gen` mechanics (for whoever picks this up)

- Stages are numbered directories; each contains numbered subdirectories;
  each of those runs numbered scripts (`00-packages` = apt list processed
  automatically, `NN-run.sh` = anything else).
- `NN-run.sh` files execute **on the host**, operating against
  `${ROOTFS_DIR}`. Anything that needs to run *inside* the target
  filesystem goes in an `on_chroot << 'EOF' ... EOF` block within that
  script.
- `STAGE_LIST` in the top-level `config` file overrides the default
  numeric stage order — this repo uses `stage0 stage1 stage2
  stage-pi-synth`, i.e. stages 3–5 (desktop, recommended software) are
  never referenced, not merely skipped.
- Build via `sudo ./pi-gen/build.sh` (native) or `./pi-gen/build-docker.sh`
  (recommended for reproducibility — same result regardless of host
  machine, though it still needs kernel-level loop devices/binfmt on the
  host, so a Linux host or VM either way).
- `pi-gen` is a git submodule pinned to a specific `bookworm`-branch
  commit/tag deliberately, not tracked at `master`, so a `git pull` here
  never silently changes the base OS between builds.
- Iterating: touch a `SKIP` file in a stage directory to not rebuild it,
  `SKIP_IMAGES` to suppress image export for a stage that has one, to
  avoid a 45+ minute full rebuild per small edit.

## Repo layout, current state

```
pi-synth-os/
├── .gitignore
├── .gitmodules              # pi-gen submodule, pinned to bookworm branch
├── config                   # STAGE_LIST etc.
├── kernel-build/            # scaffolded, empty -- for the user's own
│                            # RT kernel cross-compile tooling if they
│                            # want it version-controlled here too
├── docs/
│   └── HANDOFF.md           # this file
└── stage-pi-synth/
    ├── 00-base-packages/
    │   ├── 00-packages      # python3, build-essential, git, etc.
    │   └── 00-run.sh        # pip installs JACK-Client
    ├── 01-realtime-kernel/
    │   ├── 00-run.sh        # RT kernel install (left as explicit
    │   │                    # either/or fill-in per user's own setup),
    │   │                    # config.txt + cmdline.txt edits for
    │   │                    # isolcpus/quiet-boot/auto_initramfs
    │   └── files/           # empty, drop a prebuilt kernel .deb here
    │                        # if using the "self-built" method
    ├── 02-audio-stack/
    │   ├── 00-packages      # jackd2, a2jmidid, fluidsynth, sfizz-lv2, etc.
    │   ├── 00-run.sh        # builds mod-host from source, installs
    │   │                    # setbfree, enables jack/mod-host services
    │   └── files/
    │       ├── jack.service      # CPUAffinity=3, SCHED_FIFO via -P89
    │       └── mod-host.service  # depends on jack.service
    ├── 03-plymouth-splash/
    │   ├── 00-packages      # plymouth, plymouth-themes, initramfs-tools
    │   ├── 01-run.sh        # installs theme, plymouth-set-default-theme -R
    │   └── files/pi-synth/
    │       ├── pi-synth.plymouth
    │       ├── pi-synth.script   # static logo by default; commented-out
    │       │                    # frame-loop version for animation
    │       └── README.md    # reminder to drop in logo.png/frameNN.png
    └── 04-pi-synth-app/
        └── files/
            └── pi-synth.service  # the control plane's own unit --
                                  # CPUAffinity=0,1,2 Nice=5, deliberately
                                  # kept OFF the isolated core

```

## Not yet done — pick up here

- **`04-pi-synth-app/00-run.sh`** — not written yet. Needs to: vendor the
  actual `pi-synth` repo code (controller + UI) into the image via
  `files/`, `pip install -r requirements.txt` into a venv at
  `/opt/pi-synth/.venv`, install `pi-synth.service` (already written) and
  enable it. **This requires the user's actual `controller/backend.py`,
  `fluidsynth.py`, etc. source** — earlier attempts to fetch these from
  GitHub directly were blocked by the fetch tool (only URLs already
  surfaced via search or prior fetch are fetchable); ask the user to
  paste file contents or provide a fetchable link if writing real engine
  classes rather than the illustrative sketches used so far in
  conversation.
- **`05-system-tuning/`** — directory not yet created in this session's
  file scaffold (it was written out in chat as code blocks in the
  previous turn, but the *files* on disk under `stage-pi-synth/` haven't
  been created yet). Needs: `00-run.sh`, `files/audio-rt.conf` (rtprio/
  memlock limits), `files/pin-audio-irq.sh` + `.service` (boot-time IRQ
  steering oneshot). Content for all of these was drafted in chat and
  just needs to be written to disk in the same style as the other stages.
- **`EXPORT_IMAGE`** marker file — needs to be added to
  `stage-pi-synth/` (or whichever is the final stage) so `pi-gen`
  actually produces a bootable image from it.
- **Top-level `README.md`** — not yet written. Should cover: clone with
  `--recursive`, `sudo ./pi-gen/build-docker.sh`, where the image lands
  (`pi-gen/deploy/`), and the `SKIP`/`SKIP_IMAGES` iteration workflow.
- **Read-only root filesystem / overlay** — discussed as a good idea for
  power-cut resilience during the earlier "reduce OS size" tangent, but
  no concrete implementation was designed or written. Would need its own
  stage or an addition to `05-system-tuning`.
- **`logo.png` / animation frames** for the Plymouth theme — placeholder
  only, actual art not created.
- **RT kernel package/`.deb`** — user has this already on their own
  board; just needs to be dropped in or the apt method filled into
  `01-realtime-kernel/00-run.sh`'s commented-out Method A/B.

## Things intentionally left as open decisions for the user, not resolved

- Rust (or other compiled language) for the UI layer — legitimate for UI
  smoothness/footprint, not for latency; user hadn't decided as of last
  message.
- Whether to eventually move to an I2S DAC HAT instead of USB audio, to
  remove the USB polling-interval floor.
- Exact `hw:N` ALSA device index in `jack.service`'s `ExecStart` —
  currently a placeholder (`hw:0`), needs the user's actual audio
  interface identifier.
