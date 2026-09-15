"""Reading SoundFont headers, and turning a directory of them into voices.

The instrument library is easier to curate as *files* than as a JSON list: a
.sf2 in the soundfont directory is a voice, deleting it removes the voice, and
adding one is a copy — which is also exactly how USB import already behaves.

Two things make that work:

  - `preset_names()` reads a font's preset table without loading the file.
    Names come from the font itself rather than being reverse-engineered out of
    a filename, so "Rhodes EP" stays "Rhodes EP" and not "Rhodes Ep".
  - `gm_category()` derives a General MIDI family from the program number, so a
    directory of 128 fonts arrives grouped instead of as a wall of names.

The heavy lifting (splitting a font into one file per preset) lives in
tools/split_soundfont.py, which builds on the chunk walking here.
"""

from __future__ import annotations

import os
import struct

from synth_ui.clients.voice import Voice

SF2_EXTENSIONS = (".sf2", ".sf3")

_PHDR = "<20sHHHIII"      # name, preset, bank, bagNdx, library, genre, morphology
_PHDR_SIZE = struct.calcsize(_PHDR)

# General MIDI groups programs into families of eight.
_GM_FAMILIES = [
    "Piano", "Chromatic Percussion", "Organ", "Guitar",
    "Bass", "Strings", "Ensemble", "Brass",
    "Reed", "Pipe", "Synth Lead", "Synth Pad",
    "Synth Effects", "Ethnic", "Percussive", "Sound Effects",
]


def gm_category(bank: int, program: int) -> str:
    if bank == 128:
        return "Drums"
    return _GM_FAMILIES[program // 8] if 0 <= program < 128 else "Other"


def _read_chunk_header(f) -> tuple[bytes, int] | None:
    head = f.read(8)
    if len(head) < 8:
        return None
    return head[:4], struct.unpack("<I", head[4:])[0]


def preset_names(path: str) -> list[tuple[int, int, str]]:
    """(bank, program, name) for each preset, read from the file's header only.

    Seeks past the sample data rather than reading it. That matters: scanning a
    directory of 128 fonts happens on every voice-list refresh, and slurping
    them whole would mean reading hundreds of MB off the SD card to display a
    list. Returns [] for anything that isn't a readable SoundFont — a stray
    file in the directory must not break the library.
    """
    try:
        with open(path, "rb") as f:
            if f.read(4) != b"RIFF":
                return []
            f.read(4)                       # RIFF size
            if f.read(4) != b"sfbk":
                return []
            while (header := _read_chunk_header(f)) is not None:
                cid, size = header
                if cid != b"LIST":
                    f.seek(size + (size & 1), os.SEEK_CUR)
                    continue
                kind = f.read(4)
                if kind != b"pdta":
                    f.seek(size - 4 + (size & 1), os.SEEK_CUR)
                    continue
                end = f.tell() + size - 4
                while f.tell() < end:
                    sub = _read_chunk_header(f)
                    if sub is None:
                        return []
                    sid, ssize = sub
                    if sid == b"phdr":
                        return _parse_phdr(f.read(ssize))
                    f.seek(ssize + (ssize & 1), os.SEEK_CUR)
                return []
    except OSError:
        return []
    return []


def _parse_phdr(data: bytes) -> list[tuple[int, int, str]]:
    out = []
    # The final record is the terminal "EOP" sentinel, not a preset.
    for i in range(len(data) // _PHDR_SIZE - 1):
        name, program, bank, *_ = struct.unpack_from(_PHDR, data, i * _PHDR_SIZE)
        out.append((bank, program, name.split(b"\0")[0].decode("latin-1").strip()))
    return out


def scan(directory: str) -> list[str]:
    """Every SoundFont under `directory`, recursively, in a stable order."""
    found: list[str] = []
    for root, _dirs, files in os.walk(directory):
        for name in sorted(files):
            if name.lower().endswith(SF2_EXTENSIONS):
                found.append(os.path.join(root, name))
    return sorted(found)


def discover(directory: str, engine: str) -> list[Voice]:
    """Turn a directory of SoundFonts into voices.

    A font holding exactly one preset (what tools/split_soundfont.py produces)
    becomes a voice named after that preset. A multi-preset font — an untouched
    GM set, or something copied in over USB — becomes a single voice at whatever
    its first preset is, because the plugin that plays soundfonts in mod-host
    can't select a program. Splitting it is what makes the rest reachable.
    """
    voices: list[Voice] = []
    for path in scan(directory):
        presets = preset_names(path)
        if presets:
            bank, program, name = presets[0]
            category = gm_category(bank, program)
        else:
            # Unreadable header: still offer it rather than hiding it, and let
            # validation report the failure when it's loaded.
            name = os.path.splitext(os.path.basename(path))[0]
            category = "SoundFont"
        voices.append(
            Voice(name=name, engine=engine, path=path, category=category)
        )
    return voices
