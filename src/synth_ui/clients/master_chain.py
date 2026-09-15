"""MasterChain: the permanent tail of the signal path, in mod-host.

    instrument -> [ effects rack ] -> [ MASTER: trim -> limit ] -> system:playback

Unlike the effects rack (which the user builds and tears down, and which will
become part of a saved rig), the master chain is always there and never moves.
It exists for two reasons:

  - **Level matching.** Instruments differ wildly in output level; switching from
    a sampled piano to a B3 shouldn't jump. Each voice carries a measured
    `gain_trim_db` (see tools/calibrate_levels.py) which is applied here, summed
    with the user's volume setting. One control, one place.
  - **Peak safety.** A limiter last in the path means a hot instrument or a
    heavy-handed effect can't clip the DAC while levels are being dialled in.

**Why this is cheap enough to leave running.** What causes dropouts on this box
is per-period *variance*, not average load (see docs/engine-architecture.md —
~180 xruns/min at only 40-60% CPU). A gain stage and a limiter cost the same
work every period: no disk reads, no allocation, no worker threads. That makes
them the best-behaved things in the graph, unlike a sampler whose cost spikes on
first-touch sample reads. Keep it to gain/limit/EQ — a reverb belongs in the
per-rig rack, not here.

Instance numbering: instruments 0-9, effects 10+, master 90+.

The stage list is configuration (config.MASTER_CHAIN), not hardcoded, because
the plugin URIs and control symbols still need confirming on hardware — run
`python3 -m synth_ui.tools.verify_voices --list`. A stage whose plugin isn't
installed is skipped rather than fatal: the chain degrades to whatever loaded,
and to a direct instrument->DAC path if nothing did. Silence is never the
failure mode.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from synth_ui.clients.jack_graph import JackGraph
from synth_ui.clients.lv2 import LV2World
from synth_ui.clients.mod_host_client import ModHostClient

logger = logging.getLogger(__name__)

_BASE_INSTANCE = 90

# Below this, treat the level as silence rather than computing a linear gain
# that underflows to something meaningless.
_MIN_DB = -60.0


@dataclass(frozen=True)
class MasterStage:
    """One plugin in the master chain."""

    uri: str
    # Control-port values applied once at load (e.g. the limiter's ceiling).
    params: dict[str, float] = field(default_factory=dict)
    # Pre-limiter gain: the active voice's measured level offset. Putting trim
    # before the limiter is what makes a hot voice actually get limited.
    trim_symbol: str = ""
    # Post-limiter gain: the user's volume. Kept separate so turning the volume
    # down doesn't change how much the limiter is working. If a plugin only
    # offers one gain control, leave this empty and volume is summed onto
    # trim_symbol instead.
    volume_symbol: str = ""
    # Whether those controls take decibels or a linear multiplier. Calf's
    # level_in/level_out are linear — getting this wrong is a huge level error,
    # so it's declared rather than assumed.
    unit: str = "db"
    # Control-port bounds, when the port has them. A value outside the port's
    # range isn't just clipped, it's invalid — Calf's gains bottom out at
    # 1/64 (-36 dB), so "silence" has to clamp to that rather than send 0.
    minimum: float | None = None
    maximum: float | None = None


def _to_control_value(db: float, stage: MasterStage) -> float:
    if stage.unit == "db":
        value = db
    elif db <= _MIN_DB:
        value = 0.0
    else:
        value = 10.0 ** (db / 20.0)
    if stage.minimum is not None:
        value = max(stage.minimum, value)
    if stage.maximum is not None:
        value = min(stage.maximum, value)
    return value


class MasterChain:
    def __init__(
        self,
        jack: JackGraph,
        mod_host: ModHostClient,
        stages: list[MasterStage],
        lv2: LV2World | None = None,
    ):
        self._jack = jack
        self._mh = mod_host
        self._stages = list(stages)
        self._lv2 = lv2 or LV2World()
        # Stages that actually loaded, as (instance, stage).
        self._loaded: list[tuple[int, MasterStage]] = []
        self._wired: list[tuple[str, str]] = []
        self._volume_db = 0.0
        self._trim_db = 0.0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def ensure(self) -> bool:
        """Load and wire the chain. Idempotent — safe to call on every mod-host
        start. Returns True if any stage is up."""
        if self._loaded:
            return True

        for offset, stage in enumerate(self._stages):
            if not self._lv2.has(stage.uri):
                logger.warning("master chain: plugin not installed: %s", stage.uri)
                continue
            instance = _BASE_INSTANCE + offset
            if not self._mh.load_plugin(stage.uri, instance):
                logger.error("master chain: failed to load %s", stage.uri)
                continue
            for symbol, value in stage.params.items():
                self._mh.set_param(instance, symbol, str(value))
            self._loaded.append((instance, stage))

        if not self._loaded:
            logger.warning("master chain empty — routing instruments straight to DAC")
            return False

        self._rechain()
        self._apply_gain()
        return True

    def teardown(self) -> None:
        for src, dst in self._wired:
            self._jack.disconnect(src, dst)
        self._wired = []
        for instance, _ in self._loaded:
            self._mh.remove_plugin(instance)
        self._loaded = []

    def is_ready(self) -> bool:
        return bool(self._loaded)

    # ------------------------------------------------------------------
    # Ports (EngineManager routes the instrument or rack into input_ports)
    # ------------------------------------------------------------------

    def input_ports(self) -> list[str]:
        """Audio inputs of the first stage. Empty if no stage loaded, which is
        the caller's signal to route straight to the DAC instead."""
        if not self._loaded:
            return []
        return self._audio_ports(self._loaded[0][0], is_output=False)

    def output_ports(self) -> list[str]:
        if not self._loaded:
            return []
        return self._audio_ports(self._loaded[-1][0], is_output=True)

    # ------------------------------------------------------------------
    # Level
    # ------------------------------------------------------------------

    def set_volume_db(self, db: float) -> None:
        """The user's volume control."""
        self._volume_db = db
        self._apply_gain()

    def set_trim_db(self, db: float) -> None:
        """The active voice's measured level offset (Voice.gain_trim_db)."""
        self._trim_db = db
        self._apply_gain()

    @property
    def level_db(self) -> float:
        return self._volume_db + self._trim_db

    def _apply_gain(self) -> None:
        """Trim goes pre-limiter, volume post-limiter — see MasterStage. When a
        stage only offers one gain control, the two collapse onto it."""
        for instance, stage in self._loaded:
            if not stage.trim_symbol:
                continue
            if stage.volume_symbol:
                trim_db, volume_db = self._trim_db, self._volume_db
            else:
                trim_db, volume_db = self.level_db, None

            self._mh.set_param(
                instance, stage.trim_symbol, str(_to_control_value(trim_db, stage))
            )
            if volume_db is not None:
                self._mh.set_param(
                    instance,
                    stage.volume_symbol,
                    str(_to_control_value(volume_db, stage)),
                )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _rechain(self) -> None:
        """stage[0] -> stage[1] -> ... -> DAC. This leg never moves once built."""
        for src, dst in self._wired:
            self._jack.disconnect(src, dst)

        conns: list[tuple[str, str]] = []
        for (a, _), (b, _) in zip(self._loaded, self._loaded[1:]):
            conns.extend(
                zip(
                    self._audio_ports(a, is_output=True),
                    self._audio_ports(b, is_output=False),
                )
            )
        conns.extend(zip(self.output_ports(), self._jack.dac_sinks()))

        for src, dst in conns:
            self._jack.connect(src, dst)
        self._wired = conns

    def _audio_ports(self, instance: int, is_output: bool) -> list[str]:
        return self._jack.ports(
            client=f"effect_{instance}", type="audio", is_output=is_output
        )


# The sink an upstream stage should feed: the master chain if it's up, the DAC
# if it isn't. Used by EffectsRack and EngineManager so neither needs to know
# whether a master chain exists.
def sink_for(master: MasterChain, jack: JackGraph) -> Callable[[], list[str]]:
    def sink() -> list[str]:
        return master.input_ports() or jack.dac_sinks()

    return sink
