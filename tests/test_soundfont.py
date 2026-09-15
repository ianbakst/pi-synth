"""Tests for SoundFont header reading and directory-as-manifest discovery.

Uses real generated fonts (from the splitter's synthetic source) rather than
mocks: the header reader seeks through actual RIFF structure, so a fixture that
isn't a real file would test nothing.
"""

import sys

import pytest

from synth_ui.clients.soundfont import discover, gm_category, preset_names, scan
from synth_ui.tools.split_soundfont import extract, read_sf2

sys.path.insert(0, "tests")
from test_split_soundfont import build_font  # noqa: E402


@pytest.fixture
def library(tmp_path):
    """A directory of single-preset fonts, as split_soundfont produces."""
    src = tmp_path / "src.sf2"
    src.write_bytes(build_font())
    sf = read_sf2(str(src))
    out = tmp_path / "gm"
    out.mkdir()
    (out / "000-000-yamaha-grand-piano.sf2").write_bytes(extract(sf, 0))
    (out / "000-004-rhodes-ep.sf2").write_bytes(extract(sf, 1))
    return out


# --- header reading ---------------------------------------------------------

def test_preset_name_comes_from_the_font_not_the_filename(library):
    # "000-004-rhodes-ep.sf2" would title-case back to "Rhodes Ep"; the font
    # itself is authoritative.
    got = preset_names(str(library / "000-004-rhodes-ep.sf2"))
    assert got == [(0, 0, "Rhodes")]


def test_reading_a_header_does_not_require_reading_the_samples(library, monkeypatch):
    # Scanning happens on every voice-list refresh; slurping 128 fonts would
    # mean hundreds of MB off the SD card just to draw a list.
    path = str(library / "000-000-yamaha-grand-piano.sf2")
    reads: list[int] = []
    real_open = open

    def counting_open(*args, **kw):
        f = real_open(*args, **kw)
        orig = f.read

        def read(n=-1):
            reads.append(n)
            return orig(n)

        f.read = read
        return f

    monkeypatch.setattr("builtins.open", counting_open)
    assert preset_names(path)
    # No unbounded read() — that's what slurping the whole file looks like.
    assert -1 not in reads


def test_a_non_soundfont_yields_nothing(tmp_path):
    junk = tmp_path / "notes.txt"
    junk.write_text("this is not a soundfont")
    assert preset_names(str(junk)) == []


def test_a_missing_file_yields_nothing(tmp_path):
    assert preset_names(str(tmp_path / "gone.sf2")) == []


def test_a_truncated_font_yields_nothing(tmp_path):
    path = tmp_path / "cut.sf2"
    path.write_bytes(build_font()[:64])
    assert preset_names(str(path)) == []


# --- discovery --------------------------------------------------------------

def test_scan_finds_fonts_in_a_stable_order(library):
    assert [p.split("/")[-1] for p in scan(str(library))] == [
        "000-000-yamaha-grand-piano.sf2",
        "000-004-rhodes-ep.sf2",
    ]


def test_scan_ignores_other_files(library):
    (library / "readme.txt").write_text("hi")
    assert len(scan(str(library))) == 2


def test_discover_names_and_categorises_from_the_font(library):
    voices = discover(str(library), engine="fluida")
    assert [(v.name, v.category, v.engine) for v in voices] == [
        ("Piano", "Piano", "fluida"),
        ("Rhodes", "Piano", "fluida"),
    ]


def test_discover_keeps_an_unreadable_file_as_a_voice(library):
    # Better a voice that reports why it fails than one that silently vanishes.
    (library / "broken.sf2").write_bytes(b"RIFF____sfbk")
    voices = discover(str(library), engine="fluida")
    names = [v.name for v in voices]
    assert "broken" in names


# --- GM families ------------------------------------------------------------

@pytest.mark.parametrize("program,expected", [
    (0, "Piano"), (16, "Organ"), (62, "Brass"),
    (81, "Synth Lead"), (89, "Synth Pad"), (127, "Sound Effects"),
])
def test_gm_category(program, expected):
    assert gm_category(0, program) == expected


def test_drum_bank_is_not_a_gm_family():
    assert gm_category(128, 0) == "Drums"


# --- load_voices: manifest + directory together -----------------------------

def test_directory_fonts_become_voices_alongside_the_manifest(tmp_path, library):
    import json

    from synth_ui.ui.utils import load_voices

    manifest = tmp_path / "voices.json"
    manifest.write_text(json.dumps([{
        "name": "Hammond B3", "engine": "modhost",
        "uri": "http://gareus.org/oss/lv2/b_synth", "category": "Organ",
    }]))
    voices = load_voices(str(manifest), str(library))
    names = [v.name for v in voices]
    assert "Hammond B3" in names          # curated entry survives
    assert "Rhodes" in names              # and the directory contributes
    assert len(voices) == 3


def test_a_manifest_entry_wins_over_the_same_file_on_disk(tmp_path, library):
    # The manifest carries what a bare file can't — trim, presets, params — so
    # rediscovering the same path would drop those settings.
    import json

    from synth_ui.ui.utils import load_voices

    path = str(library / "000-004-rhodes-ep.sf2")
    manifest = tmp_path / "voices.json"
    manifest.write_text(json.dumps([{
        "name": "Wet Rhodes", "engine": "fluida",
        "path": path, "category": "Electric Piano", "gain_trim_db": -3.0,
    }]))
    voices = load_voices(str(manifest), str(library))
    rhodes = [v for v in voices if path in v.path]
    assert len(rhodes) == 1
    assert rhodes[0].name == "Wet Rhodes"
    assert rhodes[0].gain_trim_db == -3.0


def test_duplicate_names_are_disambiguated(tmp_path, library):
    # Rigs reference a voice by name; two voices sharing one would make the
    # second unreachable.
    import shutil

    from synth_ui.ui.utils import load_voices

    shutil.copy(library / "000-004-rhodes-ep.sf2", library / "copy.sf2")
    manifest = tmp_path / "voices.json"
    manifest.write_text("[]")
    voices = load_voices(str(manifest), str(library))
    names = [v.name for v in voices]
    assert len(names) == len(set(names))
