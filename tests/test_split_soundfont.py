"""Tests for the SoundFont splitter, against a synthetic font built in-process.

The splitter rewrites RIFF record tables and re-bases every sample offset, so a
mistake shows up as a font that loads but plays the wrong sample — or silence.
Building a known font and re-reading the output is the only way to check that
without the real 140MB file.
"""

import json
import struct

import pytest

from synth_ui.clients.lv2 import spec_for
from synth_ui.clients.voice import read_voices_manifest, validate
from synth_ui.tools.split_soundfont import (
    _GEN,
    _PHDR,
    _SHDR,
    _chunk,
    _pack,
    extract,
    gm_category,
    main,
    presets,
    read_sf2,
    slug,
)

# Three samples of distinct, recognisable content so a mix-up is visible.
SAMPLES = [
    (b"kick", [1] * 16),
    (b"snare", [2] * 24),
    (b"hat", [3] * 8),
]


def _sample_data():
    """Raw smpl bytes plus (start, end) frame offsets for each sample."""
    data = bytearray()
    offsets = []
    for _name, frames in SAMPLES:
        start = len(data) // 2
        for v in frames:
            data += struct.pack("<h", v)
        offsets.append((start, len(data) // 2))
        data += b"\0" * (46 * 2)   # the spec's inter-sample gap
    return bytes(data), offsets


def build_font() -> bytes:
    """Two presets: #0 uses instrument 0 (kick), #4 uses instrument 1 (snare+hat)."""
    smpl, offsets = _sample_data()

    shdr = [
        (name, offsets[i][0], offsets[i][1], offsets[i][0], offsets[i][1],
         44100, 60, 0, 0, 1)
        for i, (name, _f) in enumerate(SAMPLES)
    ] + [(b"EOS", 0, 0, 0, 0, 0, 0, 0, 0, 0)]

    # instrument 0 -> sample 0 ; instrument 1 -> samples 1 and 2
    igen = [(53, 0), (53, 1), (53, 2), (0, 0)]
    ibag = [(0, 0), (1, 0), (2, 0), (3, 0)]
    inst = [(b"inst0", 0), (b"inst1", 1), (b"EOI", 3)]

    pgen = [(41, 0), (41, 1), (0, 0)]
    pbag = [(0, 0), (1, 0), (2, 0)]
    phdr = [
        (b"Piano", 0, 0, 0, 0, 0, 0),
        (b"Rhodes", 4, 0, 1, 0, 0, 0),
        (b"EOP", 0, 0, 2, 0, 0, 0),
    ]

    pdta = b"pdta" + b"".join((
        _chunk("phdr", _pack(_PHDR, phdr)),
        _chunk("pbag", _pack("<HH", pbag)),
        _chunk("pmod", b""),
        _chunk("pgen", _pack(_GEN, pgen)),
        _chunk("inst", _pack("<20sH", inst)),
        _chunk("ibag", _pack("<HH", ibag)),
        _chunk("imod", b""),
        _chunk("igen", _pack(_GEN, igen)),
        _chunk("shdr", _pack(_SHDR, shdr)),
    ))
    body = b"sfbk" + b"".join((
        _chunk("LIST", b"INFO" + _chunk("ifil", struct.pack("<HH", 2, 1))),
        _chunk("LIST", b"sdta" + _chunk("smpl", smpl)),
        _chunk("LIST", pdta),
    ))
    return _chunk("RIFF", body)


@pytest.fixture
def font(tmp_path):
    path = tmp_path / "test.sf2"
    path.write_bytes(build_font())
    return read_sf2(str(path))


def reread(tmp_path, data: bytes):
    path = tmp_path / "out.sf2"
    path.write_bytes(data)
    return read_sf2(str(path))


# --- reading ----------------------------------------------------------------

def test_reads_every_preset(font):
    assert presets(font) == [(0, 0, "Piano"), (0, 4, "Rhodes")]


def test_rejects_a_non_soundfont(tmp_path):
    path = tmp_path / "nope.sf2"
    path.write_bytes(b"not a riff file at all")
    with pytest.raises(ValueError, match="not a SoundFont"):
        read_sf2(str(path))


# --- splitting --------------------------------------------------------------

def test_extracted_font_holds_exactly_one_preset(tmp_path, font):
    out = reread(tmp_path, extract(font, 1))
    assert presets(out) == [(0, 0, "Rhodes")]


def test_the_preset_is_renumbered_to_program_zero(tmp_path, font):
    # The whole point: Fluida can't send a program change, so the wanted
    # instrument has to BE program 0.
    out = reread(tmp_path, extract(font, 1))
    _name, program, bank, *_ = out.phdr[0]
    assert (bank, program) == (0, 0)


def test_only_referenced_samples_are_copied(tmp_path, font):
    # Preset 0 uses one sample; preset 1 uses two. Copying all three would make
    # every split file as large as the original.
    piano = reread(tmp_path, extract(font, 0))
    rhodes = reread(tmp_path, extract(font, 1))
    assert len(piano.shdr) == 2      # 1 sample + terminal EOS
    assert len(rhodes.shdr) == 3     # 2 samples + terminal


def test_sample_data_survives_the_rebase(tmp_path, font):
    # Offsets are re-based into a fresh smpl chunk; an off-by-one here plays the
    # wrong sample, or noise.
    out = reread(tmp_path, extract(font, 0))
    name, start, end, *_ = out.shdr[0]
    assert name.split(b"\0")[0] == b"kick"
    frames = struct.unpack_from(f"<{end - start}h", out.smpl, start * 2)
    assert set(frames) == {1}


def test_second_preset_keeps_both_of_its_samples(tmp_path, font):
    out = reread(tmp_path, extract(font, 1))
    got = set()
    for name, start, end, *_ in out.shdr[:-1]:
        got.add(struct.unpack_from(f"<{end - start}h", out.smpl, start * 2)[0])
    assert got == {2, 3}


def test_instrument_and_sample_indices_are_remapped(tmp_path, font):
    # Preset 1 pointed at instrument 1 and samples 1,2 in the source; in a file
    # containing only those, every index must shift down to 0-based.
    out = reread(tmp_path, extract(font, 1))
    assert [g for g in out.pgen if g[0] == 41] == [(41, 0)]
    assert sorted(a for o, a in out.igen if o == 53) == [0, 1]


def test_terminal_records_are_present(tmp_path, font):
    # Every SF2 table ends in a sentinel; without it, loaders read past the end.
    out = reread(tmp_path, extract(font, 0))
    assert out.phdr[-1][0].split(b"\0")[0] == b"EOP"
    assert out.inst[-1][0].split(b"\0")[0] == b"EOI"
    assert out.shdr[-1][0].split(b"\0")[0] == b"EOS"


def test_output_is_smaller_than_the_source(tmp_path, font):
    src = len(build_font())
    assert len(extract(font, 0)) < src


# --- naming -----------------------------------------------------------------

@pytest.mark.parametrize("name,expected", [
    ("Rhodes EP", "rhodes-ep"),
    ("Honky-Tonk", "honky-tonk"),
    ("Synth Brass 1", "synth-brass-1"),
    ("***", "preset"),
])
def test_slug(name, expected):
    assert slug(name) == expected


# --- manifest generation ----------------------------------------------------

@pytest.mark.parametrize("program,expected", [
    (0, "Piano"), (4, "Piano"), (7, "Piano"),
    (16, "Organ"), (18, "Organ"),
    (62, "Brass"), (81, "Synth Lead"), (89, "Synth Pad"),
    (127, "Sound Effects"),
])
def test_gm_category_from_program_number(program, expected):
    # GM groups programs in families of eight; deriving it means a 128-entry
    # manifest arrives navigable instead of as a flat wall of names.
    assert gm_category(0, program) == expected


def test_drum_bank_is_not_a_gm_family():
    assert gm_category(128, 0) == "Drums"


def test_manifest_entries_carry_no_program(tmp_path):
    # The preset was renumbered to 0, which is the point of splitting — a
    # leftover `program` would be a lie about how the voice is selected.
    src = tmp_path / "in.sf2"
    src.write_bytes(build_font())
    manifest = tmp_path / "gm.json"
    main([
        "--input", str(src), "--out", str(tmp_path / "out"),
        "--manifest", str(manifest),
    ])
    entries = json.loads(manifest.read_text())
    assert [e["name"] for e in entries] == ["Piano", "Rhodes"]
    for e in entries:
        assert "program" not in e and "bank" not in e
        assert e["engine"] == "fluida"
        assert e["path"].endswith(".sf2")


def test_manifest_voices_validate_against_the_real_schema(tmp_path):
    # The generated entries have to survive read_voices_manifest, or the split
    # produces files the instrument can't name.
    src = tmp_path / "in.sf2"
    src.write_bytes(build_font())
    manifest = tmp_path / "gm.json"
    main([
        "--input", str(src), "--out", str(tmp_path / "out"),
        "--manifest", str(manifest),
    ])
    voices = read_voices_manifest(str(manifest))
    assert len(voices) == 2
    for v in voices:
        # engine "fluida" must resolve to a plugin URI *and* a soundfont
        # property, else the font loads into nothing.
        spec = spec_for(v.engine, v.uri, v.file_property)
        assert spec is not None and spec.file_property
        assert validate(v, has_uri=lambda uri: True) == ""
