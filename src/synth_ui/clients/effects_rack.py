"""
EffectsRack: an ordered chain of LV2 effects hosted in the SAME mod-host
instance (port 5555) that hosts the active mod-host instrument, if any.

A prior design ran effects in a second mod-host instance on its own core
(port 5556, systemd/mod-host-fx.service, now deleted). That's dead: instrument
-> effects is a serial data dependency (effects can't process a period until
the instrument's output for that period exists), so a second core never bought
parallelism between those two stages — see docs/engine-architecture.md
"Effects rack" for the full reasoning. EngineManager constructs this rack with
its existing ModHostClient (self._mod_host); never a second one.

The rack is persistent across instrument switches; Python only patches the
JACK graph (add/remove/param + wiring), never touching audio itself.

Signal flow it owns:  fx[0].out -> fx[1].in -> ... -> fx[N].out -> `sink`
The instrument's audio -> fx[0].in leg is owned by EngineManager (it moves on an
instrument switch); the rack's output -> sink leg never moves.

`sink` is a callable, not the DAC directly: the rack now feeds the permanent
master chain (trim + limiter), which feeds the DAC. It falls back to the DAC
when no master chain loaded — see master_chain.sink_for().

Effects live at mod-host instances 10+ (instrument engines use instance 0).

Port naming: mod-host exposes each plugin instance under a per-instance JACK
client named "effect_<instance>" (both audio and control ports) — confirmed on
hardware, the same convention already proven for the instrument mod-host
(ModHostEngine.audio_client), not a separate unknown.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass

from synth_ui.clients.jack_graph import JackGraph
from synth_ui.clients.mod_host_client import ModHostClient
from synth_ui.clients.rig import ChainPlan

logger = logging.getLogger(__name__)

_BASE_INSTANCE = 10
# Instruments own 0-9, effects 10-89, the master chain 90+.
_MAX_INSTANCE = 89


@dataclass
class Effect:
    instance: int
    uri: str


class EffectsRack:
    def __init__(
        self,
        jack: JackGraph,
        mod_host: ModHostClient,
        sink: Callable[[], list[str]] | None = None,
    ):
        self._jack = jack
        self._mh = mod_host
        self._sink = sink or jack.dac_sinks
        self._effects: list[Effect] = []
        self._wired: list[tuple[str, str]] = []  # connections the rack established

    # ------------------------------------------------------------------
    # Chain management
    # ------------------------------------------------------------------

    def add(self, uri: str) -> int | None:
        """Load an effect at the end of the chain. Returns its instance id."""
        instance = _BASE_INSTANCE
        if self._effects:
            instance = max(e.instance for e in self._effects) + 1
        if not self._mh.load_plugin(uri, instance):
            logger.error("effects mod-host failed to load %s", uri)
            return None
        self._effects.append(Effect(instance, uri))
        self._rechain()
        return instance

    def remove(self, instance: int) -> None:
        self._mh.remove_plugin(instance)
        self._effects = [e for e in self._effects if e.instance != instance]
        self._rechain()

    def clear(self) -> None:
        for effect in list(self._effects):
            self._mh.remove_plugin(effect.instance)
        self._effects.clear()
        self._rechain()

    def set_param(self, instance: int, symbol: str, value: str) -> bool:
        return self._mh.set_param(instance, symbol, value)

    def effects(self) -> list[Effect]:
        return list(self._effects)

    def snapshot(self) -> list[tuple[int, str]]:
        """(instance, uri) in signal order — the input to rig.plan()."""
        return [(e.instance, e.uri) for e in self._effects]

    def apply(self, chain: ChainPlan) -> bool:
        """Apply a rig's chain by diff (see rig.plan): unload what's gone, load
        what's new, and re-order the rest. Effects common to the old and new rig
        keep their instance and are never reloaded, so switching between two
        rigs that share a reverb doesn't re-instantiate it.

        One _rechain() at the end, not one per mutation."""
        for instance in chain.remove:
            self._mh.remove_plugin(instance)
        removed = set(chain.remove)
        used = {e.instance for e in self._effects if e.instance not in removed}

        effects: list[Effect] = []
        ok = True
        for instance, wanted in chain.order:
            if instance is None:
                instance = self._free_instance(used)
                if instance is None:
                    logger.error("effects rack full; dropping %s", wanted.uri)
                    ok = False
                    continue
                if not self._mh.load_plugin(wanted.uri, instance):
                    logger.error("effects rack failed to load %s", wanted.uri)
                    ok = False
                    continue
                used.add(instance)
            for symbol, value in wanted.params.items():
                self._mh.set_param(instance, symbol, str(value))
            effects.append(Effect(instance, wanted.uri))

        self._effects = effects
        self._rechain()
        return ok

    @staticmethod
    def _free_instance(used: set[int]) -> int | None:
        """Lowest unused effect slot. Effects own 10..89 — 0-9 are instruments
        and 90+ is the master chain, so running past 89 would silently stomp the
        limiter rather than fail."""
        for instance in range(_BASE_INSTANCE, _MAX_INSTANCE + 1):
            if instance not in used:
                return instance
        return None

    def is_empty(self) -> bool:
        return not self._effects

    # ------------------------------------------------------------------
    # Ports (EngineManager routes instrument -> input_ports; rack owns -> DAC)
    # ------------------------------------------------------------------

    def input_ports(self) -> list[str]:
        """Audio inputs of the first effect — the instrument connects here."""
        if not self._effects:
            return []
        return self._audio_ports(self._effects[0].instance, is_output=False)

    def output_ports(self) -> list[str]:
        """Audio outputs of the last effect — connected to the sink."""
        if not self._effects:
            return []
        return self._audio_ports(self._effects[-1].instance, is_output=True)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _rechain(self) -> None:
        """Rebuild the internal chain + output->sink. Tears down the rack's prior
        wiring first so a stale 'old last effect -> sink' edge doesn't linger."""
        for src, dst in self._wired:
            self._jack.disconnect(src, dst)

        conns: list[tuple[str, str]] = []
        for a, b in zip(self._effects, self._effects[1:]):
            a_out = self._audio_ports(a.instance, is_output=True)
            b_in = self._audio_ports(b.instance, is_output=False)
            conns.extend(zip(a_out, b_in))
        conns.extend(zip(self.output_ports(), self._sink()))

        for src, dst in conns:
            self._jack.connect(src, dst)
        self._wired = conns

    def _audio_ports(self, instance: int, is_output: bool) -> list[str]:
        return self._jack.ports(
            client=f"effect_{instance}", type="audio", is_output=is_output
        )
