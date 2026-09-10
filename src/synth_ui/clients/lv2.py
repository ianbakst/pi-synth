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
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

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
    # SoundFont player, replacing the fluidsynth process + its TCP shell.
    # TODO: file_property unconfirmed — run `verify_voices --inspect` on the
    # board once Fluida is built. Without it the plugin loads but plays no
    # soundfont, which validation reports as "no file property for this plugin"
    # rather than failing silently.
    "fluida": PluginSpec(uri="https://github.com/brummer10/Fluida.lv2"),
}

# The generic engine name for "any LV2 instrument, URI given by the voice".
GENERIC_ENGINE = "modhost"

# Every engine name that resolves to an LV2 plugin in mod-host.
MODHOST_ENGINES = frozenset({GENERIC_ENGINE, *PLUGIN_SPECS})


def spec_for(
    engine: str, uri: str = "", file_property: str = ""
) -> PluginSpec | None:
    """Resolve an engine name (+ optional manifest overrides) to a PluginSpec.

    Returns None for engines that aren't mod-host-hosted (fluidsynth,
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
