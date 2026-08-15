import json
import os
from dataclasses import dataclass


@dataclass
class EffectCatalogEntry:
    name: str
    uri: str      # LV2 plugin URI, passed straight to EngineManager.add_effect
    category: str # "Reverb" | "Delay" | "EQ" | "Dynamics" | etc.


def read_effects_manifest(manifest_path: str) -> list[EffectCatalogEntry]:
    """Parse effects.json. Returns empty list if the file is missing or malformed."""
    if not os.path.exists(manifest_path):
        return []
    try:
        with open(manifest_path) as f:
            data = json.load(f)
        entries = []
        for entry in data:
            entries.append(EffectCatalogEntry(
                name=entry["name"],
                uri=entry["uri"],
                category=entry.get("category", ""),
            ))
        return entries
    except Exception:
        return []
