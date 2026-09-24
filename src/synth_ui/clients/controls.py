"""What kind of control each plugin setting is, independent of how it's drawn.

    ControlPort (what lv2info says)  ->  Control (knob | toggle | choice)

A plugin describes its settings as ranges with properties — toggled, integer,
enumeration, logarithmic. This module turns that into a small vocabulary of
controls that a renderer can draw without knowing anything about LV2. The
touchscreen draws them with pygame; a browser can draw the same list from
`to_dict()`. Keeping the decision here, with no pygame, is what lets both show
the same plugin the same way.

Everything is derived from the plugin, not declared per plugin, so any plugin
gets a usable screen with no table to maintain.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from synth_ui.clients.lv2 import ControlPort

KNOB = "knob"
TOGGLE = "toggle"
CHOICE = "choice"

# An integer control with this few values is a set of choices, not a dial: a
# filter's 4 modes or an oversampling factor of 1..4 are picked, not swept.
MAX_INTEGER_CHOICES = 8

# A positive range this many times wider at the top than the bottom is drawn
# logarithmic whether or not the plugin says so. Calf declares every gain as a
# linear 1/64..64 multiplier with no log flag, which on a linear knob puts unity
# at 1.5% of the travel and everything from silence to +6 dB in the first 3%.
# Logarithmic, unity sits at twelve o'clock.
LOG_RANGE_RATIO = 1000.0


def _label(value: float, label: str) -> str:
    """Calf names its "off" setting " " — its own GUI shows an unlit LED —
    which reads as nothing at all on a button."""
    if label.strip():
        return label
    return "Off" if value == 0 else f"{value:g}"


@dataclass
class Control:
    symbol: str
    name: str
    kind: str
    minimum: float
    maximum: float
    default: float
    integer: bool = False
    logarithmic: bool = False
    # (value, label) in value order. For a choice, the only values it can take.
    # For a knob, names for particular values ("Off" at 0) shown as it passes.
    options: list[tuple[float, str]] = field(default_factory=list)

    def clamp(self, value: float) -> float:
        return max(self.minimum, min(self.maximum, value))

    # --- knob travel ------------------------------------------------------

    def to_ratio(self, value: float) -> float:
        """Where along its travel (0..1) this value sits."""
        if self.maximum == self.minimum:
            return 0.0
        value = self.clamp(value)
        if self.logarithmic:
            return math.log(value / self.minimum) / math.log(
                self.maximum / self.minimum
            )
        return (value - self.minimum) / (self.maximum - self.minimum)

    def from_ratio(self, ratio: float) -> float:
        """The value at this point (0..1) along its travel."""
        ratio = max(0.0, min(1.0, ratio))
        if self.logarithmic:
            value = self.minimum * (self.maximum / self.minimum) ** ratio
        else:
            value = self.minimum + ratio * (self.maximum - self.minimum)
        return self.clamp(float(round(value))) if self.integer else value

    # --- choices ----------------------------------------------------------

    def option_index(self, value: float) -> int:
        """The option nearest this value. Nearest, not exact: a stored value
        can be 2.0000001 after a round trip through a string."""
        if not self.options:
            return 0
        return min(
            range(len(self.options)), key=lambda i: abs(self.options[i][0] - value)
        )

    # --- reading out ------------------------------------------------------

    def format(self, value: float) -> str:
        """How a value reads.

        Toggles read as on/off rather than 1.0/0.0, named values by their name,
        integers lose their decimal point, and everything else gets enough
        precision to be useful without implying more than a finger can set.
        """
        if self.kind == TOGGLE:
            return "on" if value >= 0.5 else "off"
        if self.kind == CHOICE:
            return self.options[self.option_index(value)][1] if self.options else ""
        labels = dict(self.options)
        if round(value) in labels:
            # An enumerated control names its settings, and the name is the
            # whole point: "Blah" and "Reed" are choosable by ear, 5 and 23 are
            # not. Checked mid-drag too, so 5.4 still reads "Blah".
            return labels[round(value)]
        if self.integer:
            return f"{value:.0f}"
        if abs(value) >= 10000:
            return f"{value / 1000:.1f}k"
        if abs(value) >= 1000:
            return f"{value / 1000:.2f}k"
        if abs(self.maximum - self.minimum) <= 4:
            return f"{value:.2f}"
        return f"{value:.1f}"

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "kind": self.kind,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "default": self.default,
            "integer": self.integer,
            "logarithmic": self.logarithmic,
            "options": [[value, label] for value, label in self.options],
        }


def control_for(port: ControlPort) -> Control:
    """The control a single plugin setting should be drawn as."""
    base = dict(
        symbol=port.symbol,
        name=port.name,
        minimum=port.minimum,
        maximum=port.maximum,
        default=port.default,
        integer=port.integer,
    )
    labels = sorted((v, _label(v, name)) for v, name in port.scale_points.items())

    if port.toggled:
        return Control(kind=TOGGLE, **base)

    if port.enumeration and labels:
        # The plugin says its labelled values are the only valid ones.
        return Control(kind=CHOICE, options=labels, **base)

    span = port.maximum - port.minimum
    if port.integer and 0 < span < MAX_INTEGER_CHOICES:
        # Every value in range is an option; name the ones the plugin names.
        named = dict(labels)
        values = [float(v) for v in range(int(port.minimum), int(port.maximum) + 1)]
        return Control(
            kind=CHOICE,
            options=[(v, named.get(v, f"{v:.0f}")) for v in values],
            **base,
        )

    wide = port.minimum > 0 and port.maximum / port.minimum >= LOG_RANGE_RATIO
    return Control(
        kind=KNOB,
        # A log scale is undefined at or below zero. A plugin that claims one
        # on such a range gets a linear knob rather than a crash.
        logarithmic=(port.logarithmic or wide) and port.minimum > 0,
        options=labels,
        **base,
    )


def controls_for(ports: list[ControlPort]) -> list[Control]:
    """Every setting as a control, in the plugin's own order — which is how
    its author grouped them, and usually better than anything we'd sort by."""
    return [control_for(p) for p in ports if not p.hidden]
