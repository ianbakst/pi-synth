"""
Hardware and application configuration.

Hardware settings are loaded from config.toml (looked up via SYNTH_CONFIG env
var, then <project-root>/config.toml). Path settings can be overridden with
SYNTH_SOUNDFONT_DIR and SYNTH_STATE_FILE env vars.
"""

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AudioConfig:
    device: str
    period_size: int
    periods: int
    sample_rate: int
    default_gain: float
    max_gain: float
    cores: str
    rt_priority: int
    fluidsynth_port: int


@dataclass(frozen=True)
class DisplayConfig:
    width: int
    height: int
    framebuffer: str
    touch_device: str


@dataclass(frozen=True)
class PathsConfig:
    soundfont_dir: str
    state_file: str


@dataclass(frozen=True)
class Config:
    audio: AudioConfig
    display: DisplayConfig
    paths: PathsConfig


def _find_alsa_device(card: str) -> str:
    """Resolve a human-readable card name to hw:N by scanning /proc/asound/cards."""
    try:
        text = Path("/proc/asound/cards").read_text()
    except OSError as e:
        raise RuntimeError(f"Cannot read /proc/asound/cards: {e}") from e

    for line in text.splitlines():
        m = re.match(r'^\s*(\d+)\s+\[([^\]]+)\]:\s+\S+\s+-\s+(.+)', line)
        if m and card.lower() in (m.group(2) + m.group(3)).lower():
            return f"hw:{m.group(1)}"

    raise RuntimeError(
        f"No ALSA card matching {card!r} found.\nAvailable cards:\n{text.strip()}"
    )


def _find_config_file() -> Path | None:
    if env := os.environ.get("SYNTH_CONFIG"):
        return Path(env)
    candidate = Path(__file__).parent.parent / "config.toml"
    return candidate if candidate.exists() else None


def _load() -> Config:
    data: dict = {}
    if path := _find_config_file():
        with open(path, "rb") as f:
            data = tomllib.load(f)

    a = data.get("audio", {})
    d = data.get("display", {})

    return Config(
        audio=AudioConfig(
            device=a.get("device") or (
                _find_alsa_device(a.get("card", "hifiberry"))
                if Path("/sys/firmware/devicetree/base/model").exists()
                else "stub"
            ),
            period_size=a.get("period_size", 128),
            periods=a.get("periods", 2),
            sample_rate=a.get("sample_rate", 48000),
            default_gain=a.get("default_gain", 2.0),
            max_gain=a.get("max_gain", 5.0),
            cores=a.get("cores", "2,3"),
            rt_priority=a.get("rt_priority", 80),
            fluidsynth_port=a.get("fluidsynth_port", 9800),
        ),
        display=DisplayConfig(
            width=d.get("width", 800),
            height=d.get("height", 480),
            framebuffer=d.get("framebuffer", "/dev/fb0"),
            touch_device=d.get("touch_device", "/dev/input/event4"),
        ),
        paths=PathsConfig(
            soundfont_dir=os.path.expanduser(
                os.environ.get("SYNTH_SOUNDFONT_DIR", "~/soundfonts")
            ),
            state_file=os.path.expanduser(
                os.environ.get("SYNTH_STATE_FILE", "~/.midi-instrument-state")
            ),
        ),
    )


cfg = _load()
