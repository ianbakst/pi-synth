"""Tests for rigs (instrument + effects + level) and the chain diff. No hardware."""

import json

from synth_ui.clients.rig import (
    Rig,
    RigEffect,
    RigLibrary,
    plan,
    read_rigs,
    write_rigs,
)

REVERB = "urn:reverb"
DELAY = "urn:delay"
EQ = "urn:eq"


# --- persistence ------------------------------------------------------------

def test_round_trips_through_the_store(tmp_path):
    path = str(tmp_path / "rigs.json")
    rigs = [
        Rig(
            name="Gospel B3",
            voice="Hammond B3",
            effects=[RigEffect(REVERB, {"decay": 2.5})],
            trim_db=-1.5,
        ),
        Rig(name="Dry Piano", voice="MDA Piano"),
    ]
    assert write_rigs(path, rigs) is True
    back = read_rigs(path)
    assert [r.name for r in back] == ["Gospel B3", "Dry Piano"]
    assert back[0].effects[0].params == {"decay": 2.5}
    assert back[0].trim_db == -1.5
    assert back[1].effects == []


def test_missing_store_is_not_an_error(tmp_path):
    assert read_rigs(str(tmp_path / "nope.json")) == []


def test_malformed_store_does_not_break_the_instrument(tmp_path):
    path = tmp_path / "rigs.json"
    path.write_text("{ not json")
    assert read_rigs(str(path)) == []


def test_store_is_written_atomically(tmp_path):
    # The user's own work, unlike the shipped catalog — a half-written file on
    # power loss would lose it.
    path = str(tmp_path / "rigs.json")
    write_rigs(path, [Rig(name="A", voice="V")])
    assert not (tmp_path / "rigs.json.tmp").exists()
    assert json.loads((tmp_path / "rigs.json").read_text())[0]["name"] == "A"


# --- chain diff -------------------------------------------------------------

def test_shared_effects_are_reused_not_reloaded():
    # The point of the diff: two rigs sharing a reverb switch without
    # re-instantiating it.
    p = plan([(10, REVERB)], [RigEffect(REVERB), RigEffect(DELAY)])
    assert p.add == [RigEffect(DELAY)]
    assert p.remove == []
    assert p.order[0] == (10, RigEffect(REVERB))
    assert p.order[1] == (None, RigEffect(DELAY))


def test_effects_not_in_the_target_are_removed():
    p = plan([(10, REVERB), (11, DELAY)], [RigEffect(DELAY)])
    assert p.remove == [10]
    assert p.add == []
    assert p.order == [(11, RigEffect(DELAY))]


def test_reordering_the_same_effects_loads_nothing():
    p = plan([(10, REVERB), (11, DELAY)], [RigEffect(DELAY), RigEffect(REVERB)])
    assert p.add == [] and p.remove == []
    assert [i for i, _ in p.order] == [11, 10]


def test_identical_chain_is_a_noop():
    p = plan([(10, REVERB)], [RigEffect(REVERB)])
    assert p.is_noop is True


def test_duplicate_uris_are_matched_positionally():
    # A rig with two delays must keep both, not collapse them.
    p = plan([(10, DELAY), (11, DELAY)], [RigEffect(DELAY), RigEffect(DELAY)])
    assert p.add == [] and p.remove == []
    assert [i for i, _ in p.order] == [10, 11]


def test_growing_past_the_loaded_duplicates_adds_one():
    p = plan([(10, DELAY)], [RigEffect(DELAY), RigEffect(DELAY)])
    assert p.add == [RigEffect(DELAY)]
    assert p.order[0][0] == 10 and p.order[1][0] is None


def test_empty_target_clears_the_chain():
    p = plan([(10, REVERB), (11, EQ)], [])
    assert p.remove == [10, 11]
    assert p.order == []


# --- library ----------------------------------------------------------------

def test_new_rig_is_named_after_its_voice(tmp_path):
    lib = RigLibrary(str(tmp_path / "rigs.json"))
    rig = lib.create_from_voice("Rhodes EP")
    assert rig.name == "Rhodes EP"
    assert rig.voice == "Rhodes EP"
    assert rig.effects == []


def test_picking_the_same_voice_twice_gets_a_distinct_name(tmp_path):
    # Building two rigs on one instrument is normal (dry vs wet), so names have
    # to diverge rather than collide.
    lib = RigLibrary(str(tmp_path / "rigs.json"))
    assert lib.create_from_voice("Rhodes EP").name == "Rhodes EP"
    assert lib.create_from_voice("Rhodes EP").name == "Rhodes EP 2"
    assert lib.create_from_voice("Rhodes EP").name == "Rhodes EP 3"


def test_creating_a_rig_persists_it(tmp_path):
    path = str(tmp_path / "rigs.json")
    RigLibrary(path).create_from_voice("Hammond B3")
    assert [r.name for r in RigLibrary.load(path).rigs] == ["Hammond B3"]


def test_replace_updates_in_place_and_saves(tmp_path):
    path = str(tmp_path / "rigs.json")
    lib = RigLibrary(path)
    rig = lib.create_from_voice("Hammond B3")
    rig.effects = [RigEffect(REVERB)]
    lib.replace(rig)
    reloaded = RigLibrary.load(path)
    assert len(reloaded.rigs) == 1
    assert reloaded.rigs[0].effects[0].uri == REVERB


def test_remove_deletes_and_saves(tmp_path):
    path = str(tmp_path / "rigs.json")
    lib = RigLibrary(path)
    lib.create_from_voice("A")
    lib.create_from_voice("B")
    lib.remove("A")
    assert RigLibrary.load(path).names() == ["B"]


def test_bootstrap_creates_a_rig_on_a_fresh_card(tmp_path):
    # No rigs saved yet: the unit must still boot into something playable
    # rather than an empty list.
    lib = RigLibrary(str(tmp_path / "rigs.json"))
    rig = lib.bootstrap("General MIDI")
    assert rig is not None and rig.voice == "General MIDI"
    assert lib.names() == ["General MIDI"]


def test_bootstrap_leaves_an_existing_library_alone(tmp_path):
    lib = RigLibrary(str(tmp_path / "rigs.json"))
    lib.create_from_voice("Hammond B3")
    lib.bootstrap("General MIDI")
    assert lib.names() == ["Hammond B3"]


def test_rename_persists(tmp_path):
    path = str(tmp_path / "rigs.json")
    lib = RigLibrary(path)
    rig = lib.create_from_voice("Hammond B3")
    assert lib.rename(rig, "Gospel B3") == "Gospel B3"
    assert RigLibrary.load(path).names() == ["Gospel B3"]


def test_rename_onto_an_existing_name_is_deduplicated(tmp_path):
    # Two rigs answering to one name would make get() only ever find the first.
    lib = RigLibrary(str(tmp_path / "rigs.json"))
    lib.create_from_voice("Rhodes EP")
    other = lib.create_from_voice("Hammond B3")
    assert lib.rename(other, "Rhodes EP") == "Rhodes EP 2"
    assert sorted(lib.names()) == ["Rhodes EP", "Rhodes EP 2"]


def test_renaming_a_rig_to_its_own_name_is_a_noop(tmp_path):
    # Must not treat the rig itself as a collision and become "X 2".
    lib = RigLibrary(str(tmp_path / "rigs.json"))
    rig = lib.create_from_voice("Rhodes EP")
    assert lib.rename(rig, "Rhodes EP") == "Rhodes EP"
    assert lib.names() == ["Rhodes EP"]


def test_an_empty_rename_keeps_the_old_name(tmp_path):
    lib = RigLibrary(str(tmp_path / "rigs.json"))
    rig = lib.create_from_voice("Rhodes EP")
    assert lib.rename(rig, "   ") == "Rhodes EP"
