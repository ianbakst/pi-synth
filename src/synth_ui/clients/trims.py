"""Per-voice level trims, measured on this board.

Why these don't live in voices.json, where `Voice.gain_trim_db` is declared:

  - **Most voices aren't in voices.json.** The split GM set and anything copied
    from USB are discovered from the soundfont directory — that was the point of
    "derive the manifest from the directory". There is no manifest entry to
    write a number into, and inventing 128 of them would undo the design.
  - **voices.json is shipped, these are measured.** `deploy.sh` copies the
    repo's manifest over the board's, so anything written there is erased by the
    next deploy. Trims belong with the other things this particular unit knows
    about itself — next to ~/.synth-rigs.json, in $HOME, untouched by deploys.
  - **They're hardware-specific.** The numbers depend on this DAC and these
    instrument files. Another board with the same manifest wants its own.

Keyed by voice name because that is what a rig references and what survives a
voice moving on disk.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile

logger = logging.getLogger(__name__)


def read_trims(path: str) -> dict[str, float]:
    """Voice name -> trim in dB. Missing or malformed reads as "no trims",
    never as an error: an unreadable file must not stop the instrument booting.
    """
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            data = json.load(f)
        return {
            str(name): float(db)
            for name, db in data.items()
            if isinstance(db, (int, float))
        }
    except (OSError, ValueError, AttributeError) as exc:
        logger.warning("could not read trims from %s: %s", path, exc)
        return {}


def write_trims(path: str, trims: dict[str, float]) -> bool:
    """Replace the trim file atomically, so an interrupted write can't leave a
    half-written file that reads as "no trims" on the next boot."""
    directory = os.path.dirname(path) or "."
    try:
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump({k: round(v, 1) for k, v in sorted(trims.items())}, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
        return True
    except OSError as exc:
        logger.error("could not write trims to %s: %s", path, exc)
        return False


def apply_trims(voices: list, trims: dict[str, float]) -> list:
    """Overlay measured trims onto a voice library, in place.

    Measured wins over the manifest's declared `gain_trim_db`: the manifest
    ships a guess for every board, this was measured on this one.
    """
    for voice in voices:
        if voice.name in trims:
            voice.gain_trim_db = trims[voice.name]
    return voices
