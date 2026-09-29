"""Re-wiring keyboards that appear after the rig was wired. No hardware."""

from types import SimpleNamespace

from synth_ui.clients.engine_manager import EngineManager
from synth_ui.clients.watch import HostWatcher, KeyboardWatcher, _Poller


class GraphStub:
    """Just enough JackGraph for unwired_keyboards: ports and their links."""

    def __init__(self, keyboards: dict[str, list[str]]):
        self._keyboards = keyboards

    def snapshot(self):
        return {name: SimpleNamespace(connections=list(links))
                for name, links in self._keyboards.items()}

    def keyboard_midi_sources(self, snap=None):
        return sorted(self._keyboards)


def engine_with(keyboards):
    engine = EngineManager.__new__(EngineManager)   # no mod-host, no JACK
    engine._jack = GraphStub(keyboards)
    return engine


def test_a_keyboard_feeding_nothing_is_reported():
    # Replugged: same name as before, every connection gone.
    engine = engine_with({
        "a2j:Piano": [],
        "ttymidi:MIDI_in": ["effect_100:midiin"],
    })
    assert engine.unwired_keyboards() == ["a2j:Piano"]


def test_wired_keyboards_are_left_alone():
    engine = engine_with({"a2j:Piano": ["effect_100:midiin"]})
    assert engine.unwired_keyboards() == []


def test_the_watcher_rewires_when_a_keyboard_is_unwired():
    calls = []
    watcher = KeyboardWatcher(lambda: ["a2j:Piano"], lambda: calls.append(1))
    assert watcher.tick() is True
    assert calls == [1]


def test_the_watcher_does_nothing_while_everything_is_wired():
    calls = []
    watcher = KeyboardWatcher(lambda: [], lambda: calls.append(1))
    assert watcher.tick() is False
    assert calls == []


def test_a_failed_look_does_not_stop_the_watching():
    class Flaky(_Poller):
        def __init__(self):
            super().__init__(interval=0)
            self.ticks = 0

        def tick(self):
            self.ticks += 1
            if self.ticks == 1:
                raise OSError("jack is restarting")
            self.stop()
            return False

    poller = Flaky()
    poller._run()                      # returns once the second tick stops it
    assert poller.ticks == 2


# --- mod-host restarts ---------------------------------------------------------

def host(pids):
    seen = iter(pids)
    fired = []
    watcher = HostWatcher(lambda: fired.append(1), read_pid=lambda: next(seen))
    return watcher, fired


def test_the_first_pid_seen_is_the_one_the_graph_was_built_in():
    watcher, fired = host([100, 100, 100])
    assert [watcher.tick() for _ in range(3)] == [False, False, False]
    assert fired == []


def test_a_new_pid_means_mod_host_restarted_empty():
    watcher, fired = host([100, 100, 250])
    assert [watcher.tick() for _ in range(3)] == [False, False, True]
    assert fired == [1]


def test_mod_host_down_is_waited_out_not_acted_on():
    # Mid-restart there is no PID at all; act once it's back, as a new one.
    watcher, fired = host([100, 0, 0, 250])
    assert [watcher.tick() for _ in range(4)] == [False, False, False, True]


def test_it_fires_once_the_ui_is_on_its_way_out():
    watcher, fired = host([100, 250, 300])
    for _ in range(3):
        watcher.tick()
    assert fired == [1]


def test_mod_host_never_seen_running_is_not_a_restart():
    watcher, fired = host([0, 0, 100])
    assert [watcher.tick() for _ in range(3)] == [False, False, False]
