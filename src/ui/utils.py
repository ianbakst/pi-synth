import glob
import os


def scan_soundfonts(directory: str) -> list[str]:
    fonts = []
    for ext in ("*.sf2", "*.SF2", "*.sf3", "*.SF3"):
        fonts.extend(glob.glob(os.path.join(directory, "**", ext), recursive=True))
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
