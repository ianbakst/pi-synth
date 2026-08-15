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
| **2** | Active instrument engine | yes | `fluidsynth-engine`/`setbfree`/pianoteq `taskset -c 2`; `mod-host` also uses this core (see below) |
| **3** | Currently: mod-host overflow. Later: effects | yes | `mod-host` `taskset -c 2,3` today; a *second* mod-host for effects — **Phase 3, not yet built** — would also want this core |

Deliberately **not** core 0: that's where `isolcpus`/`nohz_full` route every general
IRQ, kernel housekeeping thread, and the UI, on purpose — audio sharing it would
be exposed to exactly the noise the isolation exists to prevent, and risks
starving the same core SSH/D-Bus run on. Confirmed not worth it on hardware (see
"Active instrument engine" below).

**Why this, and what it buys:** the chain `engine → effects → DAC` is *serial*, so
separate cores don't run stages in parallel — the win is **jitter isolation and
headroom** (each stage uncontended → lower buffers, fewer xruns under sustained
load), not throughput. RT-correct for an instrument.

**"Active instrument engine" is enforced to mean exactly one process, always.**
mod-host used to be always-on (enabled at boot like jack/a2jmidid). Hardware
validation found that having it resident on core 2 at the same time as another
active engine (e.g. fluidsynth) causes continuous JACK XRuns — `journalctl -u
jack | grep -c XRun` kept climbing indefinitely even with both engines fully
idle, because two `chrt -f 80` RT clients were contending for one isolated core
every single 2.67ms period. Stopping one of the two immediately took the xrun
count to a steady 0. Fix: mod-host is now on-demand, exactly like
`fluidsynth-engine`/`setbfree` — `ModHostEngine.start()`/`stop()`
(`src/synth_ui/clients/engine.py`) start/stop `mod-host.service` around the
add/remove-plugin socket calls, and the unit is no longer `systemctl enable`d
at boot (`os-image/stage-pi-synth/05-services`) nor `PartOf=jack.service` (that
would have force-restarted it — i.e. started it even when it shouldn't be
running — on every audio-device change).

**mod-host caveat:** mod-host processes all its plugins in one thread, so a single
mod-host can't split instrument from effects onto separate cores. **Core 3's role
has changed from the original plan**: rather than sit idle waiting for Phase 3,
the instrument mod-host now spans cores 2+3 (`taskset -c 2,3`), because a large
SFZ's sample streaming/decode work was contending with JACK's own callback
thread for core 2 alone — a real, measured problem (below), not a hypothetical
one. **This means Phase 3's plan needs revisiting**: a second mod-host wanting
sole use of core 3 for effects would recreate the exact single-core contention
this fix just solved. Options when that's built: give effects core 3 back and
accept the instrument-side regression, find a third core to shuffle things onto,
or reconsider whether effects need a dedicated core at all. Not decided — flag
this when Phase 3 effects routing actually starts.

**Validated on hardware:**
- `journalctl -u jack | grep -c XRun` flat at 0 with the active instrument engine
  alone on core 2, sustained idle.
- mod-host on core 2 alone, streaming a large SFZ (641 regions) under sustained
  play: ~180 xruns/min despite only ~40-60% CPU — confirming RT audio is
  deadline-per-callback bound, not average-utilization bound; CPU headroom does
  not predict xrun-freedom when a core is shared or contended.
- Same scenario, mod-host expanded to cores 2,3 (`taskset -c 2,3`, still no
  `chrt`): ~65 xruns/min — real improvement, not a full fix (remaining stalls are
  first-touch SD sample reads on this specific library/hardware, not scheduling).
- Re-adding `chrt -f 80` to mod-host on 2,3 was tested and made things
  dramatically worse (~14,500 xruns/min, audible buzzing): it raised mod-host's
  decode threads above JACK's own callback-thread priority, causing them to
  preempt it — a priority inversion. Confirms mod-host should stay affinity-only,
  no `chrt`, matching the SIGKILL/RCU-stall wedge found earlier under heavy load.

Still to validate: `cyclictest -m -Sp99 -i200 -l100000` on cores 1,2,3 for the
raw RT floor, and the xrun counter over a sustained voice-*switching* session
(not just idle) now that mod-host's lifecycle changed.

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

## Audio device selection (which card JACK opens)

A *different* discovery layer from `JackGraph`: which physical sound card jackd
opens. JACK binds one device at startup and presents uniform `system:playback_*`
ports regardless — so the graph patching above is card-agnostic; only jackd's
startup device changes.

- **`AudioDevices`** (`clients/audio_devices.py`) enumerates ALSA playback cards
  from `aplay -l` by stable id (`hw:<id>`, never index), and resolves the
  effective device by precedence: **valid saved choice → HiFiBerry → first card →
  none**.
- **`scripts/start-jack.sh`** is `jack.service`'s ExecStart. It applies that same
  precedence at boot and falls back to jackd's **dummy backend** if no card is
  present — so jackd (and the UI) always come up and the user can recover from a
  missing/renamed card.
- Selection persists in `~/.synth-audio-device` (config `AUDIO_DEVICE_FILE`),
  written by the UI. Absent = auto-detect (the out-of-box default).
- Changing the card = **restart jackd** (not a live patch), which rebuilds the
  stack: `mod-host`/`a2jmidid` are `PartOf=jack.service` so they cycle with it;
  `synth-ui` is `Wants=` (not `Requires=`) so the control-plane UI survives the
  restart it triggered, then reloads the active voice to re-patch.
- **Next:** a UI "Audio" screen (list cards → select → write file → `sudo
  systemctl restart jack.service` → reload voice; sudoers already scoped for it),
  and sample-rate / buffer-size tuning on the same screen.

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
differs) and `patch_set`s the instrument file (an LV2 patch property — sfizz's
SFZ path is atom-based, not a control port; `param_set` silently no-ops on it).
Switching sfizz↔dexed is therefore an
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

## Effects rack (mod-host) — `EffectsRack`

**Resolved:** effects run in the *same* mod-host instance as the active
instrument (cores 2+3), not a second process on its own core. The original
two-instance plan (`systemd/mod-host-fx.service`, socket 5556, core 3) is
superseded — reasoning, not just a resource conflict:

- **Separate cores never bought parallelism here, because there wasn't any to
  buy.** Instrument → effects is a serial *data dependency*: effects can't
  start processing a period until the instrument's output for that period
  exists. Total compute (instrument + effects) has to fit in one ~2.67ms
  deadline no matter which core(s) either stage runs on — there is no
  independent work to overlap. Contrast with why cores 2+3 *did* help the
  instrument alone: mod-host's LV2 "worker" threads (e.g. sfizz's own sample-
  streaming) are genuinely independent of that period's callback and benefit
  from a free core to run on. Effects DSP has no such independent piece — it's
  on the critical path by definition.
- Practical upshot: a second mod-host on its own core would not have isolated
  effects from instrument-side latency the way the original plan assumed. It
  would have added a second full LV2-world-scan cold-start (the exact
  readiness race already fixed once for the instrument mod-host) and a second
  process that could wedge, for a benefit that doesn't materialize for this
  specific pipeline shape.
- **This also resolves one of the two hardware unknowns below**: mod-host's
  per-instance port-naming convention (`effect_<instance>`) is no longer a
  *separate* fx-mod-host question — it's the same convention already confirmed
  on hardware for the instrument mod-host (`effect_0:control` /
  `effect_0:out_left/right`, found while debugging sfizz). Effects loaded at
  instances 10+ in the same process follow the identical naming; nothing new
  to verify there. The *other* unknown (§ below) — whether a real effect
  plugin's DSP fits the shared budget without pushing xruns up — is still a
  hardware question, now the only one.

`clients/effects_rack.py` (`EffectsRack`) manages the chain:

- Effect plugins live at mod-host instances **`10+`** (instruments use `0–9`),
  loaded once and **persistent across instrument switches**.
- `add(uri)` appends to the chain and `_rechain()`s: `fx[0].out → fx[1].in → … →
  fx[N].out → system:playback`. It tears down its prior wiring first (tracked in
  `_wired`) so a stale "old last → DAC" edge never lingers. `remove`/`clear`
  likewise re-chain. Python only patches; DSP is in mod-host.
- `input_ports()` (first effect) / `output_ports()` (last effect) let the manager
  route `active_instrument.out → rack.input`; the rack→DAC leg never moves.
- **Watch the xrun counter as each effect is added** — this is now the real
  budget check, since effects share the instrument's cores and its per-period
  deadline. Add one at a time, measure, don't assume a chain that worked with
  N-1 effects still has headroom for N.
- **Not** running `mod-ui` (MOD's web pedalboard editor) — too heavy/jittery for a
  touchscreen appliance. Drive mod-host directly.

**One open question left that needs the board:** does real effect DSP (a
reverb, an EQ, a compressor) actually fit the shared per-period budget
alongside a loaded instrument, and how many/how heavy before xruns climb? No
longer an architecture question — just needs measuring on real hardware, the
same way the instrument-alone budget was measured tonight (~180 → ~65
xruns/min moving 2→cores 2,3; a similar live test applies here per effect
added).

**Still deferred, now purely because nobody's built it yet, not because of an
open unknown:** `EngineManager` routing (instrument → rack → DAC, and
re-wiring the instrument when the rack goes empty↔non-empty) and the UI
effects screen — in progress, see the plan for "Wire the effects rack into
EngineManager + UI". `mod-host-fx.service` (the old, superseded two-instance
design) has been deleted.

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
- *Validated on hardware:* found and fixed mod-host being always-on causing
  continuous core-2 XRuns (see "Active instrument engine" note above) — it's
  now on-demand like the other engines.
- Still to validate: no gap on switch, panic silences held notes, sustained
  *switching* produces no xruns (idle is now confirmed clean; switching load
  hasn't been re-tested since the mod-host lifecycle change).

**Phase 3 — effects rack** (see "Effects rack" above):
- *Done (increment 1):* effect LV2 packages in `00-packages`
  (`calf-plugins`, `zam-plugins`, `x42-plugins`, `mda-lv2`); `mod-host-fx.service`
  (core 3, installed not enabled); `EffectsRack` class + tests.
- *Needs the board:* confirm the fx mod-host client name and the effect
  audio-port naming (the two open questions above), then wire `EngineManager`
  routing (instrument → rack → DAC) and the UI effects screen.
- Watch the xrun budget on core 3 as effects are added.

## Engine build status (image `02-audio-stack`)

- **sfizz** — LV2 plugin built from `sfztools/sfizz-ui` (NOT `sfztools/sfizz`,
  which is only the core lib + a JACK client we don't use). Verified on hardware:
  installs `/usr/local/lib/lv2/sfizz.lv2`, URI `http://sfztools.github.io/sfizz`
  (matches `_MODHOST_PLUGINS` in `clients/engine.py`).
- **Dexed** — **not built yet.** The two DX7 voices in `voices.json` will fail to
  load until a Dexed LV2 build is added to `02-audio-stack` (same
  verify-on-hardware-then-codify path sfizz took). Its URI/param symbol in
  `_MODHOST_PLUGINS` (`https://asb2m10.github.io/dexed`, `sysex_file`) are
  unconfirmed until then.

## Open questions (from the design, still open)

- Exact mod-host audio-out port names — confirm with `jack_lsp -t` on the Pi
  before wiring Phase 2 audio.
- `is_ready` timeout per-engine vs global (Pianoteq starts slower than sfizz).
- Whether to add `pianoteq.service` (proprietary; only if/when installed).
