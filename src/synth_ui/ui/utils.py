import getpass
import glob
import os

from synth_ui.clients.voice import Voice, read_voices_manifest as _read_manifest


def scan_soundfonts(directory: str) -> list[str]:
    fonts = []
    for ext in ("*.sf2", "*.SF2", "*.sf3", "*.SF3"):
        fonts.extend(glob.glob(os.path.join(directory, "**", ext), recursive=True))
    fonts.sort(key=lambda f: os.path.basename(f).lower())
    return fonts


def load_voices(manifest_path: str, soundfont_dir: str) -> list[Voice]:
    """Return voices from manifest, falling back to soundfont directory scan."""
    voices = _read_manifest(manifest_path)
    if voices:
        return voices
    # Fallback: treat every SF2/SF3 in soundfont_dir as a FluidSynth voice
    for path in scan_soundfonts(soundfont_dir):
        voices.append(Voice(
            name=display_name(path),
            engine="fluidsynth",
            path=path,
            category="General MIDI",
        ))
    return voices


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
