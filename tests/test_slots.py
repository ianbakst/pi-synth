"""Tests for InstrumentSlots — instrument residency. No hardware."""

from unittest.mock import MagicMock, call

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
    mh.remove_plugin.assert_called_once_with(9)


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
    mh.remove_plugin.assert_called_once_with(1)   # V1 was least recently used


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
    assert mh.remove_plugin.call_count == 2
