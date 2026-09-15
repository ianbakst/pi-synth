#!/usr/bin/env python3
"""midisniff.py — dump raw MIDI bytes arriving on the DIN jack (UART0 / GPIO15).

A bring-up and fault-isolation tool, not part of the running system. It answers
one question: *are valid MIDI bytes reaching the board at all?* — which separates
a wiring/opto-isolator fault from a fault above it in ttymidi, JACK, or the UI.

Why this exists rather than `stty` + `hexdump`: MIDI's 31250 baud is not a
standard termios speed, and GNU stty rejects it outright ("invalid argument").
The only interface that expresses it is a raw TCSETS2 ioctl with BOTHER, which
is what ttymidi itself uses — so this reproduces ttymidi's exact serial setup
while involving neither ttymidi nor a running JACK server.

It also holds the port open across configure-and-read: termios settings revert
to the driver default when the last fd closes, so setting the rate in one
process and reading in another silently gets you 9600 and garbage.

Usage, on the Pi (needs no venv — stdlib only; 'synth' is in the dialout group):

    python3 ~/synth/scripts/midisniff.py

Play the DIN keyboard during the listen window. Expect note-on triples that
track what you play (90 3c 64 ...), and possibly a steady drip of fe (active
sensing) even at rest. Bytes that don't track your playing mean the achieved
baud is wrong -- add dtoverlay=midi-uart0-pi5 and switch ttymidi.service to
-b 38400. No bytes at all means wiring or the opto-isolator (check DIN pins
4/5 first), not baud. See docs/engine-architecture.md, "MIDI ingress".
"""

import fcntl
import os
import select
import struct
import sys
import time

# asm-generic ioctls: struct termios2, which (unlike termios) carries explicit
# c_ispeed/c_ospeed fields and so can express an arbitrary baud rate.
TCGETS2, TCSETS2 = 0x802C542A, 0x402C542B
BOTHER, CBAUD = 0o010000, 0o010017          # "speed is in c_ispeed/c_ospeed"
CS8, CREAD, CLOCAL = 0o000060, 0o000200, 0o004000
VTIME, VMIN = 5, 6                          # indices into c_cc
# struct termios2 layout: 4x tcflag_t, c_line, c_cc[19], c_ispeed, c_ospeed
CC_OFFSET, SPEED_OFFSET, TERMIOS2_SIZE = 17, 36, 44

DEVICE = os.environ.get("MIDI_TTY", "/dev/ttyAMA0")
RATE = int(os.environ.get("MIDI_BAUD", "31250"))
SECONDS = int(os.environ.get("MIDI_SECONDS", "30"))


def open_raw(device: str, rate: int) -> int:
    """Open `device` in raw mode at an arbitrary baud rate. Returns the fd."""
    fd = os.open(device, os.O_RDONLY | os.O_NOCTTY)
    buf = bytearray(TERMIOS2_SIZE)
    fcntl.ioctl(fd, TCGETS2, buf, True)
    # Raw: no input/output processing, no line discipline, no echo. Reads
    # return as soon as one byte is available (VMIN=1, VTIME=0) rather than
    # blocking for a newline.
    struct.pack_into("IIII", buf, 0, 0, 0, CS8 | CREAD | CLOCAL | BOTHER, 0)
    buf[CC_OFFSET + VTIME] = 0
    buf[CC_OFFSET + VMIN] = 1
    struct.pack_into("II", buf, SPEED_OFFSET, rate, rate)
    fcntl.ioctl(fd, TCSETS2, buf, True)

    fcntl.ioctl(fd, TCGETS2, buf, True)
    got = struct.unpack_from("II", buf, SPEED_OFFSET)
    if got != (rate, rate):
        print(f"warning: asked for {rate} baud, port reports {got}", file=sys.stderr)
    return fd


def main() -> int:
    try:
        fd = open_raw(DEVICE, RATE)
    except OSError as e:
        print(f"cannot open {DEVICE} at {RATE} baud: {e}", file=sys.stderr)
        return 1

    print(f"listening on {DEVICE} at {RATE} baud for {SECONDS}s "
          f"-- play the DIN keyboard", flush=True)
    deadline, total = time.time() + SECONDS, 0
    while time.time() < deadline:
        if select.select([fd], [], [], 0.5)[0]:
            data = os.read(fd, 64)
            total += len(data)
            print(" ".join(f"{b:02x}" for b in data), flush=True)

    print(f"\n{total} bytes received")
    if total == 0:
        print("nothing arrived -- suspect wiring/opto-isolator, not baud")
    return 0


if __name__ == "__main__":
    sys.exit(main())
