"""Tests for the generated calibration passage.

The passage is the measuring stick every voice is compared against, so a defect
here doesn't announce itself — it silently skews every trim value in the
manifest. These assert the properties the loudness comparison depends on.
"""

import struct

from synth_ui.tools.calibration_passage import (
    _vlq,
    duration_seconds,
    passage_bytes,
    write_passage,
)


def parse(data: bytes) -> list[tuple[int, int, int, int]]:
    """(tick, status, note, velocity) for each note event. A deliberately naive
    reader: if the writer's framing is wrong, this breaks rather than papering
    over it."""
    assert data[:4] == b"MThd"
    length, fmt, ntrks, division = struct.unpack(">IHHH", data[4:14])
    assert (length, fmt, ntrks) == (6, 0, 1)
    assert data[14:18] == b"MTrk"
    (track_len,) = struct.unpack(">I", data[18:22])
    body = data[22:22 + track_len]

    events, i, tick = [], 0, 0
    while i < len(body):
        delta = 0
        while body[i] & 0x80:
            delta = (delta << 7) | (body[i] & 0x7F)
            i += 1
        delta = (delta << 7) | body[i]
        i += 1
        tick += delta
        status = body[i]
        if status == 0xFF:                       # meta: skip by its length byte
            i += 2
            meta_len = body[i]
            i += 1 + meta_len
            continue
        events.append((tick, status, body[i + 1], body[i + 2]))
        i += 3
    return events


class TestVLQ:
    def test_single_byte(self):
        assert _vlq(0) == b"\x00"
        assert _vlq(127) == b"\x7f"

    def test_multi_byte(self):
        """128 is the first value needing continuation; getting this wrong
        shifts every subsequent event in the file."""
        assert _vlq(128) == b"\x81\x00"
        assert _vlq(960) == b"\x87\x40"


class TestPassage:
    def test_parses_as_a_format_0_file(self):
        assert len(parse(passage_bytes())) == 64      # 32 on + 32 off

    def test_velocity_is_constant(self):
        """Velocity changes timbre as well as level on most of these
        instruments, so a varying one would measure the passage, not the voice."""
        ons = [e for e in parse(passage_bytes()) if e[1] == 0x90]
        assert {e[3] for e in ons} == {80}

    def test_spans_the_keyboard(self):
        notes = [e[2] for e in parse(passage_bytes()) if e[1] == 0x90]
        assert min(notes) == 36 and max(notes) == 91

    def test_polyphony_is_always_four(self):
        """Uneven polyphony would make the loudest moment depend on the chord
        rather than on the instrument."""
        live, peak = 0, 0
        for _, status, _, _ in parse(passage_bytes()):
            live += 1 if status == 0x90 else -1
            peak = max(peak, live)
            assert live >= 0
        assert peak == 4
        assert live == 0                          # every note is released

    def test_chords_do_not_overlap(self):
        """Overlapping tails would favour slow, resonant instruments."""
        events = parse(passage_bytes())
        for tick, status, _, _ in events:
            if status != 0x90:
                continue
            still_held = [
                e for e in events
                if e[1] == 0x90 and e[0] < tick
                and not any(o[1] == 0x80 and o[2] == e[2] and o[0] <= tick
                            for o in events)
            ]
            assert not still_held

    def test_duration_matches_the_declared_length(self):
        assert duration_seconds() == 8.0

    def test_write_passage_round_trips(self, tmp_path):
        path = write_passage(str(tmp_path / "c.mid"))
        assert open(path, "rb").read() == passage_bytes()


class TestTrimClamp:
    """The clamp exists to stop one bad measurement writing an absurd boost.
    When it fires on every voice at once it is the rig that's wrong, so it has
    to be visible rather than silently recorded as a number."""

    def test_detects_a_clamped_trim(self):
        from synth_ui.tools.calibrate_levels import _MAX_TRIM_DB, _clamped, _trim_for

        assert _clamped(_trim_for(-42.0, -18.0))      # 28 dB low -> pins at +24
        assert _trim_for(-42.0, -18.0) == _MAX_TRIM_DB

    def test_a_normal_trim_is_not_clamped(self):
        from synth_ui.tools.calibrate_levels import _clamped, _trim_for

        assert not _clamped(_trim_for(-14.0, -18.0))  # the same voice at unity
        assert _trim_for(-14.0, -18.0) == -4.0
