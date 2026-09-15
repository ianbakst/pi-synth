import getpass
import glob
import os

from synth_ui.clients.lv2 import LV2World
from synth_ui.clients.soundfont import discover
from synth_ui.clients.trims import apply_trims, read_trims
from synth_ui.clients.voice import Voice, annotate
from synth_ui.clients.voice import read_voices_manifest as _read_manifest

# One LV2 world for the process: it caches `lv2ls` output, so refreshing the
# voice list (e.g. after a USB copy) doesn't re-scan the plugin world each time.
_lv2 = LV2World()
# Public alias: the effects catalogue needs the same cached plugin list the
# voice library uses, and re-scanning lv2ls per screen is what made the old
# library reload cost 2s of CPU per second of wall time.
lv2_world = _lv2


def scan_soundfonts(directory: str) -> list[str]:
    fonts = []
    for ext in ("*.sf2", "*.SF2", "*.sf3", "*.SF3"):
        fonts.extend(glob.glob(os.path.join(directory, "**", ext), recursive=True))
    fonts.sort(key=lambda f: os.path.basename(f).lower())
    return fonts


def soundfont_engine() -> str:
    """What plays a .sf2 here: the Fluida LV2 plugin, in mod-host.

    Was a runtime choice between Fluida and a fluidsynth process, so one library
    worked on a board built either way. That fallback is gone: every image
    builds Fluida (os-image 02-audio-stack), and carrying a second soundfont
    engine meant a second audio process, a second RT client, and a TCP control
    path that nothing else used.
    """
    return "fluida"


def load_voices(
    manifest_path: str, soundfont_dir: str, trims_path: str | None = None
) -> list[Voice]:
    """The instrument library: curated manifest entries plus every SoundFont in
    the soundfont directory.

    The directory is a manifest in its own right — dropping a .sf2 in adds a
    voice, deleting it removes one, which is how a split GM set (128 files) and
    USB-imported fonts get used without hand-writing JSON for each. Manifest
    entries win on conflict, since those carry the settings a bare file can't:
    level trim, presets, params.

    Every voice is annotated with why it can't be used here (missing file,
    plugin never built), so the UI shows that up front instead of the user
    discovering it by tapping — see clients/voice.py validate().
    """
    voices = _read_manifest(manifest_path)
    claimed = {os.path.realpath(v.path) for v in voices if v.path}
    names = {v.name for v in voices}

    for voice in discover(soundfont_dir, engine=soundfont_engine()):
        if os.path.realpath(voice.path) in claimed:
            continue
        # Rigs reference a voice by name, so a collision would make one of them
        # unreachable. Disambiguate rather than silently dropping it.
        if voice.name in names:
            voice.name = f"{voice.name} ({os.path.basename(voice.path)})"
        names.add(voice.name)
        voices.append(voice)

    # Measured levels last, so they override whatever the manifest declared.
    if trims_path:
        apply_trims(voices, read_trims(trims_path))
    return annotate(voices, has_uri=_lv2.has)


def scan_usb_soundfonts(exclude_dir: str) -> list[str]:
    """Find SF2/SF3 files on mounted USB drives, excluding the local library."""
    exclude_real = os.path.realpath(exclude_dir)
    search_roots = [
        f"/media/{getpass.getuser()}",
        "/media",
        "/mnt",
    ]
    seen: set[str] = set()
    fonts: list[str] = []
    for root in search_roots:
        if not os.path.isdir(root):
            continue
        for ext in ("*.sf2", "*.SF2", "*.sf3", "*.SF3"):
            for path in glob.glob(os.path.join(root, "**", ext), recursive=True):
                real = os.path.realpath(path)
                if real in seen:
                    continue
                if real.startswith(exclude_real):
                    continue
                seen.add(real)
                fonts.append(path)
    fonts.sort(key=lambda f: os.path.basename(f).lower())
    return fonts


def display_name(path: str) -> str:
    name = os.path.basename(path)
    name = os.path.splitext(name)[0]
    name = name.replace("_", " ").replace("-", " ")
    while "  " in name:
        name = name.replace("  ", " ")
    return name.strip()


def file_size_str(path: str) -> str:
    size = os.path.getsize(path)
    if size < 1024 * 1024:
        return f"{size // 1024} KB"
    return f"{size / (1024 * 1024):.1f} MB"
