"""LV2 plugin knowledge: which plugin a voice means, and which are installed.

Two separate concerns, deliberately in one small module with no heavy imports so
both `voice.py` (validation) and `engine.py` (loading) can use it:

  - `PluginSpec` / `spec_for()` — resolve a voice to an LV2 URI plus, if the
    plugin loads an instrument file, the LV2 *patch property* that file is set
    through. Patch properties are atom-based and only reachable via mod-host
    `patch_set`; `param_set` silently no-ops on them (this was the original
    "sfizz loads but stays silent" bug).
  - `LV2World` — what `lv2ls` says is actually installed on this machine, so the
    UI can grey out a voice whose plugin was never built instead of failing at
    switch time.

Adding an LV2 instrument should be a `voices.json` edit and nothing else: give
the voice `"engine": "modhost"` and its `uri`. `PLUGIN_SPECS` below exists only
for the two engines that predate that (`sfizz`, `dexed`), whose manifests name
the engine rather than the URI.
"""

from __future__ import annotations

import logging
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PluginSpec:
    uri: str
    # LV2 patch-property URI for this plugin's instrument file (SFZ, SF2, .syx).
    # Empty = the plugin takes no instrument file (a synth like mda JX10) or the
    # property hasn't been confirmed on hardware yet.
    file_property: str = ""


# Legacy engine names -> plugin. New voices carry `uri` in the manifest instead
# of adding entries here.
PLUGIN_SPECS: dict[str, PluginSpec] = {
    # Confirmed on hardware (jack_lsp / mod-host logs).
    "sfizz": PluginSpec(
        uri="http://sfztools.github.io/sfizz",
        file_property="http://sfztools.github.io/sfizz:sfzfile",
    ),
    # TODO(dexed): not built in the image yet, and its .syx patch-property URI is
    # unconfirmed. Empty file_property => the plugin loads but no cartridge is
    # set. `verify-voices --inspect` on the board is how to fill this in.
    "dexed": PluginSpec(uri="https://asb2m10.github.io/dexed"),
    # SoundFont player. Confirmed on hardware from its bundle turtle: the
    # soundfont is a patch:writable atom:Path (mod:fileTypes "sf2").
    # NOTE: Fluida exposes no bank/program property — instrument selection
    # inside the font is MIDI Program Change only. See docs/voice-library.md.
    "fluida": PluginSpec(
        uri="https://github.com/brummer10/Fluida.lv2",
        file_property="https://github.com/brummer10/Fluida.lv2#soundfont",
    ),
}

# The generic engine name for "any LV2 instrument, URI given by the voice".
GENERIC_ENGINE = "modhost"

# Every engine name that resolves to an LV2 plugin in mod-host.
MODHOST_ENGINES = frozenset({GENERIC_ENGINE, *PLUGIN_SPECS})


def spec_for(
    engine: str, uri: str = "", file_property: str = ""
) -> PluginSpec | None:
    """Resolve an engine name (+ optional manifest overrides) to a PluginSpec.

    Returns None for engines that aren't mod-host-hosted (pianoteq,
    pianoteq) and for a generic voice that forgot its `uri`. Manifest values win
    over the built-in spec, so a voice can pin a URI or supply a file property
    that isn't codified here yet.
    """
    base = PLUGIN_SPECS.get(engine)
    if base is None and engine != GENERIC_ENGINE:
        return None
    resolved_uri = uri or (base.uri if base else "")
    if not resolved_uri:
        return None
    return PluginSpec(
        uri=resolved_uri,
        file_property=file_property or (base.file_property if base else ""),
    )


# Returns (returncode, stdout). Injectable so tests never shell out.
Runner = Callable[[list[str]], tuple[int, str]]


def _subprocess_runner(cmd: list[str]) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return p.returncode, p.stdout
    except FileNotFoundError:
        return 127, ""
    except subprocess.TimeoutExpired:
        logger.error("timed out: %s", " ".join(cmd))
        return 1, ""


class LV2World:
    """The set of LV2 plugin URIs installed here, per `lv2ls`.

    `has()` is deliberately optimistic when the world can't be read at all (no
    `lv2ls` — e.g. a dev machine): an absent tool must not grey out the entire
    voice library. Only a world we successfully enumerated can rule a URI out.
    """

    def __init__(self, runner: Runner | None = None):
        self._run = runner or _subprocess_runner
        self._uris: set[str] | None = None
        self._readable = True

    def uris(self) -> set[str]:
        if self._uris is None:
            self.refresh()
        return set(self._uris or ())

    def refresh(self) -> None:
        rc, out = self._run(["lv2ls"])
        if rc != 0:
            logger.warning("lv2ls unavailable (rc=%d) — not filtering by URI", rc)
            self._uris, self._readable = set(), False
            return
        self._uris = {line.strip() for line in out.splitlines() if line.strip()}
        self._readable = True

    @property
    def readable(self) -> bool:
        """False if `lv2ls` couldn't be run — availability is then unknowable."""
        if self._uris is None:
            self.refresh()
        return self._readable

    def has(self, uri: str) -> bool:
        installed = self.uris()
        if not self._readable:
            return True
        return uri in installed


@dataclass
class ControlPort:
    """One editable knob on a plugin.

    Discovered from `lv2info` rather than declared in our own tables. Guessing
    port symbols by hand has been a repeated source of silent failures here, and
    a plugin is the authority on its own controls.
    """

    symbol: str
    name: str
    minimum: float
    maximum: float
    default: float
    toggled: bool = False
    integer: bool = False
    # value -> label, for ports that enumerate their settings (a filter's mode,
    # an oscillator's wavetable). Without them the control reads "Osc1 Wave 5",
    # which is not something anyone can choose a sound by.
    scale_points: dict[float, str] = field(default_factory=dict)
    # Ports a performer has no use for: mostly Calf's level meters and graph
    # outputs, which are inputs in name only.
    hidden: bool = False

    def clamp(self, value: float) -> float:
        return max(self.minimum, min(self.maximum, value))


_PORT_BLOCK_RE = re.compile(r"Symbol:\s*(\S+)")
_PORT_NAME_RE = re.compile(r"Name:\s*(.+)")
_PORT_MIN_RE = re.compile(r"Minimum:\s*(-?[\d.eE+]+)")
_PORT_MAX_RE = re.compile(r"Maximum:\s*(-?[\d.eE+]+)")
_PORT_DEF_RE = re.compile(r"Default:\s*(-?[\d.eE+]+)")
_SCALE_POINT_RE = re.compile(r'^\s*(-?[\d.eE+]+) = "(.*)"\s*$', re.M)

# One record per port, split on lv2info's own "Port N:" heading rather than on
# blank lines. A port that lists Scale Points has a blank line *inside* it,
# between the points and its Symbol — so splitting on blank lines tore those
# records in half and dropped every one of them: the half carrying the symbol
# had no "ControlPort" line left in it to match on. That silently hid every
# enumerated control on the board — a wavetable selector, a filter's mode, a
# delay's timing — from the params screen.
_PORT_SPLIT_RE = re.compile(r"^[ \t]*Port \d+:[ \t]*$", re.M)

# Calf exposes per-band analyser and meter ports as control inputs. They are not
# knobs; showing them buries the three controls that matter under twenty that
# don't.
_HIDDEN_SUFFIXES = ("_vu", "meter", "_level_out", "analyzer", "_graph", "bypass")


def port_blocks(info: str) -> list[str]:
    """One text record per port in `lv2info` output, in the plugin's order.

    Shared with `tools/verify_voices`, which needs the same records under a
    different filter — it lists every control port to codify, while the UI wants
    only the ones it can draw a slider for. Splitting them was the part both got
    wrong, so it lives here once.
    """
    # [1:] drops the plugin's own header, which precedes the first port.
    return _PORT_SPLIT_RE.split(info)[1:]


def parse_control_ports(info: str) -> list[ControlPort]:
    """Input control ports from `lv2info` output, in the plugin's own order."""
    ports: list[ControlPort] = []
    for block in port_blocks(info):
        if "ControlPort" not in block or "InputPort" not in block:
            continue
        symbol = _PORT_BLOCK_RE.search(block)
        if not symbol:
            continue
        name = _PORT_NAME_RE.search(block)
        minimum = _PORT_MIN_RE.search(block)
        maximum = _PORT_MAX_RE.search(block)
        default = _PORT_DEF_RE.search(block)
        # A port with no declared range can't drive a slider; skip rather than
        # invent 0..1, which would send out-of-range values the plugin rejects.
        if not (minimum and maximum):
            continue
        low, high = float(minimum.group(1)), float(maximum.group(1))
        ports.append(
            ControlPort(
                symbol=symbol.group(1),
                name=name.group(1).strip() if name else symbol.group(1),
                minimum=low,
                maximum=high,
                default=float(default.group(1)) if default else low,
                # lv2info prints properties as URIs ("...lv2core#toggled"),
                # lower-case, not as the capitalised words the summary lines use.
                toggled="#toggled" in block.lower(),
                integer="#integer" in block.lower(),
                hidden=any(h in symbol.group(1).lower() for h in _HIDDEN_SUFFIXES),
                scale_points={
                    float(value): label
                    for value, label in _SCALE_POINT_RE.findall(block)
                },
            )
        )
    return ports


class PortDefaults:
    """Each plugin's control defaults, read once and kept.

    Reading them means running `lv2info`, a subprocess — and the callers need
    them on the rig-switch path, where a plugin scan per switch would be felt.
    A plugin's ports don't change under us, so one read per URI is enough.

    Shared by the effects rack and the instrument slots: both have to be able to
    say "and everything else goes back to what the plugin says", and neither
    should own a private cache of it.
    """

    def __init__(self, read: Callable[[str], list[ControlPort]] | None = None):
        self._read = read or control_ports
        self._cache: dict[str, dict[str, float]] = {}

    def for_uri(self, uri: str) -> dict[str, float]:
        """symbol -> default. Empty if the plugin can't be read, which leaves
        callers filling in nothing rather than writing invented values."""
        if uri not in self._cache:
            self._cache[uri] = {p.symbol: p.default for p in self._read(uri)}
        return self._cache[uri]

    def complete(self, uri: str, params: dict[str, float]) -> dict[str, float]:
        """`params` with every unmentioned control at the plugin's default.

        A complete set is what makes a saved chain self-describing, and what
        stops one rig's settings hanging over into the next: writing all of it
        leaves nothing behind to leak.
        """
        return {**self.for_uri(uri), **params}


def control_ports(uri: str, runner: Runner | None = None) -> list[ControlPort]:
    """The editable controls of an installed plugin. Empty if lv2info can't be
    run — the caller shows "no adjustable parameters" rather than failing."""
    run = runner or _subprocess_runner
    code, out = run(["lv2info", uri])
    if code != 0:
        logger.warning("lv2info failed for %s", uri)
        return []
    return [p for p in parse_control_ports(out) if not p.hidden]
