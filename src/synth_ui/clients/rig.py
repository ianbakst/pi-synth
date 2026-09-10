"""A rig: one instrument plus its effects chain and level, saved and switchable.

    rig = voice + [effect, effect, ...] + volume trim

Distinct from a *voice*, which is one entry in the shipped instrument catalog
(`voices.json`, read-only, comes from the image). A rig is something you build
on the device by choosing a voice and stacking effects, then save. So rigs live
in their own writable file (`~/.synth-rigs.json`).

Careful with the word "preset" — it already means a soundfont's bank/program in
this codebase (see `synth_client.Preset` and the preset screen). A rig is the
larger thing.

## Loading a rig is a diff, not a rebuild

The obvious implementation — tear the rack down, build the new one — makes every
rig change pay for instantiating every plugin in it, which is the slow operation
we're otherwise working to avoid. Instead `plan()` compares the target chain
against what's already loaded and returns the minimum set of changes: effects
common to both rigs are *kept* and re-ordered rather than reloaded, so two rigs
that share a reverb switch without touching it.

That's why the rack is described declaratively here and applied by
`EngineManager`, rather than the UI calling add/remove itself.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class RigEffect:
    """One effect in a rig's chain. `params` are control symbol -> value."""

    uri: str
    params: dict[str, float] = field(default_factory=dict)


@dataclass
class Rig:
    name: str
    voice: str                 # Voice.name in the catalog
    effects: list[RigEffect] = field(default_factory=list)
    # Offset on top of the voice's calibrated trim — the "by ear" nudge that
    # measured loudness can't give you. See tools/calibrate_levels.py.
    trim_db: float = 0.0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "voice": self.voice,
            "effects": [{"uri": e.uri, "params": e.params} for e in self.effects],
            "trim_db": self.trim_db,
        }

    @staticmethod
    def from_dict(entry: dict) -> Rig:
        return Rig(
            name=entry["name"],
            voice=entry["voice"],
            effects=[
                RigEffect(
                    uri=e["uri"],
                    params={k: float(v) for k, v in (e.get("params") or {}).items()},
                )
                for e in entry.get("effects", [])
            ],
            trim_db=float(entry.get("trim_db", 0.0)),
        )


@dataclass
class ChainPlan:
    """The minimum work to turn the current chain into the target one."""

    remove: list[int] = field(default_factory=list)   # instances to unload
    add: list[RigEffect] = field(default_factory=list)  # effects to instantiate
    # Target order, as (existing instance or None, effect). None = the
    # corresponding entry in `add`, in order.
    order: list[tuple[int | None, RigEffect]] = field(default_factory=list)

    @property
    def is_noop(self) -> bool:
        return not self.remove and not self.add


def plan(current: list[tuple[int, str]], target: list[RigEffect]) -> ChainPlan:
    """Diff a loaded chain against a rig's chain.

    `current` is [(instance, uri), ...] in signal order — what the rack holds
    now. Effects present in both are reused at their existing instance, so a
    shared reverb is re-ordered rather than reloaded. Duplicates of the same URI
    are matched positionally, so a rig with two delays keeps both.
    """
    available: dict[str, list[int]] = {}
    for instance, uri in current:
        available.setdefault(uri, []).append(instance)

    order: list[tuple[int | None, RigEffect]] = []
    add: list[RigEffect] = []
    for effect in target:
        pool = available.get(effect.uri)
        if pool:
            order.append((pool.pop(0), effect))
        else:
            order.append((None, effect))
            add.append(effect)

    remove = [instance for pool in available.values() for instance in pool]
    return ChainPlan(remove=sorted(remove), add=add, order=order)


class RigLibrary:
    """The user's saved rigs, and the operations the UI performs on them.

    Kept out of the screens so the rules — unique names, "there is always at
    least one rig", persistence — are testable without pygame.
    """

    def __init__(self, path: str, rigs: list[Rig] | None = None):
        self.path = path
        self.rigs: list[Rig] = list(rigs or [])

    @classmethod
    def load(cls, path: str) -> RigLibrary:
        return cls(path, read_rigs(path))

    def save(self) -> bool:
        return write_rigs(self.path, self.rigs)

    def names(self) -> list[str]:
        return [r.name for r in self.rigs]

    def get(self, name: str) -> Rig | None:
        return next((r for r in self.rigs if r.name == name), None)

    def unique_name(self, base: str) -> str:
        """`base`, or `base 2`, `base 3`... Rigs are named after the voice they
        start from, and picking the same instrument twice is normal."""
        if self.get(base) is None:
            return base
        n = 2
        while self.get(f"{base} {n}") is not None:
            n += 1
        return f"{base} {n}"

    def create_from_voice(self, voice_name: str) -> Rig:
        """A new rig is just an instrument with nothing on it yet."""
        rig = Rig(name=self.unique_name(voice_name), voice=voice_name)
        self.rigs.append(rig)
        self.save()
        return rig

    def replace(self, rig: Rig) -> None:
        """Persist edits to an existing rig (by name), or append it if new."""
        for i, existing in enumerate(self.rigs):
            if existing.name == rig.name:
                self.rigs[i] = rig
                self.save()
                return
        self.rigs.append(rig)
        self.save()

    def rename(self, rig: Rig, new_name: str) -> str:
        """Rename in place and persist. Returns the name actually used.

        Not `replace()`: that matches on the name, which is the very thing
        changing. Routed through unique_name() so a rename can't collide with an
        existing rig and leave two entries answering to one name — `get()` would
        then only ever find the first."""
        new_name = new_name.strip()
        if not new_name or new_name == rig.name:
            return rig.name
        # unique_name would otherwise count this rig itself as a collision.
        others = [r for r in self.rigs if r is not rig]
        candidate = RigLibrary(self.path, others).unique_name(new_name)
        rig.name = candidate
        self.save()
        return candidate

    def remove(self, name: str) -> None:
        self.rigs = [r for r in self.rigs if r.name != name]
        self.save()

    def bootstrap(self, default_voice: str) -> Rig | None:
        """Guarantee something to play on a freshly flashed card, where no rigs
        have been saved yet. Without this the unit would boot to an empty list
        and make no sound until the user built a rig by hand."""
        if self.rigs:
            return self.rigs[0]
        if not default_voice:
            return None
        return self.create_from_voice(default_voice)


def read_rigs(path: str) -> list[Rig]:
    """Parse the rig store. Missing or malformed file = no rigs (never fatal:
    the instrument still plays from the voice catalog)."""
    if not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            return [Rig.from_dict(entry) for entry in json.load(f)]
    except Exception:
        logger.exception("could not read rigs from %s", path)
        return []


def write_rigs(path: str, rigs: list[Rig]) -> bool:
    """Persist the rig store. Written via a temp file + rename so a crash or a
    pulled power cable can't leave a half-written store behind — this is the
    user's own work, unlike the catalog, and isn't reproducible from the image."""
    tmp = f"{path}.tmp"
    try:
        with open(tmp, "w") as f:
            json.dump([r.to_dict() for r in rigs], f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
        return True
    except OSError:
        logger.exception("could not write rigs to %s", path)
        return False
