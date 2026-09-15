"""Tests for the measured-trim store.

Trims live outside voices.json because most voices aren't in it — the split GM
set is discovered from the soundfont directory — and because deploy.sh
overwrites the shipped manifest. These assert the properties that design
depends on.
"""

import json
from dataclasses import dataclass

from synth_ui.clients.trims import apply_trims, read_trims, write_trims


@dataclass
class FakeVoice:
    name: str
    gain_trim_db: float = 0.0


class TestReadWrite:
    def test_round_trip(self, tmp_path):
        path = str(tmp_path / "t.json")
        write_trims(path, {"Rhodes EP": -4.2, "Hammond B3": 1.5})
        assert read_trims(path) == {"Rhodes EP": -4.2, "Hammond B3": 1.5}

    def test_missing_file_is_not_an_error(self, tmp_path):
        """An absent trim file is the normal state of a fresh board; it must
        never stop the instrument booting."""
        assert read_trims(str(tmp_path / "absent.json")) == {}

    def test_malformed_file_is_not_an_error(self, tmp_path):
        path = tmp_path / "t.json"
        path.write_text("{ this is not json")
        assert read_trims(str(path)) == {}

    def test_non_numeric_entries_are_dropped(self, tmp_path):
        path = tmp_path / "t.json"
        path.write_text(json.dumps({"Good": -3.0, "Bad": "loud"}))
        assert read_trims(str(path)) == {"Good": -3.0}

    def test_write_is_atomic(self, tmp_path):
        """No .tmp left behind, and the file is complete after the write."""
        path = str(tmp_path / "t.json")
        write_trims(path, {"A": 1.0})
        assert [p.name for p in tmp_path.iterdir()] == ["t.json"]

    def test_write_creates_the_directory(self, tmp_path):
        path = str(tmp_path / "nested" / "dir" / "t.json")
        assert write_trims(path, {"A": 1.0})
        assert read_trims(path) == {"A": 1.0}


class TestApply:
    def test_measured_overrides_the_manifest(self):
        """The manifest ships a guess for every board; this was measured on
        this one."""
        voices = [FakeVoice("Rhodes EP", gain_trim_db=-12.0)]
        apply_trims(voices, {"Rhodes EP": -4.0})
        assert voices[0].gain_trim_db == -4.0

    def test_unmeasured_voices_keep_their_manifest_value(self):
        voices = [FakeVoice("Hammond B3", gain_trim_db=-2.0)]
        apply_trims(voices, {"Rhodes EP": -4.0})
        assert voices[0].gain_trim_db == -2.0

    def test_empty_trims_change_nothing(self):
        voices = [FakeVoice("A", gain_trim_db=3.0)]
        apply_trims(voices, {})
        assert voices[0].gain_trim_db == 3.0
