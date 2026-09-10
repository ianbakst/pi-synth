"""InstrumentSlots: which mod-host instance holds which voice, kept loaded.

The point of putting every instrument in one LV2 host was never the host itself
— it was this. A plugin that stays instantiated can be switched to by flipping a
bypass and re-patching JACK, which is immediate. Unloading and re-instantiating
is the slow path: an LV2 world scan, plugin setup, and for a sampler, re-reading
its library off the SD card.

Two kinds of voice, because RAM is finite:

  - **resident** (`Voice.resident`) — small synths (mda, Calf) that cost little
    to keep around. Each gets its own slot and stays there.
  - **non-resident** — large sample libraries. Holding several would blow the
    RAM budget, so they share one scratch slot and pay the load cost on switch.
    That's the same behaviour as before, confined to the voices that need it.

Slot numbering lives inside the instrument range (0-9); effects own 10-89 and
the master chain 90+. When resident voices outnumber the slots, the
least-recently-used one is evicted — bounded memory, no configuration.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from synth_ui.clients.lv2 import PluginSpec, spec_for
from synth_ui.clients.mod_host_client import ModHostClient
from synth_ui.clients.voice import Voice

logger = logging.getLogger(__name__)

# Instruments own 0-9. The last slot is reserved as the shared scratch slot for
# non-resident voices, so a big sampler can never evict the resident set.
_FIRST_SLOT = 0
_LAST_RESIDENT_SLOT = 8
_SCRATCH_SLOT = 9


@dataclass
class Slot:
    instance: int
    voice_name: str
    uri: str
    # The instrument file currently set on this plugin, if it takes one. Lets a
    # re-selected voice skip re-sending an unchanged (and expensive) SFZ path.
    path: str = ""


class InstrumentSlots:
    """Owns the mod-host instrument instances and what's loaded in each."""

    def __init__(self, mod_host: ModHostClient):
        self._mh = mod_host
        # instance -> Slot, in least-recently-used-first order.
        self._slots: dict[int, Slot] = {}
        self._lru: list[int] = []

    # ------------------------------------------------------------------
    # Acquiring
    # ------------------------------------------------------------------

    def acquire(self, voice: Voice) -> int | None:
        """The instance this voice should play from, loading it if needed.

        Returns None if the voice names no plugin or mod-host refuses to load
        it. A voice already resident returns its existing instance with no
        mod-host traffic at all — that is the fast path this class exists for.
        """
        spec = spec_for(voice.engine, voice.uri, voice.file_property)
        if spec is None:
            logger.error("voice '%s' names no LV2 plugin URI", voice.name)
            return None

        existing = self._find(voice, spec)
        if existing is not None:
            self._touch(existing.instance)
            return existing.instance

        instance = self._allocate(voice)
        if instance is None:
            return None
        if not self._load(instance, voice, spec):
            return None
        self._touch(instance)
        return instance

    def is_loaded(self, voice: Voice) -> bool:
        """True if switching to this voice needs no plugin instantiation."""
        spec = spec_for(voice.engine, voice.uri, voice.file_property)
        return spec is not None and self._find(voice, spec) is not None

    def _find(self, voice: Voice, spec: PluginSpec) -> Slot | None:
        """A slot already holding this exact voice.

        Matched on the plugin URI *and* the instrument file: two sfizz voices
        are the same plugin but different instruments, and treating them as
        interchangeable would leave you playing the wrong piano.
        """
        for slot in self._slots.values():
            if slot.uri == spec.uri and slot.path == voice.path:
                return slot
        return None

    # ------------------------------------------------------------------
    # Allocation
    # ------------------------------------------------------------------

    def _allocate(self, voice: Voice) -> int | None:
        """Pick the instance for a voice that isn't loaded yet."""
        if not voice.resident:
            # Large libraries share one slot: keeping several resident is what
            # would actually exhaust RAM.
            self._free(_SCRATCH_SLOT)
            return _SCRATCH_SLOT

        for instance in range(_FIRST_SLOT, _LAST_RESIDENT_SLOT + 1):
            if instance not in self._slots:
                return instance

        # All resident slots taken: evict the least recently used.
        victim = self._lru[0]
        logger.info(
            "instrument slots full; evicting %s from %d",
            self._slots[victim].voice_name,
            victim,
        )
        self._free(victim)
        return victim

    def _free(self, instance: int) -> None:
        if instance in self._slots:
            self._mh.remove_plugin(instance)
            del self._slots[instance]
        if instance in self._lru:
            self._lru.remove(instance)

    def _load(self, instance: int, voice: Voice, spec: PluginSpec) -> bool:
        if not self._mh.load_plugin(spec.uri, instance):
            logger.error("mod-host failed to load %s at %d", spec.uri, instance)
            return False
        slot = Slot(instance=instance, voice_name=voice.name, uri=spec.uri)
        self._slots[instance] = slot

        # Preset first, then explicit params, so a voice can start from a stock
        # LV2 preset and override individual controls.
        if voice.preset and not self._mh.preset_load(instance, voice.preset):
            logger.error("mod-host failed to load preset %s", voice.preset)
        for symbol, value in voice.params.items():
            self._mh.set_param(instance, symbol, str(value))

        if voice.path and spec.file_property:
            # The instrument file is an atom-based patch property; param_set
            # can't reach it.
            self._mh.patch_set(instance, spec.file_property, voice.path)
            slot.path = voice.path
        return True

    def _touch(self, instance: int) -> None:
        if instance in self._lru:
            self._lru.remove(instance)
        self._lru.append(instance)

    # ------------------------------------------------------------------
    # Activation
    # ------------------------------------------------------------------

    def activate(self, instance: int) -> None:
        """Make `instance` the live instrument: unbypass it, bypass every other
        loaded one. Cheap — this is the whole cost of a switch between two
        already-loaded voices."""
        for other in self._slots:
            self._mh.bypass(other, other != instance)

    def loaded_instances(self) -> list[int]:
        return sorted(self._slots)

    def instance_of(self, voice_name: str) -> int | None:
        for slot in self._slots.values():
            if slot.voice_name == voice_name:
                return slot.instance
        return None

    def clear(self) -> None:
        """Drop everything — used when mod-host has restarted underneath us and
        the bookkeeping no longer reflects reality."""
        for instance in list(self._slots):
            self._free(instance)
