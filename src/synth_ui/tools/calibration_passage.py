"""Generate the MIDI passage that every voice is measured against.

`calibrate_levels` needs all voices to hear the *identical* performance, or the
loudness numbers aren't comparable. Playing it by hand can't do that, and a
hand-made .mid checked into the repo is an opaque binary nobody can review. So
it's generated: the passage is code, and the reasoning behind it is readable.

The passage, and why:

  - **Constant velocity.** Velocity changes both level and timbre on nearly
    every instrument here. Varying it would measure the passage, not the voice.
  - **Spread across the range.** Instruments are not equally loud at every
    register — a sampled piano is far louder at C3 than at C7, and a B3's
    drawbars shift the balance entirely. One note would measure that note.
  - **Fixed polyphony.** Four notes sounding at all times. Chords of varying
    size would make the loudest moments depend on the chord, not the voice.
  - **No overlap between chords.** Otherwise release tails stack up and slow,
    resonant instruments read hotter than fast ones for no musical reason.
"""

from __future__ import annotations

import struct

# 120 BPM with 480 ticks per quarter note, so one second is 960 ticks. The
# arithmetic below is in seconds; this is the only place the conversion lives.
_TICKS_PER_QUARTER = 480
_TICKS_PER_SECOND = 960

# Medium touch. Loud enough to be well clear of any noise floor, far enough from
# 127 that instruments with a velocity-switched top layer aren't measured
# exclusively on it.
_VELOCITY = 80

# Root notes, low to high: C2 G2 C3 G3 C4 G4 C5 G5. Eight chords at one second
# each is _CAPTURE_SECONDS worth, so the whole passage is measured and none of
# it is cut off mid-way.
_ROOTS = [36, 43, 48, 55, 60, 67, 72, 79]
_CHORD = (0, 4, 7, 12)          # major triad plus the octave: four voices
_CHORD_SECONDS = 1.0
_HOLD_SECONDS = 0.9             # gap before the next chord, so tails don't stack


def _vlq(value: int) -> bytes:
    """MIDI variable-length quantity: 7 bits per byte, high bit = more to come."""
    out = bytearray([value & 0x7F])
    value >>= 7
    while value:
        out.insert(0, (value & 0x7F) | 0x80)
        value >>= 7
    return bytes(out)


def _events() -> list[tuple[int, bytes]]:
    """(absolute tick, message) for the whole passage, unsorted."""
    events: list[tuple[int, bytes]] = []
    for index, root in enumerate(_ROOTS):
        start = int(index * _CHORD_SECONDS * _TICKS_PER_SECOND)
        end = start + int(_HOLD_SECONDS * _TICKS_PER_SECOND)
        for interval in _CHORD:
            note = root + interval
            events.append((start, bytes([0x90, note, _VELOCITY])))
            events.append((end, bytes([0x80, note, 0])))
    return events


def passage_bytes() -> bytes:
    """The complete Standard MIDI File, format 0."""
    track = bytearray()
    # Tempo: 500000 microseconds per quarter = 120 BPM, matching _TICKS_PER_SECOND.
    track += _vlq(0) + b"\xff\x51\x03" + struct.pack(">I", 500000)[1:]

    previous = 0
    # Sort by tick, then by status so note-offs at tick T precede note-ons at T.
    # They never collide in this passage, but a future edit that shortens the
    # gap would otherwise silence the new chord with the old one's note-offs.
    for tick, message in sorted(_events(), key=lambda e: (e[0], e[1][0])):
        track += _vlq(tick - previous) + message
        previous = tick

    track += _vlq(0) + b"\xff\x2f\x00"          # end of track

    header = b"MThd" + struct.pack(">IHHH", 6, 0, 1, _TICKS_PER_QUARTER)
    return header + b"MTrk" + struct.pack(">I", len(track)) + bytes(track)


def duration_seconds() -> float:
    return len(_ROOTS) * _CHORD_SECONDS


def write_passage(path: str) -> str:
    with open(path, "wb") as f:
        f.write(passage_bytes())
    return path


if __name__ == "__main__":
    import sys

    out = sys.argv[1] if len(sys.argv) > 1 else "calibration.mid"
    write_passage(out)
    print(f"wrote {out} ({duration_seconds():.0f}s, "
          f"{len(_ROOTS)} chords, velocity {_VELOCITY})")
