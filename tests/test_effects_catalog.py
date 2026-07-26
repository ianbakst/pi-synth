"""Tests for the effects catalog loader — tolerant JSON parsing, no hardware."""

import json

from synth_ui.clients.effects_catalog import EffectCatalogEntry, read_effects_manifest


def test_missing_file_returns_empty_list(tmp_path):
    assert read_effects_manifest(str(tmp_path / "nope.json")) == []


def test_malformed_json_returns_empty_list(tmp_path):
    path = tmp_path / "effects.json"
    path.write_text("{not valid json")
    assert read_effects_manifest(str(path)) == []


def test_entry_missing_required_field_returns_empty_list(tmp_path):
    path = tmp_path / "effects.json"
    path.write_text(json.dumps([{"name": "Reverb", "category": "Reverb"}]))
    assert read_effects_manifest(str(path)) == []


def test_optional_category_defaults_to_empty_string(tmp_path):
    path = tmp_path / "effects.json"
    path.write_text(json.dumps([{"name": "Reverb", "uri": "urn:reverb"}]))
    assert read_effects_manifest(str(path)) == [
        EffectCatalogEntry(name="Reverb", uri="urn:reverb", category="")
    ]


def test_parses_multiple_valid_entries(tmp_path):
    path = tmp_path / "effects.json"
    path.write_text(
        json.dumps(
            [
                {"name": "Reverb", "uri": "urn:reverb", "category": "Reverb"},
                {"name": "Delay", "uri": "urn:delay", "category": "Delay"},
            ]
        )
    )
    assert read_effects_manifest(str(path)) == [
        EffectCatalogEntry(name="Reverb", uri="urn:reverb", category="Reverb"),
        EffectCatalogEntry(name="Delay", uri="urn:delay", category="Delay"),
    ]
