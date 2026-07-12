# Engine architecture & audio graph

How the pi-synth control plane switches instruments and routes audio, and the
roadmap to get there. This reconciles the design in `.claude/plan.md` with the
realities of the running system (systemd-managed RT engines, mod-host, the
harvested-kernel image).

## Principles (unchanged from the design)

- **No Python in the note/audio path.** Notes flow `a2jmidid → JACK → engine →
  DAC` in realtime-thread space. Python only issues *control-plane* actions:
  start/stop an engine, load a preset, and patch the JACK graph.
- **The app never manages JACK or RT scheduling itself.** JACK, mod-host, and
  a2jmidid run as systemd services (see `../systemd/`). Engines get their RT
  priority + core pinning from their unit files (`chrt`, `taskset`,
  `LimitRTPRIO`), never from Python.

## Key decision: switch at the systemd level, orchestrate in Python

The design doc had Python `Popen` the engine *and* forbade Python from setting RT
priority — a contradiction (nobody grants the spawned engine `chrt -f 80`). We
resolve it by keeping engines as **systemd services** and putting only the
*orchestration* in Python:

- `ProcessEngine.start()` → `systemctl start <unit>` (not `Popen`). The unit file
  supplies RT priority/affinity/memlock.
- `ModHostEngine.start()` → `add <uri> <instance>` over mod-host's socket
  (mod-host is already an RT JACK client).
- The `Engine` interface, `ENGINE_REGISTRY`, connect-before-disconnect ordering,
  panic-before-teardown, and readiness polling all live in Python.

This is not slower in any way that matters: a switch's cost is dominated by the
engine's own cold-start (soundfont load, JACK port registration); `systemctl`
adds tens of ms of orchestration that is inaudible next to that, and steady-state
audio latency is identical. systemd also buys supervision, journald logging, and
declarative limits. So the choice is made on robustness, not speed.

## CPU core allocation (Pi 4, 4 cores)

One purposeful role per core. Cores 1,2,3 are isolated
(`isolcpus=1,2,3 nohz_full=1,2,3 rcu_nocbs=1,2,3` in `cmdline.txt`); core 0 runs
the normal scheduler.

| Core | Role | Isolated | Pinned via |
|---|---|---|---|
| **0** | OS + UI + all general IRQs | no | `synth-ui.service` `taskset -c 0` `Nice=5`; `cpu-performance.service` sets IRQ mask 1 |
| **1** | JACK backend (audio heartbeat) + MIDI bridge + audio IRQ | yes | `jack.service` / `a2jmidid.service` `taskset -c 1`; audio IRQ steered by `cpu-performance.service` |
| **2** | Active instrument engine | yes | `fluidsynth-engine`/`setbfree`/`mod-host`/pianoteq `taskset -c 2` |
| **3** | Effects (reserved) | yes | effects mod-host `taskset -c 3` — **Phase 3** |

**Why this, and what it buys:** the chain `engine → effects → DAC` is *serial*, so
separate cores don't run stages in parallel — the win is **jitter isolation and
headroom** (each stage uncontended → lower buffers, fewer xruns under sustained
load), not throughput. RT-correct for an instrument.

**mod-host caveat:** mod-host processes all its plugins in one thread, so a single
mod-host can't split instrument (core 2) from effects (core 3). Phase 3 runs a
**second** mod-host pinned to core 3 for effects; until then the existing mod-host
(instruments) sits on core 2 and core 3 stays idle.

**Validate on hardware** before trusting it: `cyclictest -m -Sp99 -i200 -l100000`
on cores 1,2,3, and watch JACK's xrun counter over a sustained switching session.

## The JACK graph (data plane)

```
 a2j:<keyboard capture>  ──MIDI──▶  <active engine>:midi_in
                                         │ audio out
                                         ▼
                              [ effects rack (mod-host) ]   ← optional, persistent
                                         │
                                         ▼
                                 system:playback_1/2  (HiFiBerry DAC)
```

Per-engine JACK identities (discover with `jack_lsp` / `jack_lsp -t` on the Pi —
do not hardcode without checking):

| engine | kind | MIDI in | audio out |
|---|---|---|---|
| fluidsynth | process (`fluidsynth-engine.service`) | `fluidsynth*:midi*` | `fluidsynth*:l/r` (needs `audio.jack.autoconnect=1`) |
| setBfree | process (`setbfree.service`) | `setBfree:midi_in` | `setBfree:out_left/right` |
| sfizz / dexed | mod-host plugin (`mod-host.service`) | `mod-host:midi_in` | mod-host plugin audio out |
| pianoteq | process (future `pianoteq.service`) | `Pianoteq*:midi*` | `Pianoteq*:out_*` |

**Known gap this design closes:** nothing currently connects a mod-host
instrument's *audio* output to `system:playback`, and `fluidsynth -a jack` does
not autoconnect by default — so mod-host voices are likely silent today and
fluidsynth relies on luck. Audio patching becomes an explicit EngineManager
responsibility (Phase 2), and fluidsynth gets `audio.jack.autoconnect=1` now
(Phase 1).

## The Python layer (target shape)

```
src/synth_ui/clients/
├── jack_graph.py     # JackGraph: discover ports + connect/disconnect (subprocess
│                     #   jack_lsp/jack_connect — zero new deps; swappable for
│                     #   python-jack later). connect-before-disconnect lives here.
├── engine.py         # Engine ABC, ProcessEngine (systemctl), ModHostEngine (socket)
├── engine_manager.py # EngineManager: ENGINE_REGISTRY, switch_to(), panic ordering,
│                     #   MIDI + AUDIO patching. Public API (load_voice/list_presets/
│                     #   select_preset/set_gain/is_connected) stays stable for the UI.
├── mod_host_client.py  # (exists) mod-host socket protocol
├── synth_client.py     # (exists) FluidSynth TCP shell
└── voice.py            # (exists) Voice + manifest
```

`Engine` interface (adds `audio_out_ports` and `load` to the design's version):

```python
class Engine(ABC):
    key: str                       # JACK-source identity for same-source detection
    def start(self) -> None: ...
    def stop(self, timeout=2.0) -> None: ...
    def is_ready(self) -> bool: ...        # poll: ports present in the JACK graph
    def panic(self) -> None: ...           # all-notes-off, best effort, before teardown
    def load(self, voice: Voice) -> bool: ...   # load SF2/SFZ/patch into the running engine
    @property
    def midi_port(self) -> str: ...
    @property
    def audio_out_ports(self) -> list[str]: ...
```

**Reality-driven divergence:** sfizz and dexed are *not* separate engines — they
share mod-host's single plugin slot and its stable JACK ports. They collapse into
one `ModHostEngine` whose `load(voice)` swaps the plugin (remove+add if the URI
differs) and `param_set`s the file. Switching sfizz↔dexed is therefore an
in-mod-host swap with **no JACK re-patch** (the ports don't move). The manager
uses `engine.key` (`fluidsynth`/`setbfree`/`pianoteq`/`modhost`) to decide
in-place reload vs. full switch.

`EngineManager.switch_to(voice)` (full switch path):
1. `new.start()`; poll `new.is_ready()` with a bounded timeout → surface a clear
   UI error on timeout.
2. `new.load(voice)`.
3. `graph.connect(keyboard_midi → new.midi_port)` and
   `graph.connect(new.audio_out_ports → sink)` — **before** touching the old one.
   (`sink` = effects-rack input if effects are loaded, else `system:playback`.)
4. If an engine was active: `old.panic()`, then disconnect old MIDI/audio ports
   **except any shared with new** (protects mod-host's shared ports), then
   `old.stop()`.

Same-`key` load (e.g. fluidsynth SF2 → SF2) skips all patching: just
`active.load(voice)`.

## Effects rack (mod-host)

mod-host already hosts instruments; it hosts effects the same way. Model an
`EffectsRack` separate from instruments:

- Effect plugins live at reserved mod-host instances (**instruments `0`, effects
  `10+`**), loaded once and **persistent across instrument switches**.
- Chain wired once: `effect[0].out → effect[1].in → … → last.out →
  system:playback`. On an instrument switch the manager patches
  `active_instrument.out → effects_rack.in`; the rack→DAC leg never moves.
- Python only does `add` / `connect` / `param_set` (mix, time, cutoff…) — effect
  DSP runs in mod-host on the effects core (3). **Watch the xrun counter**: each
  effect adds to that core's RT budget.
- Effect params surface as normal UI controls, like voice selection.
- **Not** running `mod-ui` (MOD's web pedalboard editor) — too heavy/jittery for
  a touchscreen appliance. Drive mod-host directly.

Requires effect LV2 plugins in the image (see Phase 3).

## Startup: a working instrument on boot

`HomeScreen` already loads the restored voice on startup — but only if
`~/.synth-state` exists, so a freshly flashed card plays nothing until you tap a
voice. Fix: fall back to a configured `DEFAULT_VOICE` (the fluidsynth "General
MIDI" voice — its `default.sf2` is guaranteed present in the image, and with
autoconnect its audio reaches the DAC). So: boot → splash → default GM voice
loads → keyboard plays, no interaction needed.

## Phased roadmap

**Phase 1 — working instrument on startup (low risk, no hardware needed to write):**
- `fluidsynth-engine.service`: add `-o audio.jack.autoconnect=1`.
- `DEFAULT_VOICE` in config; `app.py` uses saved-state-or-default.
- Rebuild → boot → GM piano playable with no touchscreen interaction.

**Phase 2 — engine-over-systemd + full audio graph (needs the Pi in the loop):**
- `JackGraph`, `Engine`/`ProcessEngine`/`ModHostEngine`, `EngineManager` rework
  with MIDI **and** audio patching + panic + connect-before-disconnect.
- Fixes mod-host (sfizz/dexed) silence by explicitly wiring their audio to the
  DAC. Retire `scripts/engine-manager.sh` / `midi-connect.sh` (Python
  orchestrates via `systemctl` + `jack_*`).
- Validate on hardware: no gap on switch, panic silences held notes, sustained
  switching produces no xruns (per the OS-tuning validation approach).

**Phase 3 — effects rack:**
- `EffectsRack` over mod-host; add effect LV2 packages to
  `os-image/stage-pi-synth/00-base-packages/00-packages` (e.g. `lsp-plugins-lv2`,
  `zam-plugins`, `x42-plugins`, `dragonfly-reverb-lv2`).
- UI controls for a couple of effect params; xrun budget check.

## Open questions (from the design, still open)

- Exact mod-host audio-out port names — confirm with `jack_lsp -t` on the Pi
  before wiring Phase 2 audio.
- `is_ready` timeout per-engine vs global (Pianoteq starts slower than sfizz).
- Whether to add `pianoteq.service` (proprietary; only if/when installed).
