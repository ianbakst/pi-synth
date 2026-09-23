"""Tests for InstrumentSlots — instrument residency. No hardware."""

from unittest.mock import MagicMock, call

from synth_ui.clients.lv2 import ControlPort, PortDefaults
from synth_ui.clients.slots import InstrumentSlots
from synth_ui.clients.voice import Voice

EPIANO_URI = "http://drobilla.net/plugins/mda/EPiano"
JX10_URI = "http://drobilla.net/plugins/mda/JX10"
SFIZZ_URI = "http://sfztools.github.io/sfizz"
SFZ_PROPERTY = f"{SFIZZ_URI}:sfzfile"


def synth(name, uri, **kw):
    return Voice(
        name=name, engine="modhost", path="", category="", uri=uri, resident=True, **kw
    )


def sampler(name, path):
    """A large sample library: not resident, so it uses the scratch slot."""
    return Voice(name=name, engine="sfizz", path=path, category="Piano")


def defaults(**controls):
    """A PortDefaults reporting these symbol=default controls, without running
    lv2info. Slots consults it only to undo a control the incoming voice does
    not mention."""
    ports = [ControlPort(s, s, 0.0, 1.0, v) for s, v in controls.items()]
    return PortDefaults(read=lambda uri: ports)


def mh_ok():
    mh = MagicMock()
    mh.load_plugin.return_value = True
    mh.patch_set.return_value = True
    mh.preset_load.return_value = True
    return mh


# --- allocation -------------------------------------------------------------

def test_resident_voices_each_get_their_own_slot():
    mh = mh_ok()
    slots = InstrumentSlots(mh)
    assert slots.acquire(synth("A", EPIANO_URI)) == 0
    assert slots.acquire(synth("B", JX10_URI)) == 1
    assert slots.loaded_instances() == [0, 1]


def test_reacquiring_a_loaded_voice_costs_nothing():
    # The whole point: a switch back to a resident instrument must not talk to
    # mod-host at all.
    mh = mh_ok()
    slots = InstrumentSlots(mh)
    voice = synth("A", EPIANO_URI)
    slots.acquire(voice)
    mh.reset_mock()
    assert slots.acquire(voice) == 0
    mh.load_plugin.assert_not_called()
    mh.remove_plugin.assert_not_called()


def test_is_loaded_reports_whether_a_switch_would_be_instant():
    slots = InstrumentSlots(mh_ok())
    voice = synth("A", EPIANO_URI)
    assert slots.is_loaded(voice) is False
    slots.acquire(voice)
    assert slots.is_loaded(voice) is True


def test_non_resident_voices_share_the_scratch_slot():
    # Several multi-GB libraries resident at once is what would exhaust RAM, so
    # they take turns in the last slot and reload.
    mh = mh_ok()
    slots = InstrumentSlots(mh)
    assert slots.acquire(sampler("Piano A", "/sfz/a.sfz")) == 9
    assert slots.acquire(sampler("Piano B", "/sfz/b.sfz")) == 9
    # Only ever one of them held at a time.
    assert slots.loaded_instances() == [9]
    assert mh.remove_plugin.call_args_list[-1].args == (9,)


def test_a_sampler_never_evicts_the_resident_set():
    mh = mh_ok()
    slots = InstrumentSlots(mh)
    slots.acquire(synth("A", EPIANO_URI))
    slots.acquire(sampler("Piano", "/sfz/a.sfz"))
    assert slots.loaded_instances() == [0, 9]


def test_same_plugin_different_instrument_files_are_different_voices():
    # Two sfizz voices are one plugin but different pianos — treating them as
    # interchangeable would leave you playing the wrong one.
    mh = mh_ok()
    slots = InstrumentSlots(mh)
    slots.acquire(sampler("Piano A", "/sfz/a.sfz"))
    mh.reset_mock()
    slots.acquire(sampler("Piano B", "/sfz/b.sfz"))
    mh.load_plugin.assert_called_once_with(SFIZZ_URI, 9)
    mh.patch_set.assert_called_once_with(9, SFZ_PROPERTY, "/sfz/b.sfz")


def test_full_resident_set_evicts_least_recently_used():
    mh = mh_ok()
    slots = InstrumentSlots(mh)
    voices = [synth(f"V{i}", f"urn:plugin:{i}") for i in range(9)]
    for voice in voices:
        slots.acquire(voice)
    assert slots.loaded_instances() == list(range(9))

    slots.acquire(voices[0])          # touch 0 so it isn't the victim
    mh.reset_mock()
    slots.acquire(synth("New", "urn:plugin:new"))
    # V1 was least recently used, so its slot is the one reused.
    assert slots.instance_of("V1") is None
    assert slots.instance_of("New") == 1


def test_a_voice_with_no_uri_is_refused():
    mh = mh_ok()
    slots = InstrumentSlots(mh)
    assert slots.acquire(Voice("Broken", "modhost", "", "")) is None
    mh.load_plugin.assert_not_called()


def test_a_refused_plugin_does_not_claim_a_slot():
    mh = MagicMock()
    mh.load_plugin.return_value = False
    slots = InstrumentSlots(mh)
    assert slots.acquire(synth("A", EPIANO_URI)) is None
    assert slots.loaded_instances() == []


# --- setup applied at load --------------------------------------------------

def test_preset_then_params_are_applied_after_loading():
    # Preset first, params second: a voice starts from a stock LV2 preset and
    # overrides individual controls. This is what lets one plugin back many
    # voices (e.g. several organ registrations).
    mh = mh_ok()
    slots = InstrumentSlots(mh)
    slots.acquire(
        synth("Bright", EPIANO_URI, preset="urn:mda:bright", params={"decay": 0.6})
    )
    mh.preset_load.assert_called_once_with(0, "urn:mda:bright")
    mh.set_param.assert_called_once_with(0, "decay", "0.6")


def test_a_failed_preset_does_not_lose_the_instrument():
    # A stale preset URI shouldn't cost the whole voice — the plugin is loaded
    # and playable at its defaults.
    mh = mh_ok()
    mh.preset_load.return_value = False
    slots = InstrumentSlots(mh)
    assert slots.acquire(synth("A", EPIANO_URI, preset="urn:gone")) == 0


def test_the_instrument_file_goes_through_patch_set():
    # An SFZ path is an atom-based patch property; param_set silently no-ops on
    # it, which is what made sfizz load but stay silent.
    mh = mh_ok()
    slots = InstrumentSlots(mh)
    slots.acquire(sampler("Piano", "/sfz/a.sfz"))
    mh.patch_set.assert_called_once_with(9, SFZ_PROPERTY, "/sfz/a.sfz")


# --- activation -------------------------------------------------------------

def test_activate_unbypasses_one_and_bypasses_the_rest():
    mh = mh_ok()
    slots = InstrumentSlots(mh)
    slots.acquire(synth("A", EPIANO_URI))
    slots.acquire(synth("B", JX10_URI))
    mh.reset_mock()
    slots.activate(1)
    assert mh.bypass.call_args_list == [call(0, True), call(1, False)]


def test_clear_unloads_everything():
    # Used when mod-host restarted underneath us and the bookkeeping is stale.
    mh = mh_ok()
    slots = InstrumentSlots(mh)
    slots.acquire(synth("A", EPIANO_URI))
    slots.acquire(synth("B", JX10_URI))
    slots.clear()
    assert slots.loaded_instances() == []
    removed = {c.args[0] for c in mh.remove_plugin.call_args_list}
    assert {0, 1} <= removed


def test_eviction_never_takes_the_scratch_slot():
    # The scratch slot is not a resident slot. Evicting it would put a resident
    # voice at 9, where the next sample-library voice immediately drops it —
    # a voice that worked a minute ago silently stops loading.
    mh = mh_ok()
    slots = InstrumentSlots(mh)
    slots.acquire(sampler("Big Piano", "/sfz/a.sfz"))          # takes slot 9 first
    for i in range(9):
        slots.acquire(synth(f"V{i}", f"urn:plugin:{i}"))       # fills 0-8

    slots.acquire(synth("New", "urn:plugin:new"))
    assert slots.instance_of("New") != 9
    assert slots.instance_of("New") in range(0, 9)


# --- one plugin, many voices ------------------------------------------------

def test_two_voices_on_one_plugin_each_get_their_own_settings():
    # The library multiplier: one plugin backs several voices, told apart only
    # by their params. Matching on the URI alone handed the second voice the
    # first one's instance untouched — the right name, the wrong sound, and
    # nothing in the log to say so.
    mh = mh_ok()
    slots = InstrumentSlots(mh, defaults=defaults(cutoff=0.9))
    slots.acquire(synth("Dark Pad", JX10_URI, params={"cutoff": 0.2}))
    mh.reset_mock()

    assert slots.acquire(synth("Bright Lead", JX10_URI, params={"cutoff": 0.8})) == 0
    mh.load_plugin.assert_not_called()          # still no instantiation
    mh.set_param.assert_called_once_with(0, "cutoff", "0.8")


def test_a_control_the_next_voice_never_mentions_goes_back_to_default():
    # The leak this exists to stop: a cutoff dialled down for one rig would
    # otherwise follow the instrument into every other rig on it.
    mh = mh_ok()
    slots = InstrumentSlots(mh, defaults=defaults(cutoff=0.9))
    slots.acquire(synth("Dark Pad", JX10_URI, params={"cutoff": 0.2}))
    mh.reset_mock()

    slots.acquire(synth("Stock JX10", JX10_URI))
    mh.set_param.assert_called_once_with(0, "cutoff", "0.9")


def test_reconciling_only_writes_what_differs():
    mh = mh_ok()
    slots = InstrumentSlots(mh, defaults=defaults())
    slots.acquire(synth("A", JX10_URI, params={"cutoff": 0.2, "reso": 0.5}))
    mh.reset_mock()

    slots.acquire(synth("B", JX10_URI, params={"cutoff": 0.2, "reso": 0.7}))
    mh.set_param.assert_called_once_with(0, "reso", "0.7")


def test_reconciling_an_unchanged_voice_never_reads_the_plugin():
    # lv2info is a subprocess. Selecting the rig you're already on must not
    # pay for one — nothing has to be undone, so nothing has to be looked up.
    reads = []
    mh = mh_ok()
    slots = InstrumentSlots(
        mh, defaults=PortDefaults(read=lambda uri: reads.append(uri) or [])
    )
    voice = synth("A", JX10_URI, params={"cutoff": 0.2})
    slots.acquire(voice)
    slots.acquire(voice)
    assert reads == []


def test_a_new_preset_reapplies_every_param_over_it():
    # A preset rewrites controls we have no record of, so the values we thought
    # were already set can't be trusted to skip a write.
    mh = mh_ok()
    slots = InstrumentSlots(mh, defaults=defaults())
    slots.acquire(synth("Jazz", EPIANO_URI, preset="urn:jazz", params={"decay": 0.4}))
    mh.reset_mock()

    slots.acquire(synth("Rock", EPIANO_URI, preset="urn:rock", params={"decay": 0.4}))
    mh.preset_load.assert_called_once_with(0, "urn:rock")
    mh.set_param.assert_called_once_with(0, "decay", "0.4")


def test_a_live_edit_is_remembered_so_the_next_switch_undoes_it():
    # The params screen writes straight to the plugin while it plays. A slot
    # that didn't know would skip the write that puts the control back.
    mh = mh_ok()
    slots = InstrumentSlots(mh, defaults=defaults(cutoff=0.9))
    slots.acquire(synth("A", JX10_URI))
    slots.note_param(0, "cutoff", 0.3)
    mh.reset_mock()

    slots.acquire(synth("B", JX10_URI))
    mh.set_param.assert_called_once_with(0, "cutoff", "0.9")


def test_loading_clears_the_slot_first_so_a_desync_self_heals():
    # mod-host refuses `add` on an instance it already holds. If it restarted
    # under us our bookkeeping is stale, and every subsequent load would fail
    # on a slot we believe is free.
    mh = mh_ok()
    slots = InstrumentSlots(mh)
    slots.acquire(synth("A", EPIANO_URI))
    order = [c[0] for c in mh.method_calls if c[0] in ("remove_plugin", "load_plugin")]
    assert order[:2] == ["remove_plugin", "load_plugin"]
