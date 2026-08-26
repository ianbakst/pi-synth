"""Tests for the voice manifest schema + validation. No hardware."""

import json

from synth_ui.clients.voice import Voice, annotate, read_voices_manifest, validate

MDA_EPIANO = "http://drobilla.net/plugins/mda/EPiano"
SFIZZ_URI = "http://sfztools.github.io/sfizz"


def write_manifest(tmp_path, entries):
    path = tmp_path / "voices.json"
    path.write_text(json.dumps(entries))
    return str(path)


# --- parsing ----------------------------------------------------------------

def test_reads_lv2_voice_with_full_schema(tmp_path):
    manifest = write_manifest(tmp_path, [{
        "name": "Rhodes EP",
        "engine": "modhost",
        "uri": MDA_EPIANO,
        "category": "Electric Piano",
        "preset": "urn:mda:EPiano:bright",
        "params": {"decay": 0.6, "hardness": 1},
        "gain_trim_db": -2.5,
        "resident": True,
    }])
    (voice,) = read_voices_manifest(manifest)
    assert voice.uri == MDA_EPIANO
    assert voice.preset == "urn:mda:EPiano:bright"
    assert voice.params == {"decay": 0.6, "hardness": 1.0}
    assert voice.gain_trim_db == -2.5
    assert voice.resident is True
    assert voice.path == ""


def test_legacy_entries_still_parse(tmp_path):
    """The pre-existing 4-field schema must keep working unchanged."""
    manifest = write_manifest(tmp_path, [{
        "name": "Hammond B3", "engine": "setbfree", "path": "", "category": "Organ",
    }])
    (voice,) = read_voices_manifest(manifest)
    assert voice.name == "Hammond B3"
    assert voice.engine == "setbfree"
    assert voice.category == "Organ"
    assert voice.uri == "" and voice.params == {} and voice.resident is False


def test_malformed_manifest_yields_no_voices(tmp_path):
    path = tmp_path / "voices.json"
    path.write_text("{ not json")
    assert read_voices_manifest(str(path)) == []


# --- validation -------------------------------------------------------------

def test_lv2_voice_is_usable_when_its_plugin_is_installed():
    voice = Voice("Rhodes", "modhost", "", "EP", uri=MDA_EPIANO)
    assert validate(voice, has_uri=lambda uri: uri == MDA_EPIANO) == ""
    assert voice.available


def test_uninstalled_plugin_is_reported():
    voice = Voice("DX7", "dexed", "", "EP")
    assert validate(voice, has_uri=lambda uri: False) == "plugin not installed"


def test_missing_instrument_file_is_reported():
    voice = Voice("Piano", "sfizz", "/nope/piano.sfz", "Piano")
    reason = validate(voice, has_uri=lambda uri: True, path_exists=lambda p: False)
    assert reason == "file missing"


def test_file_voice_without_a_patch_property_is_reported():
    # Dexed today: plugin may exist, but with no file property the .syx can
    # never be delivered, so the voice would load silently wrong.
    voice = Voice("DX7", "dexed", "/dx/rom1a.syx", "EP")
    reason = validate(voice, has_uri=lambda uri: True, path_exists=lambda p: True)
    assert reason == "no file property for this plugin"


def test_generic_voice_without_uri_is_reported():
    assert validate(Voice("Broken", "modhost", "", "")) == "no plugin URI"


def test_unknown_engine_is_reported():
    assert validate(Voice("X", "nope", "", "")) == "unknown engine 'nope'"


def test_process_engine_voice_only_needs_its_file():
    voice = Voice("GM", "fluidsynth", "/sf/default.sf2", "GM")
    assert validate(voice, has_uri=lambda uri: False, path_exists=lambda p: True) == ""


def test_annotate_marks_each_voice_in_place():
    voices = [
        Voice("Rhodes", "modhost", "", "EP", uri=MDA_EPIANO),
        Voice("Piano", "sfizz", "/nope.sfz", "Piano"),
    ]
    annotate(voices, has_uri=lambda uri: True, path_exists=lambda p: False)
    assert voices[0].available
    assert not voices[1].available
    assert voices[1].unavailable_reason == "file missing"
