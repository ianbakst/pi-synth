"""Tests for the MIDI footswitch listener.

Line parsing and the press/release rule are the whole of the logic; the
subprocess has nothing to test that a fake wouldn't reimplement.
"""

from synth_ui.clients.midi_control import MidiControlListener


def listener(**kwargs):
    events = []
    listener = MidiControlListener(
        on_next=lambda: events.append("next"),
        on_previous=lambda: events.append("prev"),
        next_cc=kwargs.get("next_cc", 80),
        prev_cc=kwargs.get("prev_cc", 81),
        on_program=kwargs.get("on_program"),
    )
    return listener, events


CC = " 24:0   Control change          0, controller {n}, value {v}"
PC = " 24:0   Program change          0, program {p}"


class TestControlChange:
    def test_next_cc_steps_forward(self):
        lis, events = listener()
        lis.handle_line(CC.format(n=80, v=127))
        assert events == ["next"]

    def test_prev_cc_steps_back(self):
        lis, events = listener()
        lis.handle_line(CC.format(n=81, v=127))
        assert events == ["prev"]

    def test_release_is_ignored(self):
        """A momentary switch sends 127 on press and 0 on release. Acting on
        both would step two rigs per tap."""
        lis, events = listener()
        lis.handle_line(CC.format(n=80, v=127))
        lis.handle_line(CC.format(n=80, v=0))
        assert events == ["next"]

    def test_any_nonzero_value_counts_as_a_press(self):
        """Not every switch sends exactly 127."""
        lis, events = listener()
        lis.handle_line(CC.format(n=80, v=64))
        assert events == ["next"]

    def test_other_controllers_are_ignored(self):
        """Sustain is CC 64 and arrives constantly while playing."""
        lis, events = listener()
        lis.handle_line(CC.format(n=64, v=127))
        assert events == []

    def test_configurable_cc_numbers(self):
        lis, events = listener(next_cc=20, prev_cc=21)
        lis.handle_line(CC.format(n=20, v=127))
        assert events == ["next"]


class TestProgramChange:
    def test_program_change_is_ignored_when_no_handler(self):
        """Off by default: the Roland sends Program Change when its own tones
        are changed from the panel."""
        lis, events = listener()
        assert not lis.handle_line(PC.format(p=3))
        assert events == []

    def test_program_change_reaches_the_handler_when_enabled(self):
        seen = []
        lis, _ = listener(on_program=seen.append)
        lis.handle_line(PC.format(p=3))
        assert seen == [3]


class TestNoise:
    def test_note_lines_are_ignored(self):
        lis, events = listener()
        lis.handle_line(" 24:0   Note on                 0, note 60, velocity 80")
        assert events == []

    def test_header_lines_are_ignored(self):
        lis, events = listener()
        for line in ("Waiting for data.", "Source  Event", ""):
            assert not lis.handle_line(line)
        assert events == []
