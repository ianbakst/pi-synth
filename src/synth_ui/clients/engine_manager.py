"""
EngineManager: dispatches voice loading to the correct audio engine.

FluidSynth  → TCP shell on port 9800 (existing)
sfizz       → mod-host LV2 socket on port 5555
Dexed       → mod-host LV2 socket on port 5555
setBfree    → standalone JACK client (started by engine-manager.sh)
Pianoteq    → standalone JACK client (started by engine-manager.sh)

Engine switching (different engine type) is delegated to engine-manager.sh,
which stops the old process, starts the new one, and reconnects MIDI.
The Python layer never starts or kills audio processes directly.
"""

import logging
import subprocess

from synth_ui.clients.constants import DEFAULT_PORT, FIRST_TIMEOUT, LOAD_TIMEOUT, LOCALHOST, SILENCE_TIMEOUT
from synth_ui.clients.mod_host_client import ModHostClient
from synth_ui.clients.synth_client import FluidSynthController, Preset
from synth_ui.clients.voice import Voice

logger = logging.getLogger(__name__)

# LV2 plugin URIs — verify with `lv2ls` on the Pi after installation
_SFIZZ_URI = "http://sfztools.github.io/sfizz"
_DEXED_URI = "https://asb2m10.github.io/dexed"

# mod-host instance number used for the instrument plugin
_INSTRUMENT_INSTANCE = 0


class EngineManager:
    """High-level voice switcher that abstracts over all supported engines."""

    def __init__(
        self,
        engine_manager_script: str,
        fluidsynth_host: str = LOCALHOST,
        fluidsynth_port: int = DEFAULT_PORT,
        mod_host_host: str = LOCALHOST,
        mod_host_port: int = 5555,
    ):
        self._script = engine_manager_script
        self._fluidsynth = FluidSynthController(
            host=fluidsynth_host,
            port=fluidsynth_port,
            timeout=FIRST_TIMEOUT,
            silence_timeout=SILENCE_TIMEOUT,
            load_timeout=LOAD_TIMEOUT,
        )
        self._mod_host = ModHostClient(host=mod_host_host, port=mod_host_port)
        self._current_engine: str | None = None

    # ------------------------------------------------------------------
    # Public API (mirrors FluidSynthController where possible)
    # ------------------------------------------------------------------

    def load_voice(self, voice: Voice) -> bool:
        """Switch to the given voice, starting a new engine if necessary."""
        if voice.engine != self._current_engine:
            ok = self._switch_engine(voice)
            if not ok:
                return False
        else:
            ok = self._load_on_current(voice)
        return ok

    def list_presets(self) -> list[Preset]:
        """Return presets for the active voice. Non-empty only for FluidSynth."""
        if self._current_engine == "fluidsynth":
            return self._fluidsynth.list_presets()
        return []

    def select_preset(self, channel: int, sfont_id: int, bank: int, prog: int) -> None:
        if self._current_engine == "fluidsynth":
            self._fluidsynth.select_preset(channel, sfont_id, bank, prog)

    def set_gain(self, gain: float) -> None:
        if self._current_engine == "fluidsynth":
            self._fluidsynth.set_gain(gain)
        # TODO: gain for other engines via JACK volume or plugin params

    def is_connected(self) -> bool:
        if self._current_engine == "fluidsynth":
            return self._fluidsynth.is_connected()
        if self._current_engine in ("sfizz", "dexed"):
            return self._mod_host.is_connected()
        if self._current_engine in ("setbfree", "pianoteq"):
            return True  # no socket to query; assume running if switch succeeded
        return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _switch_engine(self, voice: Voice) -> bool:
        """Call engine-manager.sh to stop old engine and start new one."""
        args = [self._script, voice.engine]
        if voice.path:
            args.append(voice.path)
        try:
            result = subprocess.run(args, timeout=15)
            if result.returncode != 0:
                logger.error("engine-manager.sh exited %d", result.returncode)
                return False
        except FileNotFoundError:
            logger.warning("engine-manager.sh not found at %s — skipping engine switch", self._script)
        except subprocess.TimeoutExpired:
            logger.error("engine-manager.sh timed out")
            return False

        self._current_engine = voice.engine

        # For mod-host engines, load the plugin now
        if voice.engine in ("sfizz", "dexed"):
            return self._load_mod_host_plugin(voice)

        return True

    def _load_on_current(self, voice: Voice) -> bool:
        """Load a new voice on the already-running engine (no engine restart)."""
        match voice.engine:
            case "fluidsynth":
                return self._fluidsynth.load_soundfont(voice.path)
            case "sfizz" | "dexed":
                return self._reload_mod_host_file(voice)
            case "setbfree" | "pianoteq":
                return True  # no reload for these; they're single-voice engines
        return False

    def _load_mod_host_plugin(self, voice: Voice) -> bool:
        uri = _SFIZZ_URI if voice.engine == "sfizz" else _DEXED_URI
        self._mod_host.remove_plugin(_INSTRUMENT_INSTANCE)
        if not self._mod_host.load_plugin(uri, _INSTRUMENT_INSTANCE):
            logger.error("Failed to load %s plugin", voice.engine)
            return False
        return self._reload_mod_host_file(voice)

    def _reload_mod_host_file(self, voice: Voice) -> bool:
        if not voice.path:
            return True
        symbol = "sfz_file" if voice.engine == "sfizz" else "sysex_file"
        return self._mod_host.set_param(_INSTRUMENT_INSTANCE, symbol, f"'{voice.path}'")
