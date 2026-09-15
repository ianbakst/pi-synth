"""What can go in a rig's effects chain.

Curated in code, not discovered. `lv2ls` on this board lists a couple of hundred
plugins; a rack browser showing all of them is unusable on a 800x480 panel with
fingers. The list below is chosen for the instruments this thing plays —
electric pianos, a Hammond, a clav, analog polys — and deliberately offers one
or two good options per job rather than every plugin that claims the category.

Entries can be added or replaced per-unit through `instruments/effects.json`,
matched on URI. That file starts empty and usually stays that way; it exists so
a board can carry something the image doesn't.

Every entry is checked against the installed plugins on load, the same way
voices are, so a URI that is wrong or a package that isn't installed shows as
greyed-out in the browser instead of failing silently when tapped.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_CALF = "http://calf.sourceforge.net/plugins/"
_MDA = "http://drobilla.net/plugins/mda/"


@dataclass
class EffectCatalogEntry:
    name: str
    uri: str      # LV2 plugin URI, passed straight to EngineManager.add_effect
    category: str  # "Reverb" | "Delay" | "EQ" | "Dynamics" | etc.
    note: str = ""
    unavailable_reason: str = ""

    @property
    def available(self) -> bool:
        return not self.unavailable_reason


# Ordered by where each belongs in a signal chain, because that is the order
# you build one in: tone shaping, then dirt, then movement, then space, then
# control. The browser keeps this order rather than sorting alphabetically.
DEFAULT_EFFECTS: list[EffectCatalogEntry] = [
    # --- amp and drive: most of what makes a Rhodes or clav sound played ---
    EffectCatalogEntry("Combo Amp", _MDA + "Combo", "Amp",
                       "Speaker cabinet sim. The clav and Rhodes both want one."),
    EffectCatalogEntry("Overdrive", _MDA + "Overdrive", "Drive",
                       "Soft asymmetric clip. Subtle at low settings."),
    EffectCatalogEntry("Saturator", _CALF + "Saturator", "Drive",
                       "Tube-style warmth; more controllable than Overdrive."),
    EffectCatalogEntry("Tape", _CALF + "TapeSimulator", "Drive"),

    # --- movement ---
    EffectCatalogEntry("Rotary Speaker", _CALF + "RotarySpeaker", "Rotary",
                       "Leslie. The Hammond is only half an organ without it."),
    EffectCatalogEntry("Leslie", _MDA + "Leslie", "Rotary",
                       "Lighter than Calf's; worth comparing on CPU."),
    EffectCatalogEntry("Chorus", _CALF + "MultiChorus", "Modulation",
                       "The classic Rhodes/Wurlitzer widener."),
    EffectCatalogEntry("Phaser", _CALF + "Phaser", "Modulation",
                       "For the clav, and for 70s electric piano."),
    EffectCatalogEntry("Flanger", _CALF + "Flanger", "Modulation"),

    # --- time ---
    EffectCatalogEntry("Vintage Delay", _CALF + "VintageDelay", "Delay",
                       "Tempo-ish delay with filtered repeats."),
    EffectCatalogEntry("Delay", _MDA + "Delay", "Delay"),
    EffectCatalogEntry("Reverb", _CALF + "Reverb", "Reverb"),
    EffectCatalogEntry("Ambience", _MDA + "Ambience", "Reverb",
                       "Small and cheap — a room, not a hall."),

    # --- tone and control ---
    EffectCatalogEntry("EQ 5-Band", _CALF + "Equalizer5Band", "EQ"),
    EffectCatalogEntry("Filter", _CALF + "Filter", "Filter",
                       "Sweepable; the wah half of a clav sound."),
    EffectCatalogEntry("Compressor", _CALF + "Compressor", "Dynamics",
                       "Evens out a piano before it hits the master limiter."),
    EffectCatalogEntry("Bass Enhancer", _CALF + "BassEnhancer", "Tone"),
    EffectCatalogEntry("Exciter", _CALF + "Exciter", "Tone"),
]


def annotate_effects(
    entries: list[EffectCatalogEntry], has_uri: Callable[[str], bool]
) -> list[EffectCatalogEntry]:
    """Mark entries whose plugin isn't installed here.

    Same contract as voice.annotate: the browser shows the entry greyed out
    with a reason, rather than letting someone tap an effect that can never
    load. `has_uri` is deliberately optimistic when the plugin list can't be
    read, so a dev machine doesn't grey out everything.
    """
    for entry in entries:
        entry.unavailable_reason = "" if has_uri(entry.uri) else "plugin not installed"
    return entries


def read_effects_manifest(manifest_path: str) -> list[EffectCatalogEntry]:
    """The curated catalogue, with any per-unit overrides from effects.json
    applied. A missing or malformed file leaves the defaults untouched — an
    unreadable override must not empty the rack browser."""
    catalog = [
        EffectCatalogEntry(e.name, e.uri, e.category, e.note) for e in DEFAULT_EFFECTS
    ]
    if not os.path.exists(manifest_path):
        return catalog

    try:
        with open(manifest_path) as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        logger.warning("could not read %s: %s", manifest_path, exc)
        return catalog

    by_uri = {e.uri: i for i, e in enumerate(catalog)}
    for entry in data:
        try:
            new = EffectCatalogEntry(
                name=entry["name"],
                uri=entry["uri"],
                category=entry.get("category", ""),
                note=entry.get("note", ""),
            )
        except (TypeError, KeyError):
            logger.warning("skipping malformed effects.json entry: %r", entry)
            continue
        if new.uri in by_uri:
            catalog[by_uri[new.uri]] = new
        else:
            by_uri[new.uri] = len(catalog)
            catalog.append(new)
    return catalog
