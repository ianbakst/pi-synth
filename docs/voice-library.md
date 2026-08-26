# Voice library — everything through mod-host

How the instrument library is described, validated, and (progressively) moved so
that every voice is an LV2 plugin in one mod-host process. Companion to
[`engine-architecture.md`](engine-architecture.md), which owns the JACK graph and
core allocation.

## Why consolidate on mod-host

Three engine mechanisms exist today — fluidsynth (systemd unit + TCP 9800),
setBfree (systemd unit, no control channel), mod-host (LV2 over a socket). Three
lifecycles, three failure modes, three RT configurations. Two capabilities are
hostage to that split:

- **Effects only work under a mod-host instrument.** `EngineManager.effects_available()`
  is literally `_is_active("modhost")`, so the Hammond and the GM piano can never
  have reverb.
- **`set_gain` only reaches fluidsynth.** No master level, and no way to
  level-match voices against each other.

And switching costs a process cold start: `systemctl start` → JACK port
registration → an LV2 world scan (`ModHostEngine._load_plugin_with_retry` waits
up to 10s for it) or a soundfont read.

**The payoff isn't the host, it's residency.** Once every instrument is an LV2
plugin in one process, plugins can stay *instantiated* at instances 0–9 and a
switch becomes bypass + re-patch — no process start, no instantiate, no world
scan. That is where "switching is faster" actually comes from; moving engines
into mod-host one-for-one only buys uniformity.

**The honest cost:** mod-host processes every instance in one thread, so all
resident non-bypassed plugins share a single period deadline, and one bad plugin
takes down every voice rather than one. That ceiling is lower than N processes
across cores — but per `engine-architecture.md`, instrument → effects is a serial
data dependency anyway, so the parallelism being given up was never real.

## Voice schema

A voice is data. Adding an LV2 instrument is a `voices.json` edit and nothing
else — no engine class, no table in the code:

```json
{
  "name": "Rhodes EP",
  "engine": "modhost",
  "uri": "http://drobilla.net/plugins/mda/EPiano",
  "category": "Electric Piano",
  "resident": true
}
```

| field | meaning |
|---|---|
| `engine` | `modhost` for any LV2 instrument. `sfizz`/`dexed` are legacy aliases that carry their URI in `lv2.PLUGIN_SPECS`; `fluidsynth`/`setbfree`/`pianoteq` are the remaining process engines |
| `uri` | LV2 plugin URI. Required for `modhost` |
| `path` | instrument file (SFZ/SF2/.syx), if the plugin takes one |
| `file_property` | the LV2 **patch property** `path` is set through. Patch properties are atom-based and only reachable via mod-host `patch_set` — `param_set` silently no-ops on them, which is what made sfizz load-but-stay-silent |
| `preset` | LV2 preset URI applied after instantiation |
| `params` | control-port symbol → value, applied *after* `preset` so a voice can start from a stock preset and override a few controls |
| `gain_trim_db` | per-voice output trim, so switching from a sampled piano to a B3 doesn't jump in level. Consumed by the master gain stage (Phase D) |
| `resident` | keep this plugin instantiated rather than loading on demand — the basis of instant switching. Only for plugins cheap in RAM; **not** large sample libraries |

`preset` + `params` are the library multiplier: one `b_synth` instance with
different drawbar registrations *is* Gospel B3, Rock B3, and Jazz B3 as three
library entries.

## Validation

The manifest ships in the image and can outrun what's installed. It did: it
referenced SFZ libraries no image stage creates and a Dexed plugin that was never
built, so **four of six voices failed only when tapped**.

`clients/voice.py validate()` now resolves each voice against the filesystem and
against `lv2ls`, returning a reason string (`"file missing"`, `"plugin not
installed"`, `"no file property for this plugin"`, `"no plugin URI"`, `"unknown
engine"`). Three consumers:

- **The UI** greys the row and shows the reason as its subtitle instead of
  logging it, and won't let it be tapped (`ui/components/voice_list.py`).
- **Boot** falls back to the first *available* voice when the saved/default voice
  isn't usable on this unit — the unit plays on boot rather than going silent.
- **`python3 -m synth_ui.tools.verify_voices`** validates the whole manifest and
  exits non-zero if anything is unusable, so a build or deploy can gate on it.

`LV2World.has()` is deliberately optimistic when `lv2ls` can't be run at all (a
dev machine): a missing tool must not grey out the entire library. Only a world
that was successfully enumerated can rule a URI out.

### Verify on hardware, then codify

`verify_voices --inspect <uri>` prints a plugin's control symbols and patch
properties via `lv2info` — exactly what `params` and `file_property` need. That's
how an unverified URI, or Dexed's still-unknown `.syx` property, gets filled in
without guessing. `lilv-utils` is in `00-packages` for this.

## Plugin sources, cheapest first

**Already in the image** (`mda-lv2`, `calf-plugins` were added for effects, but
both ship instruments): mda EPiano / Piano / DX10 / JX10 / Organ, Calf Organ,
Calf Monosynth. Tiny CPU, instant load, no instrument file. These are in
`voices.json` now — **their URIs are inferred and unverified**; run
`verify_voices` on the board, and anything wrong greys out rather than breaking.

**Apt.** `setbfree` is already installed and should also ship the `b_synth` LV2
(Leslie included) — that moves the Hammond into mod-host and retires
`setbfree.service`. `amsynth` is light with a large preset bank.

**Needs a build** (`02-audio-stack`). Dexed, only if DX7 specifically matters —
mda DX10 plus amsynth cover much of that ground for free.

**Not movable:** Pianoteq, if it's ever bought — no LV2 on Linux ARM as far as we
know. `ProcessEngine` stays alive for exactly that case.

## The master chain

Everything — every voice, and the whole effects rack — now feeds a permanent tail
in mod-host before reaching the DAC:

```
instrument -> [ effects rack ] -> [ MASTER: trim -> limiter ] -> system:playback
```

It exists for two reasons. **Level matching:** each voice carries a measured
`gain_trim_db` applied here, summed with the user's volume, so switching from a
sampled piano to a B3 doesn't jump. **Peak safety:** a limiter last in the path
means nothing clips while levels are being dialled in.

Leaving it running costs little, and the reason matters: what causes dropouts
here is per-period *variance*, not average load. A gain stage and a limiter cost
the same work every period — no disk reads, no allocation, no worker threads —
which makes them the best-behaved things in the graph, unlike a sampler whose
cost spikes on first-touch sample reads. Keep it to gain/limit/EQ; a reverb
belongs in the per-rig rack.

**Automatic leveling was rejected.** A compressor or AGC on the master would even
the instruments out, but it would flatten playing dynamics too — fixing the
switching problem by breaking expression. Fixed per-voice offsets instead.

Two consequences:

- **mod-host is always-on again** (enabled at boot, `PartOf=jack.service`). It
  can't be tied to whether a mod-host *instrument* is active when it hosts the
  output path for every voice. This reverses the pi4-era on-demand lifecycle;
  see the caveat below.
- **Effects work under every voice.** `effects_available()` is now
  unconditionally true — the Hammond and the GM piano can have reverb, which the
  old gate made impossible.

### Calibration

`python3 -m synth_ui.tools.calibrate_levels --midi <file>` plays one fixed
passage into every voice, records the master output, measures **EBU R128
integrated loudness** (not peak — two instruments can peak identically and
differ hugely in perceived level), and writes `gain_trim_db` back into the
manifest. `--dry-run` measures without writing.

It's a starting point, not the last word: measured loudness and "sits right when
I play it" differ, and a voice matched at medium touch can still diverge when you
dig in — that's velocity response, a separate axis from gain. The by-ear nudge on
top is what a rig's `trim_db` stores.

## Rigs

A **rig** is instrument + effects chain + level, saved and switchable as one
unit. Note the naming: "preset" already means a soundfont's bank/program here
(`synth_client.Preset`, the preset screen), so the larger thing is a rig.

Rigs live in `~/.synth-rigs.json`, written atomically — deliberately separate
from `voices.json`, which is a read-only catalog shipped in the image. One is the
user's own work created on the device; the other is what the image provides.

**Loading a rig is a diff, not a rebuild.** The obvious implementation — tear the
rack down, build the new one — makes every rig change pay to instantiate every
plugin in it, which is exactly the cost this architecture exists to avoid.
`rig.plan()` compares the target chain against what's loaded and returns the
minimum change: effects common to both rigs keep their instance and are
re-ordered rather than reloaded, so two rigs sharing a reverb switch without
touching it. Duplicate URIs are matched positionally, so a rig with two delays
keeps both.

Instance numbering across the whole host: instruments `0-9`, effects `10-89`,
master chain `90+`.

### The UI is rig-first

Rigs, not voices, are what you pick — a rig is a whole sound, which is what you
reach for when playing. The screen hierarchy moved accordingly:

| screen | role |
|---|---|
| `screens/rigs.py` (**home**) | the saved rigs; tap to load. Actions: New / Edit / Audio |
| `screens/voice_picker.py` | the instrument catalog, reached via **New**. Picking a voice creates a rig around it. Owns USB import, since that changes the catalog |
| `screens/effects.py` | the active rig's chain. Leaving the screen writes it back to the rig |

`screens/home.py` is gone — `VoicePickerScreen` is what it became.

Rules that live in `RigLibrary` rather than in the screens, so they're testable
without pygame: rigs are named after the voice they start from with numeric
suffixes on collision (two rigs on one instrument is normal — dry vs wet); a
fresh card with no saved rigs bootstraps one from `DEFAULT_VOICE` so the unit
boots into something playable; a rig whose voice has left the catalog is greyed
with the reason rather than loading to silence.

**There's no rename, and naming is automatic.** A touchscreen keyboard is a
sizeable component and wasn't built, so a rig takes its instrument's name. That's
the main rough edge in this flow.

**A rig doesn't capture a soundfont program.** Picking a GM voice still drills
into the preset screen, but the chosen bank/program isn't stored in the rig —
only the voice is.

## Phases

- **A — library + validation. Done.** Voice schema, `clients/lv2.py`, validation
  in UI + boot + CLI, URI-driven `ModHostEngine` (`engine: "modhost"` needs no
  code), `preset_load` on `ModHostClient`, seven new voices.
- **D — master chain + level + rigs. Done.** `MasterChain`, routing through it,
  universal volume, `calibrate_levels`, `Rig`/`plan`/`RigLibrary` +
  `EngineManager.load_rig`, and the rig-first UI (rig list, voice picker,
  effects writing back to the rig). Gaps: no rename (needs a touch keyboard),
  and a rig doesn't store a soundfont program.
- **B — residency.** Multi-instance `ModHostEngine`: allocate instruments to
  instances 0–9, honour `resident`, switch by `bypass` + re-patch instead of
  load. sfizz stays one shared slot that swaps its SFZ (libraries are too big to
  hold N resident). **Verify first:** whether mod-host's `bypass` actually skips
  `lilv_instance_run` or merely mutes — if it only mutes, fall back to
  disconnecting MIDI, which is fine for synths but leaks sampler release tails.
- **C — Hammond to `b_synth`**, retire `setbfree.service`.
- **E — SF2.** Either Calf Fluidsynth in mod-host (retiring `fluidsynth-engine`
  and `synth_client.py`) or drop GM for a curated library. Open question: whether
  GM program-change browsing (`list_presets`/`select_preset`) is worth
  preserving.
- **F — budget.** Measure with mod-host's `cpu_load` and the JACK xrun counter,
  one resident plugin at a time, and codify the ceiling.

## Consequences to handle

- **mod-host is always-on again — verify the xrun cost on the CM5.** Its
  on-demand lifecycle existed because two RT clients contended for core 2 on the
  *pi4* (~180 xruns/min). That measurement predates this board. The window where
  it can still bite is exactly "a process engine is active alongside mod-host" —
  i.e. selecting the Hammond or the GM voice. Check
  `journalctl -u jack | grep -c XRun` with each of those loaded and playing. It
  stops mattering once both move into mod-host (phases C and E).
- **Single point of failure.** One process owns every voice. `Restart=on-failure`
  is set, but `EngineManager` has no rebuild path: after a crash it still
  believes its plugins are loaded. Phase B needs a `_rebuild()` that replays
  resident plugins, params, and wiring on socket reconnect.
- **`DEFAULT_VOICE` is `"General MIDI"`** and depends on fluidsynth existing. It
  has to move to a guaranteed-present mod-host voice before Phase E, or
  boot-plays-immediately breaks. (The available-voice fallback added in Phase A
  covers this in the interim.)
- **The xrun numbers in `engine-architecture.md` are pi4 measurements** (~180 →
  ~65 xruns/min moving mod-host to cores 2,3; the `chrt` priority inversion).
  The reasoning carries over to the CM5, the numbers do not — re-measure before
  treating any of them as the budget.
