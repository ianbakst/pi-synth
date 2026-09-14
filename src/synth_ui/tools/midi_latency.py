"""Measure MIDI arrival timing on this board, with no human in the loop.

Pairs with hardware/midi-tester/midi-tester.ino, a microcontroller that emits
notes on an exact schedule. The sender's clock is never compared to this one;
only *intervals* are measured — which is what matters: a path that adds a
constant delay is fine, one that adds a varying delay is what makes an
instrument feel broken, and a path that loses notes is worse still.

Two transports, so the same deterministic source can be compared across them:

    # USB: read the MIDI device directly, before ALSA's sequencer or JACK
    python3 -m synth_ui.tools.midi_latency --list      # what's plugged in
    python3 -m synth_ui.tools.midi_latency --rawmidi   # the only one, or name it

    # DIN: read the UART the 5-pin jack feeds, before ttymidi
    python3 -m synth_ui.tools.midi_latency --serial /dev/ttyAMA0

Both read as close to the kernel as userspace can get, so a delay seen here is
upstream of everything this project builds. Stop the audio stack first
(`systemctl stop synth-ui jack mod-host ttymidi a2jmidid`) — those hold the
devices open, and a quiet machine is the baseline everything else compares to.
"""

from __future__ import annotations

import argparse
import fcntl
import glob
import os
import re
import statistics
import struct
import sys
import threading
import time

# --- non-standard baud via termios2 -----------------------------------------
# 31250 isn't a standard rate, so the usual termios constants can't express it.
# TCSETS2 with BOTHER takes an arbitrary integer rate — the same mechanism
# ttymidi uses, and why this project doesn't need the midi-uart0 clock-fudge
# overlay.
_TCGETS2, _TCSETS2 = 0x802C542A, 0x402C542B
_BOTHER, _CBAUD = 0o010000, 0o010017
_CS8, _CLOCAL, _CREAD = 0o000060, 0o004000, 0o000200
_VTIME, _VMIN = 5, 6
_TERMIOS2 = "=4IB19B2I"


def open_serial(path: str, baud: int = 31250) -> int:
    """Open a serial port in raw mode at an arbitrary baud rate."""
    fd = os.open(path, os.O_RDONLY | os.O_NOCTTY)
    buf = bytearray(struct.calcsize(_TERMIOS2))
    fcntl.ioctl(fd, _TCGETS2, buf)
    f = list(struct.unpack(_TERMIOS2, bytes(buf)))
    f[0] = f[1] = f[3] = 0                                   # raw: no i/o/l processing
    f[2] = (f[2] & ~_CBAUD) | _BOTHER | _CS8 | _CLOCAL | _CREAD
    f[5 + _VMIN] = 1                                         # block until >=1 byte
    f[5 + _VTIME] = 0
    f[-2] = f[-1] = baud                                     # c_ispeed, c_ospeed
    fcntl.ioctl(fd, _TCSETS2, struct.pack(_TERMIOS2, *f))
    return fd


# --- MIDI parsing -----------------------------------------------------------

class MidiParser:
    """Bytes in, (time, status, data1, data2) out.

    Handles running status (a stream may omit the status byte when it repeats)
    and ignores realtime bytes, which can appear mid-message.
    """

    def __init__(self):
        self._status = 0
        self._data: list[int] = []

    def feed(self, byte: int, t_ns: int) -> tuple | None:
        """One byte in; a complete message out, or None if more bytes are due."""
        if byte >= 0xF8:              # realtime: valid anywhere, carries no data
            return None
        if byte >= 0x80:
            self._status, self._data = byte, []
            return None
        if not self._status:
            return None               # data before any status: unsynced, drop
        self._data.append(byte)
        expected = 1 if 0xC0 <= self._status <= 0xDF else 2
        if len(self._data) < expected:
            return None
        event = (t_ns, self._status, self._data[0],
                 self._data[1] if expected == 2 else 0)
        self._data = []               # running status: keep self._status
        return event

    def events(self, data: bytes, t_ns: int):
        for byte in data:
            event = self.feed(byte, t_ns)
            if event:
                yield event


# --- analysis ---------------------------------------------------------------

PATTERN_NAMES = {0: "metronome (1 note / 500ms)",
                 1: "burst (16 back-to-back, 2s apart)",
                 2: "sustained (1 note / 40ms)"}

# How many notes the sender emits per pattern. Printed alongside the received
# count because "104 notes" means nothing on its own, and the reader should not
# have to remember the firmware to know whether that is good.
PATTERN_EXPECTED = {0: 60, 1: 160, 2: 500}

# A burst's 16 notes arrive together and share one read(), so they share one
# timestamp: the gaps within a burst read as 0.0 ms whatever actually happened.
# Say so, rather than let someone conclude the path is infinitely fast.
PATTERN_NOTES = {
    1: "gaps within a burst share one read() and read as 0.0 — this pattern\n"
       "      measures delivery and loss, not intra-burst spacing",
}


def segment(events: list[tuple]) -> list[tuple[int, list[tuple]]]:
    """Split the stream on Program Change markers the sender emits per pattern."""
    segments: list[tuple[int, list[tuple]]] = []
    current: list[tuple] = []
    pattern = -1
    for t, status, d1, d2 in events:
        if status & 0xF0 == 0xC0:
            if current:
                segments.append((pattern, current))
            pattern, current = d1, []
        elif status & 0xF0 == 0x90 and d2 > 0:
            current.append((t, d2))          # velocity carries the sequence number
    if current:
        segments.append((pattern, current))
    return segments


def dropped(seqs: list[int]) -> int:
    """How many notes went missing, from gaps in the 1..127 wrapping counter."""
    missing = 0
    for prev, cur in zip(seqs, seqs[1:]):
        step = (cur - prev) % 127
        missing += step - 1 if step >= 1 else 0
    return missing


def batches(notes: list[tuple]) -> int:
    """How many separate deliveries the notes arrived in.

    Notes that share a timestamp came out of one read(), i.e. the kernel handed
    them over together. This is the statistic that survives a human at the
    keyboard: gap medians and maxima are dominated by how long someone paused
    between phrases, but nothing a player does can make ten notes arrive in the
    same instant. Notes far exceeding batches means the stream is being
    delivered in clumps, which is what late-and-then-a-flood feels like.
    """
    return len({t for t, _ in notes})


def describe(pattern: int, notes: list[tuple]) -> str:
    name = PATTERN_NAMES.get(pattern, "free playing (no test-pattern marker)")
    if len(notes) < 2:
        return f"  {name}: {len(notes)} notes — too few to measure"
    gaps = [(b[0] - a[0]) / 1e6 for a, b in zip(notes, notes[1:])]   # ms
    ordered = sorted(gaps)
    p95 = ordered[int(len(ordered) * 0.95)]
    expected = PATTERN_EXPECTED.get(pattern)
    count = f"{len(notes)}"
    if expected:
        count += f" of {expected}"

    # Drop detection reads the velocity as a sequence number, which is only true
    # of the test firmware. A real instrument sends real velocities, and
    # differencing those produces a large meaningless number — it looked like a
    # catastrophic finding the first time it was printed against a keyboard.
    sequenced = pattern >= 0
    lost = (str(dropped([n[1] for n in notes])) if sequenced
            else "n/a (not the test source)")

    groups = batches(notes)
    out = (
        f"  {name}\n"
        f"    notes {count}   dropped {lost}\n"
        f"    deliveries {groups}   ({len(notes) / groups:.1f} notes per read)\n"
        f"    gap ms: min {min(gaps):.1f}  median {statistics.median(gaps):.1f}  "
        f"p95 {p95:.1f}  max {max(gaps):.1f}\n"
        f"    spread (max-min) {max(gaps) - min(gaps):.1f} ms"
    )
    if pattern in PATTERN_NOTES:
        out += f"\n    note: {PATTERN_NOTES[pattern]}"
    if not sequenced:
        out += ("\n    note: played by hand, so gaps include pauses. Read"
                " 'notes per read':\n"
                "      near 1.0 is healthy, well above 1.0 means clumped delivery")
    return out


# --- system metrics ---------------------------------------------------------

def _cpu_busy() -> list[float]:
    busy = []
    with open("/proc/stat") as f:
        for line in f:
            if not re.match(r"cpu\d", line):
                continue
            v = [int(x) for x in line.split()[1:]]
            total, idle = sum(v), v[3] + (v[4] if len(v) > 4 else 0)
            busy.append(total - idle)
    return busy


def _irq_counts(pattern: str) -> dict[str, int]:
    out = {}
    with open("/proc/interrupts") as f:
        for line in f:
            if re.search(pattern, line, re.I):
                parts = line.split()
                name = parts[-1]
                out[name] = sum(int(p) for p in parts[1:] if p.isdigit())
    return out


class Metrics(threading.Thread):
    """Samples CPU and interrupt counters alongside the capture, so a bad run
    can be correlated with load instead of guessed at."""

    def __init__(self, irq_filter: str):
        super().__init__(daemon=True)
        self.irq_filter = irq_filter
        self.peak_cpu: list[float] = []
        self.irq_start = _irq_counts(irq_filter)
        self.irq_end = dict(self.irq_start)
        self._stop = threading.Event()

    def run(self):
        prev, prev_t = _cpu_busy(), time.monotonic()
        self.peak_cpu = [0.0] * len(prev)
        while not self._stop.wait(1.0):
            cur, now = _cpu_busy(), time.monotonic()
            hz = os.sysconf("SC_CLK_TCK")
            for i, (a, b) in enumerate(zip(prev, cur)):
                pct = (b - a) / hz / max(now - prev_t, 1e-6) * 100
                self.peak_cpu[i] = max(self.peak_cpu[i], pct)
            prev, prev_t = cur, now
            self.irq_end = _irq_counts(self.irq_filter)

    def stop(self):
        self._stop.set()


# --- capture ----------------------------------------------------------------

def capture(fd: int, duration: float) -> list[tuple]:
    parser = MidiParser()
    events: list[tuple] = []
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        try:
            data = os.read(fd, 256)
        except OSError:
            break
        # Timestamp immediately on return: this is the earliest moment userspace
        # can know the bytes exist.
        t = time.monotonic_ns()
        if not data:
            continue
        events.extend(parser.events(data, t))
    return events


def rawmidi_devices() -> list[tuple[str, str]]:
    """(device node, card name) for every ALSA rawmidi input on this board.

    The node number follows the card number, which moves when devices are
    plugged in a different order — so it can't be written down once. Cheaper to
    ask than to have someone cross-reference `aconnect -l` every run.
    """
    found = []
    for node in sorted(glob.glob("/dev/snd/midiC*D*")):
        match = re.search(r"midiC(\d+)D", node)
        card = match.group(1) if match else ""
        name = ""
        try:
            with open(f"/proc/asound/card{card}/id") as f:
                name = f.read().strip()
        except OSError:
            pass
        found.append((node, name))
    return found


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--rawmidi", nargs="?", const="",
                     help="e.g. /dev/snd/midiC1D0 (USB MIDI). Bare --rawmidi "
                          "picks the only one if there is exactly one.")
    src.add_argument("--serial", help="e.g. /dev/ttyAMA0 (5-pin DIN via UART)")
    src.add_argument("--list", action="store_true",
                     help="show the MIDI devices this board can see, and exit")
    ap.add_argument("--baud", type=int, default=31250)
    ap.add_argument("--duration", type=float, default=90.0,
                    help="seconds to capture; the sender's full cycle is ~80s")
    ap.add_argument("--irq-filter", default="xhci|dma|i2s",
                    help="which /proc/interrupts lines to track")
    args = ap.parse_args(argv)

    devices = rawmidi_devices()
    if args.list:
        for node, name in devices:
            print(f"  {node}   {name}")
        if not devices:
            print("  (no rawmidi devices — nothing MIDI is plugged in)")
        return 0

    if args.serial:
        fd, label = open_serial(args.serial, args.baud), args.serial
    else:
        path = args.rawmidi
        if not path:
            if len(devices) != 1:
                print("--rawmidi needs a device; this board has "
                      f"{len(devices)}. Run with --list.", file=sys.stderr)
                return 1
            path = devices[0][0]
        if not os.path.exists(path):
            print(f"{path} does not exist. Available:", file=sys.stderr)
            for node, name in devices:
                print(f"  {node}   {name}", file=sys.stderr)
            return 1
        fd, label = os.open(path, os.O_RDONLY), path

    print(f"capturing from {label} for {args.duration:.0f}s ...", file=sys.stderr)
    metrics = Metrics(args.irq_filter)
    metrics.start()
    try:
        events = capture(fd, args.duration)
    finally:
        metrics.stop()
        os.close(fd)

    if not events:
        print("no MIDI received — is the sender powered and wired?", file=sys.stderr)
        return 1

    print(f"\n{len(events)} MIDI messages\n")
    segments = segment(events)
    if not segments:
        print("no pattern markers seen; is this the test firmware?", file=sys.stderr)
        return 1
    for pattern, notes in segments:
        print(describe(pattern, notes))
        print()

    if metrics.peak_cpu:
        print("peak CPU per core: " +
              "  ".join(f"{i}:{p:.0f}%" for i, p in enumerate(metrics.peak_cpu)))
    deltas = {k: metrics.irq_end.get(k, 0) - v for k, v in metrics.irq_start.items()}
    if deltas:
        print("interrupts during run: " +
              "  ".join(f"{k}={v}" for k, v in deltas.items() if v))
    return 0


if __name__ == "__main__":
    sys.exit(main())
