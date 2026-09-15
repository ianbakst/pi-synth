"""Voice: one entry in the instrument library, plus manifest parsing/validation.

A voice is pure data — it names a plugin (or a legacy process engine) and how to
set it up. Adding an LV2 instrument should mean editing `voices.json` and
nothing else:

    {
      "name": "Rhodes EP",
      "engine": "modhost",
      "uri": "http://drobilla.net/plugins/mda/EPiano",
      "category": "Electric Piano",
      "resident": true
    }

Validation exists because the manifest is shipped in the image and can easily
outrun reality: it referenced SFZ libraries no image stage installs and a Dexed
plugin that was never built, so four of six voices failed only when tapped.
`validate()` turns that into something the UI can show up front and the image
build can assert on.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field

from synth_ui.clients.lv2 import MODHOST_ENGINES, spec_for

# Engines backed by a systemd unit rather than an LV2 plugin. Kept here (rather
# than imported from engine.py) so validation stays free of JACK/subprocess deps.
PROCESS_ENGINES = frozenset({"pianoteq"})
KNOWN_ENGINES = PROCESS_ENGINES | MODHOST_ENGINES


@dataclass
class Voice:
    name: str
    engine: str      # "modhost" | "sfizz" | "dexed" | "fluidsynth" | ...
    path: str        # SF2/SFZ/.syx instrument file; empty when the plugin needs none
    category: str    # "Piano" | "Organ" | "Electric Piano" | ...

    # --- LV2 voices (engine="modhost", or overriding a built-in spec) ---
    uri: str = ""              # LV2 plugin URI
    file_property: str = ""    # LV2 patch property `path` is set through
    preset: str = ""           # LV2 preset URI applied after instantiation
    params: dict[str, float] = field(default_factory=dict)  # control symbol -> value

    # --- SoundFont voices ---
    # Which instrument *inside* the soundfont. A .sf2 holds up to 128 programs
    # per bank, so without this the whole GM set collapses to a single "General
    # MIDI" voice and its Rhodes, Wurlitzer, organ and synth brass are
    # unreachable. -1 = don't select, keep whatever the engine loaded.
    bank: int = 0
    program: int = -1

    # --- Library-level settings ---
    # Per-voice output trim, so switching between a sampled piano and a B3
    # doesn't jump in level. Applied by the master gain stage.
    gain_trim_db: float = 0.0
    # Keep this plugin instantiated in mod-host rather than loading it on
    # demand — the basis of instant switching. Only for plugins cheap enough in
    # RAM to hold: not large sample libraries.
    resident: bool = False

    # Runtime, not manifest: why this voice can't be used here (see validate()).
    # Empty string = usable.
    unavailable_reason: str = ""

    @property
    def available(self) -> bool:
        return not self.unavailable_reason


def read_voices_manifest(manifest_path: str) -> list[Voice]:
    """Parse voices.json. Returns empty list if the file is missing or malformed."""
    if not os.path.exists(manifest_path):
        return []
    try:
        with open(manifest_path) as f:
            data = json.load(f)
        return [_voice_from_entry(entry) for entry in data]
    except Exception:
        return []


def _voice_from_entry(entry: dict) -> Voice:
    return Voice(
        name=entry["name"],
        engine=entry["engine"],
        path=entry.get("path", ""),
        category=entry.get("category", ""),
        uri=entry.get("uri", ""),
        file_property=entry.get("file_property", ""),
        preset=entry.get("preset", ""),
        params={k: float(v) for k, v in (entry.get("params") or {}).items()},
        bank=int(entry.get("bank", 0)),
        program=int(entry.get("program", -1)),
        gain_trim_db=float(entry.get("gain_trim_db", 0.0)),
        resident=bool(entry.get("resident", False)),
    )


def validate(
    voice: Voice,
    *,
    has_uri: Callable[[str], bool] | None = None,
    path_exists: Callable[[str], bool] = os.path.exists,
) -> str:
    """Return why `voice` can't be used here, or "" if it can.

    `has_uri` is `LV2World.has` on the Pi; None skips the plugin-installed check
    (dev machines have no LV2 world worth consulting).
    """
    if voice.engine not in KNOWN_ENGINES:
        return f"unknown engine '{voice.engine}'"

    if voice.engine in MODHOST_ENGINES:
        spec = spec_for(voice.engine, voice.uri, voice.file_property)
        if spec is None:
            return "no plugin URI"
        if has_uri is not None and not has_uri(spec.uri):
            return "plugin not installed"
        # A voice with an instrument file needs somewhere to put it; without the
        # patch property the plugin loads and plays its default (or nothing).
        if voice.path and not spec.file_property:
            return "no file property for this plugin"

    if voice.path and not path_exists(voice.path):
        return "file missing"

    return ""


def annotate(
    voices: list[Voice],
    *,
    has_uri: Callable[[str], bool] | None = None,
    path_exists: Callable[[str], bool] = os.path.exists,
) -> list[Voice]:
    """Set `unavailable_reason` on each voice in place, and return the list."""
    for voice in voices:
        voice.unavailable_reason = validate(
            voice, has_uri=has_uri, path_exists=path_exists
        )
    return voices
