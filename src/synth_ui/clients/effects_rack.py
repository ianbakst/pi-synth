"""
EffectsRack: an ordered chain of LV2 effects hosted in the effects mod-host.

A dedicated mod-host instance (port 5556, core 3 — see systemd/mod-host-fx.service)
hosts only effects, so instrument DSP (core 2) and effect DSP (core 3) don't
share a thread. The rack is persistent across instrument switches; Python only
patches the JACK graph (add/remove/param + wiring), never touching audio itself.

Signal flow it owns:  fx[0].out -> fx[1].in -> ... -> fx[N].out -> system:playback
The instrument's audio -> fx[0].in leg is owned by EngineManager (it moves on an
instrument switch); the rack's output -> DAC leg never moves.

Effects live at mod-host instances 10+ (instruments use 0-9).

PORT DISCOVERY IS A HARDWARE KNOB: this assumes mod-host exposes each plugin
instance under a per-instance JACK client named "effect_<instance>". Confirm with
`jack_lsp` on the Pi and adjust _audio_ports if mod-host names them differently.
"""

import logging
from dataclasses import dataclass

from synth_ui.clients.jack_graph import JackGraph
from synth_ui.clients.mod_host_client import ModHostClient

logger = logging.getLogger(__name__)

_BASE_INSTANCE = 10


@dataclass
class Effect:
    instance: int
    uri: str


class EffectsRack:
    def __init__(self, jack: JackGraph, mod_host: ModHostClient):
        self._jack = jack
        self._mh = mod_host
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
        """Audio outputs of the last effect — connected to the DAC."""
        if not self._effects:
            return []
        return self._audio_ports(self._effects[-1].instance, is_output=True)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _rechain(self) -> None:
        """Rebuild the internal chain + output->DAC. Tears down the rack's prior
        wiring first so a stale 'old last effect -> DAC' edge doesn't linger."""
        for src, dst in self._wired:
            self._jack.disconnect(src, dst)

        conns: list[tuple[str, str]] = []
        for a, b in zip(self._effects, self._effects[1:]):
            a_out = self._audio_ports(a.instance, is_output=True)
            b_in = self._audio_ports(b.instance, is_output=False)
            conns.extend(zip(a_out, b_in))
        conns.extend(zip(self.output_ports(), self._jack.dac_sinks()))

        for src, dst in conns:
            self._jack.connect(src, dst)
        self._wired = conns

    def _audio_ports(self, instance: int, is_output: bool) -> list[str]:
        return self._jack.ports(
            client=f"effect_{instance}", type="audio", is_output=is_output
        )
