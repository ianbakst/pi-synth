"""Tests for LV2 plugin resolution + installed-plugin discovery. No hardware."""

from synth_ui.clients.lv2 import LV2World, spec_for

SFIZZ_URI = "http://sfztools.github.io/sfizz"


# --- spec_for ---------------------------------------------------------------

def test_legacy_engine_resolves_to_built_in_spec():
    spec = spec_for("sfizz")
    assert spec.uri == SFIZZ_URI
    assert spec.file_property == f"{SFIZZ_URI}:sfzfile"


def test_generic_engine_takes_uri_from_the_voice():
    spec = spec_for("modhost", uri="http://drobilla.net/plugins/mda/EPiano")
    assert spec.uri == "http://drobilla.net/plugins/mda/EPiano"
    assert spec.file_property == ""


def test_generic_engine_without_a_uri_is_unresolvable():
    assert spec_for("modhost") is None


def test_process_engines_are_not_lv2():
    assert spec_for("fluidsynth") is None
    assert spec_for("setbfree") is None


def test_manifest_overrides_win_over_the_built_in_spec():
    # How an unconfirmed property (e.g. dexed's .syx) gets supplied from JSON
    # once it's been found on hardware, with no code change.
    spec = spec_for("dexed", file_property="urn:dexed:sysex")
    assert spec.uri == "https://asb2m10.github.io/dexed"
    assert spec.file_property == "urn:dexed:sysex"


# --- LV2World ---------------------------------------------------------------

def world_with(*uris, rc=0):
    return LV2World(runner=lambda cmd: (rc, "\n".join(uris)))


def test_world_reports_installed_plugins():
    world = world_with(SFIZZ_URI, "http://drobilla.net/plugins/mda/EPiano")
    assert world.has(SFIZZ_URI)
    assert not world.has("http://example.org/nope")


def test_world_caches_lv2ls():
    calls = []

    def runner(cmd):
        calls.append(cmd)
        return 0, SFIZZ_URI

    world = LV2World(runner=runner)
    world.has(SFIZZ_URI)
    world.has(SFIZZ_URI)
    assert len(calls) == 1
    world.refresh()
    assert len(calls) == 2


def test_missing_lv2ls_does_not_rule_anything_out():
    # A dev machine has no lv2ls; that must not grey out the whole library.
    world = world_with(rc=127)
    assert world.readable is False
    assert world.has("http://example.org/anything") is True
