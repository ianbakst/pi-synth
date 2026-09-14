"""Tests for the MIDI timing harness.

Only the pure logic is covered: parsing, segmenting, and drop detection. Those
are the parts that can be wrong without anyone noticing — a parser that loses
running-status notes would report drops that never happened, and send us chasing
a hardware fault that isn't there. The device I/O has nothing to test that a fake
wouldn't simply reimplement.
"""

from synth_ui.tools.midi_latency import MidiParser, describe, dropped, segment

MS = 1_000_000


def parse(data: bytes, t_ns: int = 0) -> list[tuple]:
    return list(MidiParser().events(data, t_ns))


class TestMidiParser:
    def test_note_on(self):
        assert parse(b"\x90\x3c\x40") == [(0, 0x90, 0x3C, 0x40)]

    def test_running_status(self):
        """A stream may omit a repeated status byte. Missing this would look
        exactly like the transport dropping notes."""
        events = parse(b"\x90\x3c\x01\x3c\x02\x3c\x03")
        assert [e[3] for e in events] == [1, 2, 3]
        assert all(e[1] == 0x90 for e in events)

    def test_program_change_is_two_bytes(self):
        assert parse(b"\xc0\x02") == [(0, 0xC0, 0x02, 0)]

    def test_program_change_does_not_swallow_the_next_note(self):
        events = parse(b"\xc0\x01\x90\x3c\x05")
        assert [e[1] for e in events] == [0xC0, 0x90]

    def test_realtime_bytes_are_ignored_mid_message(self):
        """Clock and active-sense can arrive between the bytes of a message."""
        assert parse(b"\x90\xf8\x3c\xfe\x40") == [(0, 0x90, 0x3C, 0x40)]

    def test_leading_data_without_status_is_dropped(self):
        """Capture can start mid-message; those bytes are unsynced, not notes."""
        assert parse(b"\x3c\x40\x90\x3c\x07") == [(0, 0x90, 0x3C, 0x07)]

    def test_split_across_reads(self):
        parser = MidiParser()
        assert list(parser.events(b"\x90\x3c", 0)) == []
        assert list(parser.events(b"\x40", 5)) == [(5, 0x90, 0x3C, 0x40)]


class TestDropped:
    def test_none_missing(self):
        assert dropped([1, 2, 3, 4]) == 0

    def test_one_missing(self):
        assert dropped([1, 2, 4, 5]) == 1

    def test_several_missing(self):
        assert dropped([1, 10]) == 8

    def test_wraps_at_127(self):
        """The sender counts 1..127 and wraps; a wrap is not a drop."""
        assert dropped([126, 127, 1, 2]) == 0

    def test_missing_across_the_wrap(self):
        assert dropped([126, 2]) == 2


class TestSegment:
    def test_splits_on_program_change(self):
        events = [
            (0, 0xC0, 0, 0),
            (1, 0x90, 60, 1),
            (2, 0x90, 60, 2),
            (3, 0xC0, 1, 0),
            (4, 0x90, 60, 3),
        ]
        segments = segment(events)
        assert [p for p, _ in segments] == [0, 1]
        assert [len(n) for _, n in segments] == [2, 1]

    def test_note_offs_are_not_counted(self):
        """Note-off and zero-velocity note-on both end a note. Counting them
        would halve every measured interval."""
        events = [
            (0, 0xC0, 0, 0),
            (1, 0x90, 60, 1),
            (2, 0x80, 60, 0),
            (3, 0x90, 60, 0),
        ]
        assert len(segment(events)[0][1]) == 1

    def test_notes_before_any_marker_are_kept(self):
        """Capture often starts mid-pattern; those notes are still real."""
        segments = segment([(0, 0x90, 60, 1), (1, 0xC0, 0, 0), (2, 0x90, 60, 2)])
        assert [p for p, _ in segments] == [-1, 0]


class TestDescribe:
    def test_reports_gaps_in_milliseconds(self):
        notes = [(0, 1), (500 * MS, 2), (1000 * MS, 3)]
        out = describe(0, notes)
        assert "median 500.0" in out
        assert "dropped 0" in out

    def test_reports_the_spread(self):
        """The number that matters: a constant delay is playable, a varying one
        is not."""
        notes = [(0, 1), (100 * MS, 2), (400 * MS, 3)]
        assert "spread (max-min) 200.0 ms" in describe(0, notes)

    def test_too_few_notes_says_so(self):
        assert "too few" in describe(0, [(0, 1)])


class TestDescribeCounts:
    def test_shows_the_expected_note_count(self):
        """"104 notes" is unreadable without knowing the firmware; "104 of 160"
        is not."""
        notes = [(i * 40_000_000, i + 1) for i in range(10)]
        assert "10 of 160" in describe(1, notes)

    def test_burst_warns_that_intra_burst_gaps_are_not_measurable(self):
        assert "not intra-burst spacing" in describe(1, [(0, 1), (0, 2)])

    def test_no_warning_on_the_steady_patterns(self):
        assert "note:" not in describe(0, [(0, 1), (500 * MS, 2)])


class TestFreePlaying:
    """A human at a keyboard sends real velocities, not sequence numbers."""

    def test_drop_count_is_suppressed_without_a_marker(self):
        notes = [(0, 64), (100 * MS, 99), (200 * MS, 12)]
        assert "n/a" in describe(-1, notes)

    def test_drop_count_is_reported_for_the_test_source(self):
        assert "dropped 0" in describe(0, [(0, 1), (500 * MS, 2)])

    def test_clumping_is_visible(self):
        """Ten notes sharing one read() is the signature of batched delivery —
        and the one thing a player cannot fake."""
        notes = [(0, 60 + i) for i in range(10)]
        assert "deliveries 1" in describe(-1, notes)
        assert "10.0 notes per read" in describe(-1, notes)

    def test_healthy_delivery_is_one_note_per_read(self):
        notes = [(i * 100 * MS, 60) for i in range(10)]
        assert "1.0 notes per read" in describe(-1, notes)
