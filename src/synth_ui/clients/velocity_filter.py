"""VelocityFilter: the permanent head of the MIDI path, in mod-host.

    keyboards (a2j / ttymidi) -> [ VELOCITY ] -> active instrument:control

The mirror image of MasterChain. That one is the permanent *tail* of the audio
path and owns level; this is the permanent *head* of the MIDI path and owns
what velocity the instrument sees. Both are single-purpose stages the user never
builds or tears down, and both degrade to "not there" rather than to silence.

**Why a plugin and not a line of Python.** Notes do not pass through Python and
that is not negotiable (docs/engine-architecture.md). Flattening velocity means
rewriting a byte of every note-on *in the RT path*, so it has to happen in a
JACK client. x42's `midifilter.lv2` already does exactly this and ships in
`x42-plugins`, which the image installs — no new code on the audio thread, and
no new package.

**Why velocityscale rather than a "fixed velocity" plugin.** There isn't one.
midifilter's velocityscale maps the input range 1-127 onto [onmin, onmax], and
its note-on arithmetic is

    out = rint(vel * (onmax - onmin) / 126 + onmin - (onmax - onmin) / 126)

so the two settings this class needs both fall out exactly, with no rounding
slop at either end:

  - `onmin == onmax == V`  ->  the vel term vanishes, every note-on is exactly V;
  - `onmin = 1, onmax = 127` -> the expression reduces to `vel`, a true identity.

That second one is the reason "off" is expressed as parameters rather than as
mod-host `bypass`: bypass copies input buffers to output, which is defined for
audio and not something to bet a MIDI-only plugin on. A plugin that is loaded
but arithmetically transparent has no such question hanging over it.

Note-off velocity is left alone — the port defaults (offmin 0, offmax 127,
offset 0) are already an identity, and release velocity is a different musical
control from attack velocity.

**Once in the path, it stays.** Enabling loads the plugin and re-patches MIDI
once; disabling only sets the identity parameters back. Removing the plugin
would mean tearing down and rebuilding the keyboard->instrument leg every time
the setting changed, and the window where both the direct and the filtered edge
exist is one where every note sounds twice. A loaded midifilter costs a branch
and a byte-copy per event and no per-sample work at all, so leaving it there is
cheaper than the churn of taking it out.

Instance numbering: instruments 0-9, effects 10-89, master chain 90+, MIDI
filters 100+.
"""

from __future__ import annotations

import logging

from synth_ui.clients.jack_graph import JackGraph
from synth_ui.clients.lv2 import LV2World
from synth_ui.clients.mod_host_client import ModHostClient

logger = logging.getLogger(__name__)

VELOCITY_FILTER_URI = "http://gareus.org/oss/lv2/midifilter#velocityscale"

_INSTANCE = 100

# midifilter's atom port symbols (ttf.h), which mod-host exposes as
# effect_<instance>:<symbol>.
_MIDI_IN = "midiin"
_MIDI_OUT = "midiout"

# Note-on mapping controls. `onmin`'s port range starts at 1, so a fixed
# velocity is 1-127 — 0 would be a note-off anyway.
_MIN_VELOCITY = 1
_MAX_VELOCITY = 127


class VelocityFilter:
    def __init__(
        self,
        jack: JackGraph,
        mod_host: ModHostClient,
        lv2: LV2World | None = None,
        uri: str = VELOCITY_FILTER_URI,
        instance: int = _INSTANCE,
    ):
        self._jack = jack
        self._mh = mod_host
        self._lv2 = lv2 or LV2World()
        self._uri = uri
        self._instance = instance
        self._loaded = False
        # Keyboard sources already patched into the filter, so a re-attach after
        # a hotplug adds only the new ones.
        self._attached: set[str] = set()
        self._velocity = 0

    # ------------------------------------------------------------------
    # Availability
    # ------------------------------------------------------------------

    @property
    def available(self) -> bool:
        """Whether this board can do fixed velocity at all.

        False means x42-plugins isn't installed, which is a UI question (hide
        the control) rather than an error: every other thing the instrument does
        still works.
        """
        return self._lv2.has(self._uri)

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def velocity(self) -> int:
        """The fixed velocity in force, or 0 when notes play as struck."""
        return self._velocity

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def ensure(self) -> bool:
        """Load the filter if it isn't loaded. Idempotent; returns whether it's
        up. Callers treat False as "wire the keyboards straight to the
        instrument", which is the pre-existing behaviour."""
        if self._loaded:
            return True
        if not self.available:
            logger.warning("velocity filter unavailable: %s not installed", self._uri)
            return False
        if not self._mh.load_plugin(self._uri, self._instance):
            logger.error("velocity filter: failed to load %s", self._uri)
            return False
        self._loaded = True
        # Any channel: the point is what the keyboard sends, and a single
        # keyboard on channel 1 shouldn't have to be configured for.
        self._mh.set_param(self._instance, "channel", "0")
        self._apply()
        return True

    def teardown(self) -> None:
        """Drop the plugin. Used when mod-host has restarted underneath us and
        the bookkeeping no longer describes anything real."""
        if self._loaded:
            self._mh.remove_plugin(self._instance, missing_ok=True)
        self._loaded = False
        self._attached.clear()

    # ------------------------------------------------------------------
    # Ports
    # ------------------------------------------------------------------

    def input_port(self) -> str | None:
        return self._port(is_output=False)

    def output_port(self) -> str | None:
        """What the active instrument's MIDI input should be fed from. None when
        the filter isn't up — the caller's signal to use the keyboards direct."""
        return self._port(is_output=True)

    def _port(self, is_output: bool) -> str | None:
        if not self._loaded:
            return None
        wanted = _MIDI_OUT if is_output else _MIDI_IN
        ports = self._jack.ports(
            client=f"effect_{self._instance}", type="midi", is_output=is_output
        )
        # Match the symbol rather than taking ports[0]: midifilter registers both
        # an in and an out, and picking the wrong one silently wires MIDI
        # backwards, which looks exactly like a dead keyboard.
        for port in ports:
            if port.rsplit(":", 1)[-1] == wanted:
                return port
        return ports[0] if ports else None

    def attach(self, sources: list[str]) -> None:
        """Patch keyboard MIDI sources into the filter.

        Called on every wire, not once at load, for the same reason
        EngineManager re-patches the keyboards on every switch: a USB keyboard
        unplugged and plugged back in is a new JACK port.
        """
        midi_in = self.input_port()
        if midi_in is None:
            return
        for src in sources:
            if src in self._attached:
                continue
            if self._jack.connect(src, midi_in):
                self._attached.add(src)

    # ------------------------------------------------------------------
    # Setting
    # ------------------------------------------------------------------

    def set_fixed(self, velocity: int) -> None:
        """Send every note-on at `velocity`, or pass velocity through if 0.

        A value outside 1-127 with the filter loaded is clamped rather than
        rejected: the alternative is a rig that silently plays at the wrong
        dynamic, and the ports would clamp it anyway.
        """
        if velocity <= 0:
            self._velocity = 0
        else:
            self._velocity = max(_MIN_VELOCITY, min(_MAX_VELOCITY, velocity))
        if self._loaded:
            self._apply()

    def _apply(self) -> None:
        """onmin == onmax pins the output; 1/127 is the identity. See the module
        docstring for the arithmetic."""
        if self._velocity:
            low = high = float(self._velocity)
        else:
            low, high = float(_MIN_VELOCITY), float(_MAX_VELOCITY)
        self._mh.set_param(self._instance, "onmin", str(low))
        self._mh.set_param(self._instance, "onmax", str(high))
        self._mh.set_param(self._instance, "onoff", "0.0")
