"""Tests for JackGraph — driven by a fake runner that emits canned jack_lsp
output, so no JACK server is needed."""

from synth_ui.clients.jack_graph import JackGraph

# A realistic graph: fluidsynth (audio out -> DAC, midi in <- keyboard) running,
# mod-host present but its audio NOT wired to the DAC (the gap Phase 2 fixes).
KBD = "a2j:Keystation 61 [24] (capture): Keystation 61 MIDI 1"
KBD_PB = "a2j:Keystation 61 [24] (playback): Keystation 61 MIDI 1"

PORTS = [
    # (name, type, is_output)
    ("system:capture_1", "audio", True),
    ("system:playback_1", "audio", False),
    ("system:playback_2", "audio", False),
    (KBD, "midi", True),
    (KBD_PB, "midi", False),
    ("fluidsynth-01:left", "audio", True),
    ("fluidsynth-01:right", "audio", True),
    ("fluidsynth-01:midi_00", "midi", False),
    ("mod-host:midi_in", "midi", False),
    ("mod-host:audio_out_1", "audio", True),
    ("mod-host:audio_out_2", "audio", True),
]

CONNECTIONS = [
    (KBD, "fluidsynth-01:midi_00"),
    ("fluidsynth-01:left", "system:playback_1"),
    ("fluidsynth-01:right", "system:playback_2"),
]


def build_runner(ports=PORTS, connections=CONNECTIONS, *, connect_rc=0):
    """Return a runner emulating jack_lsp/jack_connect/jack_disconnect."""
    adj: dict[str, list[str]] = {name: [] for name, _, _ in ports}
    for a, b in connections:
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)

    def lsp_t() -> str:
        lines = []
        for name, t, _ in ports:
            desc = "32 bit float mono audio" if t == "audio" else "8 bit raw midi"
            lines += [name, f"\t{desc}"]
        return "\n".join(lines) + "\n"

    def lsp_p() -> str:
        lines = []
        for name, _, is_out in ports:
            flags = "output" if is_out else "input"
            lines += [name, f"\tproperties: {flags},physical,terminal,"]
        return "\n".join(lines) + "\n"

    def lsp_c() -> str:
        lines = []
        for name, _, _ in ports:
            lines.append(name)
            lines += [f"\t{c}" for c in adj.get(name, [])]
        return "\n".join(lines) + "\n"

    calls: list[list[str]] = []

    def runner(cmd: list[str]) -> tuple[int, str]:
        calls.append(cmd)
        if cmd[:2] == ["jack_lsp", "-t"]:
            return 0, lsp_t()
        if cmd[:2] == ["jack_lsp", "-p"]:
            return 0, lsp_p()
        if cmd[:2] == ["jack_lsp", "-c"]:
            return 0, lsp_c()
        if cmd[0] in ("jack_connect", "jack_disconnect"):
            return connect_rc, ""
        return 1, ""

    runner.calls = calls  # type: ignore[attr-defined]
    return runner


def test_snapshot_parses_type_direction_connections():
    snap = JackGraph(build_runner()).snapshot()
    assert snap["fluidsynth-01:left"].type == "audio"
    assert snap["fluidsynth-01:left"].is_output is True
    assert snap["system:playback_1"].type == "audio"
    assert snap["system:playback_1"].is_output is False   # a sink
    assert snap[KBD].type == "midi"
    assert snap[KBD].is_output is True                     # a source
    assert "fluidsynth-01:midi_00" in snap[KBD].connections
    assert snap["fluidsynth-01:left"].client == "fluidsynth-01"


def test_ports_filtering_matches_client_prefix_and_type_and_direction():
    g = JackGraph(build_runner())
    # client prefix: "fluidsynth" matches the pid-suffixed "fluidsynth-01"
    assert g.ports(client="fluidsynth", type="audio", is_output=True) == [
        "fluidsynth-01:left",
        "fluidsynth-01:right",
    ]
    assert g.ports(client="mod-host", type="audio", is_output=True) == [
        "mod-host:audio_out_1",
        "mod-host:audio_out_2",
    ]
    assert g.ports(client="mod-host", type="midi", is_output=False) == [
        "mod-host:midi_in"
    ]


def test_keyboard_sources_and_dac_sinks():
    g = JackGraph(build_runner())
    assert g.keyboard_midi_sources() == [KBD]              # capture only, not playback
    assert g.dac_sinks() == ["system:playback_1", "system:playback_2"]


def test_is_connected():
    g = JackGraph(build_runner())
    assert g.is_connected("fluidsynth-01:left", "system:playback_1") is True
    # the Phase 2 gap: mod-host audio is not wired to the DAC
    assert g.is_connected("mod-host:audio_out_1", "system:playback_1") is False


def test_connect_calls_jack_connect_and_reports_success():
    runner = build_runner()
    g = JackGraph(runner)
    assert g.connect("mod-host:audio_out_1", "system:playback_1") is True
    assert ["jack_connect", "mod-host:audio_out_1", "system:playback_1"] in runner.calls


def test_connect_is_idempotent_when_already_connected():
    # jack_connect returns nonzero for an existing connection; connect() should
    # still report success because the edge is present in the graph.
    runner = build_runner(connect_rc=1)
    g = JackGraph(runner)
    assert g.connect("fluidsynth-01:left", "system:playback_1") is True


def test_connect_reports_failure_for_missing_edge():
    runner = build_runner(connect_rc=1)  # command fails AND edge doesn't exist
    g = JackGraph(runner)
    assert g.connect("mod-host:audio_out_1", "system:playback_1") is False


def test_wait_for_returns_true_when_present_false_on_timeout():
    g = JackGraph(build_runner())
    assert g.wait_for(client="fluidsynth", type="audio", timeout=0.2) is True
    assert g.wait_for(client="pianoteq", timeout=0.05, interval=0.01) is False


def test_empty_graph_off_pi_is_safe():
    # No JACK tools -> runner returns 127/"" -> everything empty, no exceptions.
    g = JackGraph(lambda cmd: (127, ""))
    assert g.snapshot() == {}
    assert g.keyboard_midi_sources() == []
    assert g.dac_sinks() == []
    assert g.connect("a", "b") is False
