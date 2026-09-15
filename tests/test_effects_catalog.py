"""Tests for the effects catalogue — curated defaults plus per-unit overrides.

The contract changed here: effects.json used to be the whole catalogue, and an
empty or absent file meant an empty rack browser (which is what shipped, and why
no rig could ever hold an effect). It is now an *override* on a curated list
built into the code, so the browser is populated out of the box.
"""

import json

from synth_ui.clients.effects_catalog import (
    DEFAULT_EFFECTS,
    EffectCatalogEntry,
    annotate_effects,
    read_effects_manifest,
)


def uris(entries):
    return [e.uri for e in entries]


class TestDefaults:
    def test_missing_file_gives_the_curated_catalogue(self, tmp_path):
        """A board with no overrides must still have a usable rack browser."""
        entries = read_effects_manifest(str(tmp_path / "nope.json"))
        assert uris(entries) == uris(DEFAULT_EFFECTS)

    def test_malformed_json_does_not_empty_the_catalogue(self, tmp_path):
        path = tmp_path / "effects.json"
        path.write_text("{not valid json")
        assert uris(read_effects_manifest(str(path))) == uris(DEFAULT_EFFECTS)

    def test_defaults_are_not_shared_between_calls(self, tmp_path):
        """Callers annotate the result in place; that must not leak into the
        module-level list and persist across reloads."""
        first = read_effects_manifest(str(tmp_path / "nope.json"))
        first[0].unavailable_reason = "poisoned"
        second = read_effects_manifest(str(tmp_path / "nope.json"))
        assert second[0].unavailable_reason == ""

    def test_every_default_has_a_uri_and_category(self):
        for entry in DEFAULT_EFFECTS:
            assert entry.uri.startswith("http"), entry.name
            assert entry.category, entry.name

    def test_no_duplicate_uris(self):
        assert len(uris(DEFAULT_EFFECTS)) == len(set(uris(DEFAULT_EFFECTS)))


class TestOverrides:
    def test_unknown_uri_is_appended(self, tmp_path):
        path = tmp_path / "effects.json"
        path.write_text(json.dumps([{"name": "Weird", "uri": "urn:weird"}]))
        entries = read_effects_manifest(str(path))
        assert len(entries) == len(DEFAULT_EFFECTS) + 1
        assert entries[-1].name == "Weird"

    def test_matching_uri_replaces_the_default_in_place(self, tmp_path):
        """Overriding must not duplicate the entry or move it in the chain
        order, which is deliberate."""
        target = DEFAULT_EFFECTS[3]
        path = tmp_path / "effects.json"
        path.write_text(json.dumps([{"name": "Renamed", "uri": target.uri}]))
        entries = read_effects_manifest(str(path))
        assert len(entries) == len(DEFAULT_EFFECTS)
        assert entries[3].name == "Renamed"

    def test_malformed_entry_is_skipped_not_fatal(self, tmp_path):
        path = tmp_path / "effects.json"
        path.write_text(json.dumps([
            {"category": "Reverb"},                      # no name, no uri
            {"name": "Fine", "uri": "urn:fine"},
        ]))
        entries = read_effects_manifest(str(path))
        assert entries[-1].name == "Fine"
        assert len(entries) == len(DEFAULT_EFFECTS) + 1

    def test_category_is_optional(self, tmp_path):
        path = tmp_path / "effects.json"
        path.write_text(json.dumps([{"name": "R", "uri": "urn:r"}]))
        assert read_effects_manifest(str(path))[-1].category == ""


class TestAnnotate:
    def test_missing_plugin_is_marked_unavailable(self):
        entries = [EffectCatalogEntry("X", "urn:x", "Reverb")]
        annotate_effects(entries, has_uri=lambda _u: False)
        assert not entries[0].available
        assert entries[0].unavailable_reason == "plugin not installed"

    def test_installed_plugin_is_available(self):
        entries = [EffectCatalogEntry("X", "urn:x", "Reverb")]
        annotate_effects(entries, has_uri=lambda _u: True)
        assert entries[0].available

    def test_re_annotating_clears_a_stale_reason(self):
        """A plugin installed since the last scan must stop being greyed out."""
        entries = [EffectCatalogEntry("X", "urn:x", "Reverb", unavailable_reason="old")]
        annotate_effects(entries, has_uri=lambda _u: True)
        assert entries[0].available
