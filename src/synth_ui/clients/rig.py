"""A rig: one instrument plus its effects chain and level, saved and switchable.

    rig = voice + [effect, effect, ...] + volume trim

Distinct from a *voice*, which is one entry in the shipped instrument catalog
(`voices.json`, read-only, comes from the image). A rig is something you build
on the device by choosing a voice and stacking effects.

A rig belongs to exactly one **set** and is stored inside it (see set.py), so
this module owns what a rig *is* and how a chain is diffed, not where rigs live.
`read_rigs` remains only to read the pre-sets store during migration.

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
import uuid
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class RigEffect:
    """One effect in a rig's chain. `params` are control symbol -> value."""

    uri: str
    params: dict[str, float] = field(default_factory=dict)
    # A bypassed effect is part of the rig: you bypass a chorus for one song and
    # want it still there, still set up, when you come back.
    bypassed: bool = False


def new_id() -> str:
    return str(uuid.uuid4())


@dataclass
class Rig:
    name: str
    voice: str                 # Voice.name in the catalog
    # What this rig *is*, as far as anything else is concerned. The name is
    # metadata: two songs can both want a rig called "Rhodes", and renaming one
    # must not turn it into a different rig or break what points at it.
    id: str = field(default_factory=new_id)
    effects: list[RigEffect] = field(default_factory=list)
    # Control symbol -> value for the *instrument*, on top of whatever the
    # catalog entry sets. The catalog is read-only and shipped in the image, and
    # one entry has to serve every rig built on it; the patch — this pad's slow
    # attack, that lead's cutoff — is part of the sound the rig names, so it is
    # the rig's to own and lives in the rig's file. After `effects` because
    # callers build rigs positionally.
    voice_params: dict[str, float] = field(default_factory=dict)
    # Offset on top of the voice's calibrated trim — the "by ear" nudge that
    # measured loudness can't give you. See tools/calibrate_levels.py.
    trim_db: float = 0.0
    # Play every note at config.FIXED_VELOCITY instead of as struck. A rig
    # setting rather than a voice one, because it's a decision about the part
    # being played, not about the instrument: the same organ wants flat velocity
    # for a sustained pad and the keyboard's own dynamics for a solo.
    # See clients/velocity_filter.py.
    fixed_velocity: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "voice": self.voice,
            "voice_params": self.voice_params,
            "effects": [
                {"uri": e.uri, "params": e.params, "bypassed": e.bypassed}
                for e in self.effects
            ],
            "trim_db": self.trim_db,
            "fixed_velocity": self.fixed_velocity,
        }

    @staticmethod
    def from_dict(entry: dict) -> Rig:
        return Rig(
            name=entry["name"],
            voice=entry["voice"],
            # Minted when absent: every rig saved before rigs had identity, on
            # every board in the field, gets one on load and keeps it from the
            # next save onward.
            id=entry.get("id") or new_id(),
            # Absent in every rig file written before rigs carried a patch, so
            # an older store loads with the voice at its catalog settings.
            voice_params={
                k: float(v) for k, v in (entry.get("voice_params") or {}).items()
            },
            effects=[
                RigEffect(
                    uri=e["uri"],
                    params={k: float(v) for k, v in (e.get("params") or {}).items()},
                    bypassed=bool(e.get("bypassed", False)),
                )
                for e in entry.get("effects", [])
            ],
            trim_db=float(entry.get("trim_db", 0.0)),
            fixed_velocity=bool(entry.get("fixed_velocity", False)),
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


def read_rigs(path: str) -> list[Rig]:
    """Parse the pre-sets rig store, for migration only (see set.py).

    Missing or malformed file = no rigs, never fatal: a board with no readable
    rigs still boots and plays from the voice catalog.
    """
    if not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            return [Rig.from_dict(entry) for entry in json.load(f)]
    except Exception:
        logger.exception("could not read rigs from %s", path)
        return []
