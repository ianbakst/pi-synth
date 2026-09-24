"""The browser editor: what it does to sets and rigs, and the server in front.

The controller is driven against a real SetLibrary on disk and a stub engine,
so "saved" means written to the store and read back, and "heard" means the
engine was asked.
"""

import http.client
import json
import queue
import threading

import pygame
import pytest

from synth_ui.clients.effects_catalog import EffectCatalogEntry
from synth_ui.clients.effects_rack import Effect
from synth_ui.clients.lv2 import ControlPort
from synth_ui.clients.rig import Rig, RigEffect
from synth_ui.clients.set import SetLibrary, SongSet
from synth_ui.clients.voice import Voice
from synth_ui.server import EditorError
from synth_ui.server.http import MAX_BAD_PINS, EditorServer
from synth_ui.ui.app import SynthUI
from synth_ui.ui.editor import EditorController
from synth_ui.ui.screens.rigs import RigsScreen


@pytest.fixture(autouse=True, scope="module")
def _pygame():
    pygame.init()
    pygame.display.set_mode((800, 480))
    yield
    pygame.quit()


class Engine:
    """Records what the editor asked of the audio side."""

    def __init__(self):
        self.live: list[Effect] = []
        self.voice: dict[str, float] = {}
        self.calls: list[tuple] = []
        self._next = 10

    def effects(self):
        return list(self.live)

    def add_effect(self, uri, index=None):
        self.calls.append(("add", uri, index))
        effect = Effect(self._next, uri)
        self._next += 1
        self.live.insert(len(self.live) if index is None else index, effect)
        return effect.instance

    def remove_effect(self, instance):
        self.calls.append(("remove", instance))
        self.live = [e for e in self.live if e.instance != instance]

    def move_effect(self, source, target):
        self.calls.append(("move", source, target))
        self.live.insert(target, self.live.pop(source))
        return True

    def set_effect_bypass(self, instance, bypassed):
        self.calls.append(("bypass", instance, bypassed))
        next(e for e in self.live if e.instance == instance).bypassed = bypassed
        return True

    def set_effect_param(self, instance, symbol, value):
        self.calls.append(("param", instance, symbol, value))
        effect = next(e for e in self.live if e.instance == instance)
        effect.params[symbol] = float(value)
        return True

    def voice_params(self):
        return dict(self.voice)

    def set_voice_param(self, symbol, value):
        self.calls.append(("voice_param", symbol, value))
        self.voice[symbol] = float(value)
        return True

    def load_voice(self, voice):
        self.calls.append(("load_voice", voice.name))
        self.voice = dict(voice.params)
        return True

    def load_rig(self, rig, voice):
        self.calls.append(("load_rig", rig.name))
        return True

    def set_rig_trim(self, db):
        self.calls.append(("trim", db))

    def fixed_velocity_available(self):
        return True

    def set_fixed_velocity(self, enabled):
        self.calls.append(("fixed_velocity", enabled))
        return True

    def effect_controls(self, uri):
        self.calls.append(("controls", uri))
        return [ControlPort("mix", "Mix", 0.0, 1.0, 0.5)]

    def voice_controls(self, voice):
        return [ControlPort("cutoff", "Cutoff", 0.0, 1.0, 0.9)]


class Home:
    def refresh(self, sets):
        pass

    def set_active(self, song_set):
        pass


VOICES = {
    "Rhodes EP": Voice("Rhodes EP", "modhost", "", "Electric Piano", uri="urn:ep",
                       params={"tone": 0.5}),
    "Organ": Voice("Organ", "modhost", "", "Organ", uri="urn:organ"),
}
CATALOG = [
    EffectCatalogEntry("Reverb", "urn:rev", "Reverb"),
    EffectCatalogEntry("Delay", "urn:dly", "Delay"),
]


@pytest.fixture
def ui(tmp_path):
    """A UI with two sets on disk; 'Live' in 'Song A' is being played."""
    live = Rig("Live", "Rhodes EP", effects=[RigEffect("urn:rev")])
    stored = Rig("Stored", "Organ",
                 effects=[RigEffect("urn:rev"), RigEffect("urn:dly")])
    song_a = SongSet("Song A", rigs=[live, stored])
    song_b = SongSet("Song B")
    library = SetLibrary(str(tmp_path / "sets.json"), [song_a, song_b])
    library.save()

    app = SynthUI.__new__(SynthUI)
    app._engine = Engine()
    app._engine.live = [Effect(1, "urn:rev")]
    app._engine.voice = {"tone": 0.5}
    app._sets = library
    app._active_set = song_a
    app._library = dict(VOICES)
    app._catalog = CATALOG
    app._home = Home()
    app._rig_screen = RigsScreen(
        rigs=song_a.rigs, on_load_rig=lambda r: True, on_edit_rig=lambda r: None,
        on_new=lambda: None, on_edit=lambda: None, on_back=lambda: None,
        on_gain_change=lambda g: None,
    )
    app._rig_screen._active_rig = live
    app._effects_screen = None
    app._params_screen = None
    app._settings_screen = None
    app._catalog_screen = None
    app._insert_at = None
    app.screen = app._rig_screen
    app._commands = queue.SimpleQueue()
    app._ui_thread = threading.current_thread()
    app._in_background = lambda work: work()
    app._editor = EditorController(app)
    return app


def _reloaded(app) -> SetLibrary:
    """The store as the next boot will see it."""
    return SetLibrary.load(app._sets.path)


def _rig(library, name):
    return next(r for s in library.sets for r in s.rigs if r.name == name)


def _ids(app):
    a = app._sets.sets[0]
    return a, a.rigs[0], a.rigs[1]


class TestState:
    def test_every_set_and_rig_is_listed(self, ui):
        state = ui._editor.state()
        assert [s["name"] for s in state["sets"]] == ["Song A", "Song B"]
        assert [r["name"] for r in state["sets"][0]["rigs"]] == ["Live", "Stored"]

    def test_the_active_rig_shows_what_is_live_not_what_was_saved(self, ui):
        ui._engine.live[0].params["mix"] = 0.8
        ui._engine.voice["tone"] = 0.9
        live = ui._editor.state()["sets"][0]["rigs"][0]
        assert live["active"]
        assert live["effects"][0]["params"] == {"mix": 0.8}
        assert live["voice_values"]["tone"] == 0.9

    def test_a_stored_rig_shows_its_catalog_values_under_its_own(self, ui):
        _, _, stored = _ids(ui)
        stored.voice_params = {"drawbar": 8.0}
        rig = ui._editor.state()["sets"][0]["rigs"][1]
        assert not rig["active"]
        assert rig["voice_values"] == {"drawbar": 8.0}

    def test_it_is_plain_json(self, ui):
        json.dumps(ui._editor.state())


class TestStoredRigs:
    """Editing a rig that isn't playing is data: saved, and nothing heard."""

    def test_adding_and_removing_effects_is_saved(self, ui):
        _, _, stored = _ids(ui)
        ui._editor.add_effect(stored.id, "urn:dly", 0)
        ui._editor.remove_effect(stored.id, 2)
        assert [e.uri for e in _rig(_reloaded(ui), "Stored").effects] == [
            "urn:dly", "urn:rev"
        ]
        assert not [c for c in ui._engine.calls if c[0] in ("add", "remove")]

    def test_moving_an_effect(self, ui):
        _, _, stored = _ids(ui)
        ui._editor.move_effect(stored.id, 0, 1)
        assert [e.uri for e in _rig(_reloaded(ui), "Stored").effects] == [
            "urn:dly", "urn:rev"
        ]

    def test_params_and_bypass(self, ui):
        _, _, stored = _ids(ui)
        ui._editor.update_effect(stored.id, 1, bypassed=True, params={"time": 0.3})
        saved = _rig(_reloaded(ui), "Stored").effects[1]
        assert saved.bypassed and saved.params == {"time": 0.3}
        assert ui._engine.calls == []

    def test_a_voice_param_is_stored_as_a_difference_from_the_catalog(self, ui):
        a = ui._sets.sets[0]
        rig = a.create_from_voice("Rhodes EP")
        ui._editor.set_voice_param(rig.id, "tone", 0.7)
        assert _rig(_reloaded(ui), rig.name).voice_params == {"tone": 0.7}
        ui._editor.set_voice_param(rig.id, "tone", 0.5)   # back to the catalog's
        assert _rig(_reloaded(ui), rig.name).voice_params == {}

    def test_changing_the_instrument_drops_the_old_patch(self, ui):
        _, _, stored = _ids(ui)
        stored.voice_params = {"drawbar": 8.0}
        result = ui._editor.update_rig(stored.id, voice="Rhodes EP")
        assert result == {"pending": False}
        saved = _rig(_reloaded(ui), "Stored")
        assert (saved.voice, saved.voice_params) == ("Rhodes EP", {})
        assert not [c for c in ui._engine.calls if c[0] == "load_voice"]

    def test_level_and_velocity(self, ui):
        _, _, stored = _ids(ui)
        ui._editor.update_rig(stored.id, trim_db=-3.0, fixed_velocity=True)
        saved = _rig(_reloaded(ui), "Stored")
        assert (saved.trim_db, saved.fixed_velocity) == (-3.0, True)
        assert ui._engine.calls == []


class TestTheActiveRig:
    """Edits to the rig being played are heard, then saved."""

    def test_a_param_reaches_the_engine_and_the_store(self, ui):
        _, live, _ = _ids(ui)
        ui._editor.update_effect(live.id, 0, params={"mix": 0.2})
        assert ("param", 1, "mix", "0.2") in ui._engine.calls
        assert _rig(_reloaded(ui), "Live").effects[0].params == {"mix": 0.2}

    def test_a_voice_param_is_heard(self, ui):
        _, live, _ = _ids(ui)
        ui._editor.set_voice_param(live.id, "tone", 0.9)
        assert ("voice_param", "tone", "0.9") in ui._engine.calls
        assert _rig(_reloaded(ui), "Live").voice_params == {"tone": 0.9}

    def test_level_is_heard(self, ui):
        _, live, _ = _ids(ui)
        ui._editor.update_rig(live.id, trim_db=2.0)
        assert ("trim", 2.0) in ui._engine.calls

    def test_adding_an_effect_loads_it_in_the_background(self, ui):
        _, live, _ = _ids(ui)
        assert ui._editor.add_effect(live.id, "urn:dly") == {"pending": True}
        assert ui._editor.busy
        with pytest.raises(EditorError) as busy:
            ui._editor.update_effect(live.id, 0, bypassed=True)
        assert busy.value.status == 409
        ui._drain_commands()   # the worker's hand-back to the UI thread
        assert not ui._editor.busy
        assert [e.uri for e in _rig(_reloaded(ui), "Live").effects] == [
            "urn:rev", "urn:dly"
        ]

    def test_removing_an_effect(self, ui):
        _, live, _ = _ids(ui)
        ui._editor.remove_effect(live.id, 0)
        ui._drain_commands()
        assert ("remove", 1) in ui._engine.calls
        assert _rig(_reloaded(ui), "Live").effects == []

    def test_changing_the_instrument_loads_it(self, ui):
        _, live, _ = _ids(ui)
        assert ui._editor.update_rig(live.id, voice="Organ") == {"pending": True}
        ui._drain_commands()
        assert ("load_voice", "Organ") in ui._engine.calls
        assert _rig(_reloaded(ui), "Live").voice == "Organ"

    def test_the_chain_screen_is_rebuilt_rather_than_left_stale(self, ui):
        """It holds its own copy of the level; left stale, leaving it would
        write the old value back over the editor's."""
        _, live, _ = _ids(ui)
        ui._show_effects_screen()
        before = ui._effects_screen
        ui._editor.update_rig(live.id, trim_db=-6.0)
        assert ui._effects_screen is not before
        assert ui._effects_screen.trim_slider.value == -6.0

    def test_there_is_no_way_to_make_a_rig_active(self, ui):
        """What the keyboard plays is chosen at the instrument."""
        a, _, _ = _ids(ui)
        ui._editor.create_rig(a.id, "Organ")
        assert not [c for c in ui._engine.calls if c[0] in ("load_rig", "load_voice")]
        assert ui._rig_screen.active_rig.name == "Live"


class TestSetsAndRigs:
    def test_create_rename_move_delete_a_set(self, ui):
        new = ui._editor.create_set("Encore")["id"]
        ui._editor.rename_set(new, "Encore 2")
        ui._editor.move_set(new, 0)
        assert [s.name for s in _reloaded(ui).sets] == ["Encore 2", "Song A", "Song B"]
        ui._editor.delete_set(new)
        assert [s.name for s in _reloaded(ui).sets] == ["Song A", "Song B"]

    def test_deleting_the_set_being_played_keeps_the_sound(self, ui):
        a, _, _ = _ids(ui)
        ui._editor.delete_set(a.id)
        assert ui._active_set is None
        assert ui._rig_screen.active_rig is None
        assert not [c for c in ui._engine.calls if c[0] in ("remove", "load_voice")]

    def test_deleting_the_rig_being_played(self, ui):
        _, live, _ = _ids(ui)
        ui._show_effects_screen()
        ui._editor.delete_rig(live.id)
        assert ui._rig_screen.active_rig is None
        assert ui.screen is ui._rig_screen
        assert [r.name for r in _reloaded(ui).sets[0].rigs] == ["Stored"]

    def test_copying_the_active_rig_takes_what_is_live(self, ui):
        _, live, _ = _ids(ui)
        b = ui._sets.sets[1]
        ui._engine.live[0].params["mix"] = 0.1
        copy_id = ui._editor.copy_rig(live.id, b.id)["id"]
        copy = _reloaded(ui).sets[1].rigs[0]
        assert copy.id == copy_id != live.id
        assert copy.effects[0].params == {"mix": 0.1}

    def test_a_copy_into_the_same_set_gets_its_own_name(self, ui):
        a, _, stored = _ids(ui)
        ui._editor.copy_rig(stored.id, a.id)
        assert [r.name for r in _reloaded(ui).sets[0].rigs] == [
            "Live", "Stored", "Stored 2"
        ]

    def test_reordering_rigs(self, ui):
        _, _, stored = _ids(ui)
        ui._editor.move_rig(stored.id, 0)
        assert [r.name for r in _reloaded(ui).sets[0].rigs] == ["Stored", "Live"]


class TestImportExport:
    def test_an_export_imports_as_new_sets(self, ui):
        exported = ui._editor.export()
        assert ui._editor.import_sets(exported) == {"added": 2}
        library = _reloaded(ui)
        assert [s.name for s in library.sets] == [
            "Song A", "Song B", "Song A 2", "Song B 2"
        ]
        ids = [r.id for s in library.sets for r in s.rigs]
        assert len(ids) == len(set(ids))

    def test_import_never_touches_what_is_playing(self, ui):
        ui._editor.import_sets(ui._editor.export())
        assert ui._rig_screen.active_rig.name == "Live"
        assert ui._engine.calls == []

    def test_a_non_export_is_refused(self, ui):
        with pytest.raises(EditorError):
            ui._editor.import_sets([{"rigs": "nonsense"}])
        with pytest.raises(EditorError):
            ui._editor.import_sets({"not": "a list"})


class TestRefusals:
    def test_unknown_things_are_404(self, ui):
        a, _, stored = _ids(ui)
        for call in (
            lambda: ui._editor.rename_set("nope", "x"),
            lambda: ui._editor.delete_rig("nope"),
            lambda: ui._editor.create_rig(a.id, "No Such Voice"),
            lambda: ui._editor.add_effect(stored.id, "urn:not-in-catalog"),
            lambda: ui._editor.remove_effect(stored.id, 9),
        ):
            with pytest.raises(EditorError) as err:
                call()
            assert err.value.status == 404

    def test_a_blank_name_is_refused(self, ui):
        a, _, _ = _ids(ui)
        with pytest.raises(EditorError):
            ui._editor.rename_set(a.id, "   ")


class TestControls:
    def test_read_once_per_plugin(self, ui):
        first = ui._editor.effect_controls("urn:rev")
        ui._editor.effect_controls("urn:rev")
        assert [c for c in ui._engine.calls if c[0] == "controls"] == [
            ("controls", "urn:rev")
        ]
        assert first["controls"][0]["symbol"] == "mix"
        assert first["eq"] is None


# --- the server ----------------------------------------------------------------

@pytest.fixture
def server(ui):
    srv = EditorServer(ui._editor, dispatch=lambda fn: fn(), port=0, host="127.0.0.1")
    assert srv.start()
    yield srv
    srv.stop()


def _call(srv, method, path, body=None, cookie=None):
    conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=5)
    headers = {"Cookie": cookie} if cookie else {}
    data = None
    if body is not None:
        data = json.dumps(body)
        headers["Content-Type"] = "application/json"
    conn.request(method, path, body=data, headers=headers)
    res = conn.getresponse()
    raw = res.read()
    conn.close()
    try:
        payload = json.loads(raw)
    except ValueError:
        payload = raw
    return res.status, payload, res.getheader("Set-Cookie")


def _login(srv):
    status, _, cookie = _call(srv, "POST", "/api/login", {"pin": srv.pin})
    assert status == 200
    return cookie.split(";")[0]


class TestServer:
    def test_the_page_is_served_without_a_pin(self, server):
        status, body, _ = _call(server, "GET", "/")
        assert status == 200 and b"Synth Editor" in body

    def test_nothing_outside_the_web_folder_is_reachable(self, server):
        for path in ("/../server/http.py", "/%2e%2e/config.py", "/.hidden"):
            assert _call(server, "GET", path)[0] == 404

    def test_the_api_needs_a_pin(self, server):
        assert _call(server, "GET", "/api/state")[0] == 401

    def test_a_wrong_pin_is_refused(self, server):
        wrong = "0000" if server.pin != "0000" else "1111"
        assert _call(server, "POST", "/api/login", {"pin": wrong})[0] == 401

    def test_too_many_wrong_pins_replace_it(self, server):
        original = server.pin
        wrong = "0000" if original != "0000" else "1111"
        for _ in range(MAX_BAD_PINS):
            _call(server, "POST", "/api/login", {"pin": wrong})
        # A new PIN may by chance equal the old; the counter reset shows either way.
        assert server._bad_pins == 0

    def test_with_the_pin_the_state_comes_back(self, server):
        cookie = _login(server)
        status, state, _ = _call(server, "GET", "/api/state", cookie=cookie)
        assert status == 200
        assert state["sets"][0]["name"] == "Song A"

    def test_a_change_round_trips(self, server, ui):
        cookie = _login(server)
        a, _, stored = _ids(ui)
        status, _, _ = _call(server, "PATCH", f"/api/rigs/{stored.id}",
                             {"name": "Renamed"}, cookie)
        assert status == 200
        assert _rig(_reloaded(ui), "Renamed")

    def test_controller_errors_carry_their_status(self, server):
        cookie = _login(server)
        assert _call(server, "DELETE", "/api/rigs/nope", cookie=cookie)[0] == 404

    def test_bad_requests_are_400_not_500(self, server, ui):
        cookie = _login(server)
        a, _, _ = _ids(ui)
        # Missing "to".
        assert _call(server, "POST", f"/api/sets/{a.id}/move", {}, cookie)[0] == 400
        # A list where an object belongs.
        assert _call(server, "POST", "/api/sets", [1, 2], cookie)[0] == 400

    def test_an_unknown_endpoint(self, server):
        cookie = _login(server)
        assert _call(server, "GET", "/api/nope", cookie=cookie)[0] == 404

    def test_stopping_forgets_every_session(self, server):
        cookie = _login(server)
        server.stop()
        assert not server.authorised(cookie.split("=", 1)[1])

    def test_idle_time_counts_from_the_last_request(self, server):
        import time

        _call(server, "GET", "/api/state")
        assert server.idle_for(time.monotonic()) < 1.0


def test_the_ui_thread_runs_queued_work_in_order(ui):
    """run_on_ui from another thread waits for the frame loop to do it."""
    results = []

    def request():
        results.append(ui.run_on_ui(lambda: "done", timeout=5))

    thread = threading.Thread(target=request)
    thread.start()
    while thread.is_alive():
        ui._drain_commands()
        thread.join(0.01)
    assert results == ["done"]
