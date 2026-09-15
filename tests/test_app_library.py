"""The voice library must be built once, never on a per-frame lookup.

RigList.draw asks "is this rig usable?" for every visible row, every frame.
When answering that rebuilt the library — scanning every soundfont on the SD
card — the UI needed more than a second of work per second of wall time, pinned
a core, and flooded the SD card with reads.
"""

from synth_ui.clients.rig import Rig
from synth_ui.clients.voice import Voice
from synth_ui.ui import app as app_module
from synth_ui.ui.app import SynthUI


def _bare_ui(monkeypatch, voices):
    """A SynthUI with only its library wiring — no display, no audio stack."""
    calls = {"n": 0}

    def counting_load_voices(manifest, soundfont_dir, trims_path=None):
        calls["n"] += 1
        return list(voices)

    monkeypatch.setattr(app_module, "load_voices", counting_load_voices)
    ui = SynthUI.__new__(SynthUI)
    ui._library = {}
    ui._reload_library()
    return ui, calls


def test_per_frame_lookups_never_rescan_the_library(monkeypatch):
    rhodes = Voice("Rhodes EP", "modhost", "", "EP", uri="urn:mda:epiano")
    ui, calls = _bare_ui(monkeypatch, [rhodes])
    rig = Rig(name="Wet Rhodes", voice="Rhodes EP")

    # Three seconds of drawing five rows at 30 fps.
    for _ in range(30 * 3 * 5):
        ui._rig_unavailable(rig)

    assert calls["n"] == 1


def test_lookup_finds_voices_from_the_cache(monkeypatch):
    rhodes = Voice("Rhodes EP", "modhost", "", "EP", uri="urn:mda:epiano")
    ui, _ = _bare_ui(monkeypatch, [rhodes])
    assert ui._voice_for("Rhodes EP") is rhodes
    assert ui._rig_unavailable(Rig(name="Gone", voice="Nope")) == (
        "voice 'Nope' not in the library"
    )


def test_reloading_picks_up_new_fonts(monkeypatch):
    # The library is rebuilt when it can change (voice picker, USB import), so
    # a font dropped in after boot still appears.
    voices = [Voice("A", "modhost", "", "", uri="urn:a")]
    ui, calls = _bare_ui(monkeypatch, voices)
    voices.append(Voice("B", "modhost", "", "", uri="urn:b"))
    assert ui._voice_for("B") is None
    ui._reload_library()
    assert ui._voice_for("B") is not None
    assert calls["n"] == 2
