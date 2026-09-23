"""Tests for what a rig is, and for the chain diff. No hardware.

Where rigs *live* is set.py's problem now — a rig belongs to exactly one set and
is stored inside it — so the library tests moved to test_set.py with it.
"""

import json

from synth_ui.clients.rig import Rig, RigEffect, plan, read_rigs

REVERB = "urn:reverb"
DELAY = "urn:delay"
EQ = "urn:eq"


# --- what a rig is ----------------------------------------------------------

def test_round_trips_through_its_own_serialisation():
    rig = Rig(
        name="Gospel B3",
        voice="Hammond B3",
        effects=[RigEffect(REVERB, {"decay": 2.5}, bypassed=True)],
        voice_params={"drawbar": 8.0},
        trim_db=-1.5,
        fixed_velocity=True,
    )
    back = Rig.from_dict(rig.to_dict())
    assert back.id == rig.id
    assert back.name == "Gospel B3"
    assert back.voice_params == {"drawbar": 8.0}
    assert back.effects[0].params == {"decay": 2.5}
    assert back.effects[0].bypassed is True
    assert back.trim_db == -1.5
    assert back.fixed_velocity is True


def test_every_rig_gets_an_identity_of_its_own():
    assert Rig("A", "V").id != Rig("A", "V").id


def test_two_rigs_may_share_a_name():
    """The name is metadata. Two songs both wanting a "Rhodes" is normal, and
    they are still different rigs."""
    a, b = Rig("Rhodes", "Rhodes EP"), Rig("Rhodes", "Rhodes EP")
    assert a.name == b.name and a.id != b.id


def test_a_rig_saved_before_rigs_had_identity_is_given_one():
    rig = Rig.from_dict({"name": "Old", "voice": "MDA Piano"})
    assert rig.id
    assert rig.name == "Old"


def test_an_existing_identity_is_never_reminted():
    entry = {"id": "fixed-id", "name": "Old", "voice": "MDA Piano"}
    assert Rig.from_dict(entry).id == "fixed-id"
    assert Rig.from_dict(entry).id == "fixed-id"


def test_rigs_saved_before_fixed_velocity_existed_still_load():
    # A rig outlives the feature set that wrote it: an older entry has no such
    # key and must read as "as struck".
    rig = Rig.from_dict({"name": "Old", "voice": "MDA Piano", "trim_db": 0.0})
    assert rig.fixed_velocity is False
    assert rig.voice_params == {}


# --- reading the pre-sets store (migration only) ----------------------------

def test_the_legacy_store_still_reads(tmp_path):
    path = tmp_path / "rigs.json"
    path.write_text(json.dumps([{"name": "Old", "voice": "MDA Piano"}]))
    rigs = read_rigs(str(path))
    assert [r.name for r in rigs] == ["Old"]
    assert rigs[0].id


def test_missing_store_is_not_an_error(tmp_path):
    assert read_rigs(str(tmp_path / "nope.json")) == []


def test_malformed_store_does_not_break_the_instrument(tmp_path):
    path = tmp_path / "rigs.json"
    path.write_text("{ not json")
    assert read_rigs(str(path)) == []


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


