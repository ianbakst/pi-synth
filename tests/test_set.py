"""Tests for sets — one song's rigs, and the store they all live in.

No hardware. Covers the containment model (a rig belongs to exactly one set),
the ordering and stepping a footswitch depends on, and the migration from the
pre-sets rig store.
"""

import json
import os

from synth_ui.clients.rig import Rig, RigEffect
from synth_ui.clients.set import (
    BACKUP_INTERVAL_S,
    SetLibrary,
    SongSet,
    backup_paths,
    read_sets,
    write_sets,
)

REVERB = "urn:reverb"


def song_set(name, rig_names=()):
    return SongSet(name=name, rigs=[Rig(n, n) for n in rig_names])


def names(rigs):
    return [r.name for r in rigs]


# --- rigs live inside a set -------------------------------------------------

def test_a_new_rig_is_named_after_its_voice_and_lands_in_the_set():
    s = SongSet("Levitating")
    rig = s.create_from_voice("Rhodes EP")
    assert rig.name == "Rhodes EP" and rig.voice == "Rhodes EP"
    assert rig.effects == []
    assert s.rigs == [rig]


def test_picking_the_same_voice_twice_gets_a_distinct_name():
    # Two rigs on one instrument is normal (dry vs wet). Names may legally
    # collide now that rigs have ids, but two identical pads are
    # indistinguishable on a touchscreen, so auto-naming still counts up.
    s = SongSet("Set")
    assert s.create_from_voice("Rhodes EP").name == "Rhodes EP"
    assert s.create_from_voice("Rhodes EP").name == "Rhodes EP 2"
    assert s.create_from_voice("Rhodes EP").name == "Rhodes EP 3"


def test_the_same_name_in_two_sets_does_not_collide():
    """Sets are independent: a "Rhodes" in every song is the normal case."""
    a, b = SongSet("Song A"), SongSet("Song B")
    assert a.create_from_voice("Rhodes EP").name == "Rhodes EP"
    assert b.create_from_voice("Rhodes EP").name == "Rhodes EP"


def test_replace_matches_on_identity_not_name():
    # Matching on the name would fail for the one edit most likely to be saved
    # alongside others: a rename.
    s = SongSet("Set")
    rig = s.create_from_voice("Hammond B3")
    rig.name = "Gospel B3"
    rig.effects = [RigEffect(REVERB)]
    s.replace(rig)
    assert len(s.rigs) == 1
    assert s.rigs[0].name == "Gospel B3"
    assert s.rigs[0].effects[0].uri == REVERB


def test_removing_a_rig_from_its_set_deletes_it():
    # There is nowhere else for it to be.
    s = song_set("Set", ["A", "B"])
    s.remove(s.rigs[0].id)
    assert names(s.rigs) == ["B"]


def test_get_finds_by_id():
    s = song_set("Set", ["A", "B"])
    assert s.get(s.rigs[1].id).name == "B"
    assert s.get("nope") is None


# --- order is performance order ---------------------------------------------

class TestOrdering:
    """The sequence next/previous and a footswitch step through."""

    def test_move_reorders(self):
        s = song_set("Set", ["A", "B", "C"])
        assert s.move(2, 0)
        assert names(s.rigs) == ["C", "A", "B"]

    def test_move_to_the_same_place_is_a_noop(self):
        assert not song_set("Set", ["A", "B"]).move(1, 1)

    def test_move_clamps_past_the_end(self):
        s = song_set("Set", ["A", "B", "C"])
        assert s.move(0, 99)
        assert names(s.rigs) == ["B", "C", "A"]

    def test_move_rejects_a_bad_source(self):
        assert not song_set("Set", ["A"]).move(5, 0)

    def test_step_forward_and_back(self):
        s = song_set("Set", ["A", "B", "C"])
        assert s.step(s.rigs[0].id, 1).name == "B"
        assert s.step(s.rigs[1].id, -1).name == "A"

    def test_step_wraps_both_ways(self):
        """A footswitch can't show you that you've hit the end, so stopping
        dead there is worse than looping."""
        s = song_set("Set", ["A", "B", "C"])
        assert s.step(s.rigs[2].id, 1).name == "A"
        assert s.step(s.rigs[0].id, -1).name == "C"

    def test_step_from_nothing_gives_the_first(self):
        assert song_set("Set", ["A", "B"]).step(None, 1).name == "A"

    def test_step_from_a_rig_in_another_set_gives_the_first(self):
        assert song_set("Set", ["A", "B"]).step("elsewhere", 1).name == "A"

    def test_step_on_an_empty_set_is_none(self):
        assert song_set("Set", []).step(None, 1) is None


# --- the store --------------------------------------------------------------

def test_round_trips_through_the_store(tmp_path):
    path = str(tmp_path / "sets.json")
    s = SongSet("Levitating")
    rig = s.create_from_voice("Calf Wavetable")
    rig.voice_params = {"o1wave": 5.0}
    assert write_sets(path, [s]) is True

    back = read_sets(path)
    assert [x.name for x in back] == ["Levitating"]
    assert back[0].id == s.id
    assert names(back[0].rigs) == ["Calf Wavetable"]
    assert back[0].rigs[0].id == rig.id
    assert back[0].rigs[0].voice_params == {"o1wave": 5.0}


def test_the_store_is_written_atomically(tmp_path):
    # The user's own work, unlike the shipped catalog — a half-written file on
    # power loss would lose it.
    path = str(tmp_path / "sets.json")
    write_sets(path, [song_set("A", ["R"])])
    assert [p.name for p in tmp_path.iterdir()] == ["sets.json"]
    assert json.loads((tmp_path / "sets.json").read_text())[0]["name"] == "A"


def test_a_missing_store_is_not_an_error(tmp_path):
    assert read_sets(str(tmp_path / "nope.json")) == []


def test_a_malformed_store_does_not_break_the_instrument(tmp_path):
    path = tmp_path / "sets.json"
    path.write_text("{ not json")
    assert read_sets(str(path)) == []


def test_a_set_saved_without_identity_is_given_one(tmp_path):
    path = tmp_path / "sets.json"
    path.write_text(json.dumps([{"name": "Old", "rigs": []}]))
    assert read_sets(str(path))[0].id


# --- the library ------------------------------------------------------------

def test_creating_a_set_persists_it(tmp_path):
    path = str(tmp_path / "sets.json")
    SetLibrary(path).create("Levitating")
    assert [s.name for s in SetLibrary.load(path).sets] == ["Levitating"]


def test_set_names_are_deduplicated(tmp_path):
    lib = SetLibrary(str(tmp_path / "sets.json"))
    assert lib.create("Gig").name == "Gig"
    assert lib.create("Gig").name == "Gig 2"


def test_removing_a_set_takes_its_rigs_with_it(tmp_path):
    path = str(tmp_path / "sets.json")
    lib = SetLibrary(path)
    keep, drop = lib.create("Keep"), lib.create("Drop")
    drop.create_from_voice("Rhodes EP")
    lib.save()

    lib.remove(drop.id)
    reloaded = SetLibrary.load(path)
    assert [s.id for s in reloaded.sets] == [keep.id]
    assert reloaded.find_rig(drop.rigs[0].id) is None


def test_find_rig_locates_the_set_holding_it(tmp_path):
    lib = SetLibrary(str(tmp_path / "sets.json"))
    lib.create("A")
    b = lib.create("B")
    rig = b.create_from_voice("Hammond B3")
    found = lib.find_rig(rig.id)
    assert found is not None
    assert found[0].id == b.id and found[1].id == rig.id


def test_sets_reorder_and_persist(tmp_path):
    path = str(tmp_path / "sets.json")
    lib = SetLibrary(path)
    for name in ("A", "B", "C"):
        lib.create(name)
    assert lib.move(2, 0)
    assert [s.name for s in SetLibrary.load(path).sets] == ["C", "A", "B"]


def test_bootstrap_gives_a_fresh_card_something_to_play(tmp_path):
    # No sets and no rigs: the unit must still boot into something playable
    # rather than an empty list.
    path = str(tmp_path / "sets.json")
    lib = SetLibrary(path)
    s = lib.bootstrap("Rhodes EP")
    assert s is not None
    assert names(s.rigs) == ["Rhodes EP"]
    assert names(SetLibrary.load(path).sets[0].rigs) == ["Rhodes EP"]


def test_bootstrap_leaves_an_existing_library_alone(tmp_path):
    lib = SetLibrary(str(tmp_path / "sets.json"))
    lib.create("Mine")
    lib.bootstrap("Rhodes EP")
    assert [s.name for s in lib.sets] == ["Mine"]


# --- migration from the pre-sets rig store ----------------------------------

def test_rigs_from_before_sets_become_one_set(tmp_path):
    """Every board in the field has a flat rig file and no sets. Those rigs are
    the user's actual instrument — they have to survive, in order."""
    rigs_path = tmp_path / "rigs.json"
    rigs_path.write_text(json.dumps([
        {"name": "Hammond B3", "voice": "Hammond B3"},
        {"name": "Rhodes EP", "voice": "Rhodes EP"},
    ]))
    sets_path = str(tmp_path / "sets.json")

    lib = SetLibrary.load(sets_path, legacy_rigs_path=str(rigs_path))
    assert len(lib.sets) == 1
    assert names(lib.sets[0].rigs) == ["Hammond B3", "Rhodes EP"]
    # Written through, so the next boot reads sets rather than migrating again.
    assert names(SetLibrary.load(sets_path).sets[0].rigs) == [
        "Hammond B3", "Rhodes EP"
    ]


def test_the_old_rig_file_is_left_untouched_as_a_backup(tmp_path):
    rigs_path = tmp_path / "rigs.json"
    original = json.dumps([{"name": "Hammond B3", "voice": "Hammond B3"}])
    rigs_path.write_text(original)
    SetLibrary.load(str(tmp_path / "sets.json"), legacy_rigs_path=str(rigs_path))
    assert rigs_path.read_text() == original


def test_migration_does_not_run_once_sets_exist(tmp_path):
    # Otherwise deleting every set would resurrect the pre-sets rigs.
    rigs_path = tmp_path / "rigs.json"
    rigs_path.write_text(json.dumps([{"name": "Old", "voice": "V"}]))
    sets_path = str(tmp_path / "sets.json")
    write_sets(sets_path, [song_set("Current", ["R"])])

    lib = SetLibrary.load(sets_path, legacy_rigs_path=str(rigs_path))
    assert [s.name for s in lib.sets] == ["Current"]


def test_no_legacy_rigs_means_no_set(tmp_path):
    lib = SetLibrary.load(
        str(tmp_path / "sets.json"), legacy_rigs_path=str(tmp_path / "nope.json")
    )
    assert lib.sets == []


def test_an_emptied_store_does_not_resurrect_the_old_rigs(tmp_path):
    # Deleting every set leaves a valid, empty store — not a missing one.
    rigs_path = tmp_path / "rigs.json"
    rigs_path.write_text(json.dumps([{"name": "Old", "voice": "V"}]))
    sets_path = str(tmp_path / "sets.json")
    write_sets(sets_path, [])
    assert SetLibrary.load(sets_path, legacy_rigs_path=str(rigs_path)).sets == []


# --- backups and recovery ---------------------------------------------------

def age(path, seconds):
    """Pretend `path` was written `seconds` ago."""
    t = os.path.getmtime(path) - seconds
    os.utime(path, (t, t))


def stored_names(path):
    return [s["name"] for s in json.loads(open(path).read())]


def test_a_save_keeps_the_version_it_replaces(tmp_path):
    path = str(tmp_path / "sets.json")
    write_sets(path, [song_set("First")])
    write_sets(path, [song_set("Second")])
    assert stored_names(path) == ["Second"]
    assert stored_names(path + ".1") == ["First"]


def test_backups_are_not_cycled_by_a_burst_of_saves(tmp_path):
    # A slider drag saves many times a second; that must not push every older
    # copy out and leave three backups of the last half-second.
    path = str(tmp_path / "sets.json")
    write_sets(path, [song_set("Old")])
    for n in range(10):
        write_sets(path, [song_set(f"Drag {n}")])
    assert stored_names(path + ".1") == ["Old"]
    assert backup_paths(path) == [path + ".1"]


def test_backups_shift_and_the_oldest_falls_off(tmp_path):
    path = str(tmp_path / "sets.json")
    for name in ("A", "B", "C", "D", "E"):
        write_sets(path, [song_set(name)])
        for backup in backup_paths(path):
            age(backup, BACKUP_INTERVAL_S + 1)
    assert stored_names(path) == ["E"]
    assert [stored_names(p) for p in backup_paths(path)] == [["D"], ["C"], ["B"]]


def test_a_damaged_store_is_never_copied_over_a_good_backup(tmp_path):
    path = str(tmp_path / "sets.json")
    write_sets(path, [song_set("Good")])
    write_sets(path, [song_set("Newer")])
    age(path + ".1", BACKUP_INTERVAL_S + 1)
    (tmp_path / "sets.json").write_text("{ torn")
    write_sets(path, [song_set("After")])
    assert stored_names(path + ".1") == ["Good"]


def test_a_damaged_store_is_recovered_from_its_newest_good_backup(tmp_path):
    path = str(tmp_path / "sets.json")
    (tmp_path / "sets.json.1").write_text("{ also torn")
    write_sets(str(tmp_path / "x.json"), [song_set("Gig")], backups=0)
    os.replace(tmp_path / "x.json", tmp_path / "sets.json.2")
    (tmp_path / "sets.json").write_text("{ torn")

    lib = SetLibrary.load(path)
    assert [s.name for s in lib.sets] == ["Gig"]
    assert lib.recovery and "Restored" in lib.recovery
    # Written back, so the next boot reads it normally.
    assert stored_names(path) == ["Gig"]


def test_a_damaged_store_is_kept_rather_than_overwritten(tmp_path):
    # The bug this guards: an unreadable store read as "no sets", bootstrap
    # then saved a fresh "Set 1" over it, and the original was gone.
    path = str(tmp_path / "sets.json")
    (tmp_path / "sets.json").write_text("{ torn")

    lib = SetLibrary.load(path)
    lib.bootstrap("Rhodes EP")

    kept = [p for p in tmp_path.iterdir() if ".corrupt-" in p.name]
    assert len(kept) == 1 and kept[0].read_text() == "{ torn"
    assert lib.recovery and kept[0].name in lib.recovery
    assert stored_names(path) == ["Set 1"]


def test_a_damaged_store_does_not_trigger_the_old_rig_migration(tmp_path):
    rigs_path = tmp_path / "rigs.json"
    rigs_path.write_text(json.dumps([{"name": "Old", "voice": "V"}]))
    (tmp_path / "sets.json").write_text("{ torn")
    lib = SetLibrary.load(str(tmp_path / "sets.json"), legacy_rigs_path=str(rigs_path))
    assert lib.sets == []


def test_a_store_that_is_not_a_list_counts_as_damaged(tmp_path):
    (tmp_path / "sets.json").write_text(json.dumps({"name": "not a list"}))
    lib = SetLibrary.load(str(tmp_path / "sets.json"))
    assert lib.recovery is not None


def test_a_healthy_store_reports_no_recovery(tmp_path):
    path = str(tmp_path / "sets.json")
    write_sets(path, [song_set("A")])
    assert SetLibrary.load(path).recovery is None
