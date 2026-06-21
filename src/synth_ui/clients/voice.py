import json
import os
from dataclasses import dataclass


@dataclass
class Voice:
    name: str
    engine: str   # "fluidsynth" | "sfizz" | "setbfree" | "dexed" | "pianoteq"
    path: str     # SF2, SFZ, .syx path; empty string for setBfree
    category: str # "Piano" | "Organ" | "Electric Piano" | "General MIDI" | etc.


def read_voices_manifest(manifest_path: str) -> list[Voice]:
    """Parse voices.json. Returns empty list if the file is missing or malformed."""
    if not os.path.exists(manifest_path):
        return []
    try:
        with open(manifest_path) as f:
            data = json.load(f)
        voices = []
        for entry in data:
            voices.append(Voice(
                name=entry["name"],
                engine=entry["engine"],
                path=entry.get("path", ""),
                category=entry.get("category", ""),
            ))
        return voices
    except Exception:
        return []
