"""Measure how loud each voice actually is, and write per-voice trim values.

The problem: instruments differ wildly in output level, so switching from a
sampled piano to a B3 jumps in volume. The fix is a fixed per-voice offset
(`Voice.gain_trim_db`), applied by the master chain on load — but the numbers
have to come from somewhere, and "by ear" doesn't stay consistent across a dozen
instruments.

So: play the same passage into every voice, record it, measure it, and write the
offsets back into the manifest.

    python3 -m synth_ui.tools.calibrate_levels --dry-run   # measure, change nothing
    python3 -m synth_ui.tools.calibrate_levels             # measure and write

**Loudness, not peak.** Two instruments can peak identically and still sound
twice as loud as each other — peak says nothing about perceived level. This
measures EBU R128 integrated loudness (LUFS) via ffmpeg, the same standard
broadcast uses, and trims each voice to `--target` (default -18 LUFS, leaving
headroom for the limiter).

**This is a starting point, not the last word.** Measured loudness and "sits
right when I play it" aren't identical, and a voice matched at medium touch can
still diverge when you dig in — that's velocity response, a separate axis from
gain. Expect to nudge by ear afterwards; that nudge is what gets saved per rig.

Requires: a running audio stack (jack + mod-host), `ffmpeg`, and a JACK recorder
(`jack_capture`). Run it with nothing else making sound.
"""

from __future__ import annotations

import argparse
import math
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time

from synth_ui.clients.engine_manager import EngineManager
from synth_ui.clients.jack_graph import JackGraph
from synth_ui.clients.trims import read_trims, write_trims
from synth_ui.clients.voice import Voice
from synth_ui.config import (
    MAX_GAIN,
    MOD_HOST_PORT,
    SOUNDFONT_DIR,
    TRIMS_FILE,
    VOICES_MANIFEST,
)
from synth_ui.tools.calibration_passage import duration_seconds, write_passage
from synth_ui.ui.utils import load_voices

# A voice needs to be sounding before there's anything to measure. Long enough
# to cover attack + sustain on slow instruments, short enough to sit through
# once per voice.
_CAPTURE_SECONDS = duration_seconds()
_DEFAULT_TARGET_LUFS = -18.0
# Below this, treat the capture as silence: the voice never sounded (dead
# plugin, missing sample) and a "trim" computed from it would be nonsense.
_SILENCE_LUFS = -70.0
# Don't let one bad measurement produce an absurd boost.
_MAX_TRIM_DB = 24.0


def _capture_cmd(ports: list[str], seconds: float, path: str) -> list[str]:
    """The jack_capture invocation, kept in one place so --check can show the
    exact command it ran rather than a paraphrase of it."""
    cmd = ["jack_capture", "-d", str(seconds)]
    for port in ports:
        cmd += ["-p", port]
    cmd.append(path)          # filename is positional in every documented example
    return cmd


def _head(result: subprocess.CompletedProcess) -> str:
    """The first few meaningful lines of a failed run — where the reason is."""
    text = (result.stderr or b"").decode(errors="replace")
    text += (result.stdout or b"").decode(errors="replace")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return " / ".join(lines[:3])[:200] or "no output"


def _capture(ports: list[str], seconds: float, out_dir: str) -> tuple[str | None, str]:
    """Record the master chain's output to a wav. Returns the path, or None.

    Ports are named explicitly rather than globbed. A glob like "effect_9*"
    matches effect_9 — the scratch *instrument* slot — as readily as effect_90,
    the master limiter, and recording the wrong one silently measures the
    untrimmed instrument instead of the finished signal.
    """
    path = os.path.join(out_dir, "capture.wav")
    cmd = _capture_cmd(ports, seconds, path)
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=seconds + 20)
    except FileNotFoundError:
        return None, "jack_capture not installed (apt install jack-capture)"
    except subprocess.TimeoutExpired:
        return None, "jack_capture timed out"
    if result.returncode != 0:
        # The FIRST lines, not the last. jack_capture's complaint is at the top
        # and its footer is at the bottom; truncating to the tail showed only
        # "jack_capture --advanced-options (or --help2)" twice in a row, which
        # said nothing about what was actually rejected.
        detail = _head(result)
        return None, f"jack_capture exit {result.returncode}: {detail}"
    if not os.path.exists(path):
        return None, f"jack_capture wrote no file to {path}"
    return path, ""


def _integrated_lufs(wav_path: str) -> float | None:
    """EBU R128 integrated loudness of a wav, via ffmpeg's ebur128 filter."""
    try:
        p = subprocess.run(
            ["ffmpeg", "-nostats", "-i", wav_path, "-af", "ebur128", "-f", "null", "-"],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except FileNotFoundError:
        print("ffmpeg not installed", file=sys.stderr)
        return None
    except subprocess.TimeoutExpired:
        return None
    # ffmpeg prints a summary block ending with "I: -23.4 LUFS".
    matches = re.findall(r"I:\s*(-?\d+\.?\d*)\s*LUFS", p.stderr)
    return float(matches[-1]) if matches else None


# --- getting the passage into the instrument ---------------------------------
#
# Two routes, because the obvious one isn't installable. `jack-smf-player` plays
# an SMF straight onto a JACK MIDI port and is exactly the right tool, but it
# lives in jack-smf-utils, which is no longer packaged for Debian — hence the
# `aplaymidi` route, which needs only alsa-utils.
#
# aplaymidi speaks to the ALSA sequencer, not JACK, so the passage takes a
# detour: aplaymidi -> "Midi Through" (snd-seq-dummy, which echoes input to
# output) -> a2jmidid, which already exports every ALSA-seq port into JACK ->
# the instrument. Timing through that chain is not tight, which does not matter
# here: this measures loudness over eight seconds, not latency.
#
# Either way the connection must be made explicitly. EngineManager wires only
# *physical* MIDI sources to the instrument — correct for keyboards, but it
# means a software sender is never connected for us and every capture would
# come back silent.

def _midi_through_alsa_port() -> str | None:
    """The ALSA sequencer address of "Midi Through", e.g. "14:0"."""
    try:
        out = subprocess.run(["aconnect", "-l"], capture_output=True,
                             text=True, timeout=10).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    client = None
    for line in out.splitlines():
        header = re.match(r"client (\d+): '([^']*)'", line)
        if header:
            client = header.group(1) if "through" in header.group(2).lower() else None
            continue
        port = re.match(r"\s+(\d+) ", line)
        if client and port:
            return client + ":" + port.group(1)
    return None


def _midi_through_jack_port(jack: JackGraph) -> str | None:
    """The JACK port a2jmidid publishes for "Midi Through"."""
    ports = jack.ports(type="midi", is_output=True, contains="Midi Through")
    return ports[0] if ports else None


class Player:
    """Plays the passage into `midi_port`, however this box can manage it."""

    def __init__(self, jack: JackGraph):
        self._jack = jack
        self._proc: subprocess.Popen | None = None
        self._link: tuple[str, str] | None = None

    def start(self, midi_file: str, midi_port: str) -> str | None:
        """Returns None on success, or a reason it could not play."""
        if shutil.which("jack-smf-player"):
            self._proc = subprocess.Popen(
                ["jack-smf-player", "-q", "-a", midi_port, midi_file],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return None

        if not shutil.which("aplaymidi"):
            return "no MIDI player (apt install alsa-utils)"
        alsa = _midi_through_alsa_port()
        if not alsa:
            return "no ALSA 'Midi Through' port (modprobe snd-seq-dummy)"
        bridge = _midi_through_jack_port(self._jack)
        if not bridge:
            return "a2jmidid isn't exporting 'Midi Through' — is it running?"
        if not self._jack.connect(bridge, midi_port):
            return f"could not connect {bridge} -> {midi_port}"
        self._link = (bridge, midi_port)
        self._proc = subprocess.Popen(
            ["aplaymidi", "-p", alsa, midi_file],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return None

    def stop(self) -> None:
        if self._proc is not None:
            self._proc.terminate()
            self._proc = None
        if self._link is not None:
            self._jack.disconnect(*self._link)
            self._link = None


def _measure_voice(
    engine: EngineManager, voice: Voice, midi_file: str, ports: list[str] | None
) -> tuple[float | None, str]:
    """Load a voice, play the passage, and return (loudness, reason it failed).

    The reason travels back with the result so the per-voice line can say what
    went wrong. A bare "no measurement" against every voice — which is what this
    printed the first time it ran — says nothing about which of six things
    broke."""
    if not engine.load_voice(voice):
        return None, "failed to load"
    time.sleep(0.5)  # let the graph settle before recording

    midi_port = engine.active_midi_port()
    if not midi_port:
        return None, "no MIDI input to play into"
    record = ports or engine.master_output_ports()
    if not record:
        return None, "no master-chain output to record"

    player = Player(engine.jack)
    reason = player.start(midi_file, midi_port)
    if reason:
        return None, reason
    try:
        with tempfile.TemporaryDirectory() as tmp:
            wav, error = _capture(record, _CAPTURE_SECONDS, tmp)
            if wav is None:
                return None, error
            lufs = _integrated_lufs(wav)
            if lufs is None:
                return None, "ffmpeg gave no loudness reading"
            return lufs, ""
    finally:
        player.stop()


def _trim_for(lufs: float, target: float) -> float:
    return max(-_MAX_TRIM_DB, min(_MAX_TRIM_DB, target - lufs))


def _clamped(trim: float) -> bool:
    """A trim at the limit is a symptom, not a measurement. Every voice pinning
    to +24 dB is what a mis-set measurement gain looks like, and it should read
    as suspicious rather than as twelve quiet instruments."""
    return abs(abs(trim) - _MAX_TRIM_DB) < 0.05


def _save(path: str, measured: dict[str, float]) -> None:
    """Merge new measurements into the trim file, keeping voices not measured
    this run — calibrating one voice with --only must not discard the rest."""
    trims = read_trims(path)
    trims.update(measured)
    write_trims(path, trims)


def check(engine: EngineManager, midi_file: str) -> int:
    """Test every stage separately and say which one is broken.

    Calibration is a chain of four external programs, and a failure anywhere in
    it used to surface as the same message against all twelve voices. This runs
    each link once, on its own, and reports it — one round trip instead of a
    guess per stage.
    """
    ok = True

    print("programs:")
    for tool, package, needed in [
        ("jack_capture", "jack-capture", True),
        ("ffmpeg", "ffmpeg", True),
        ("aplaymidi", "alsa-utils", True),
        ("aconnect", "alsa-utils", True),
        ("jack-smf-player", "not packaged for Debian; optional", False),
    ]:
        path = shutil.which(tool)
        mark = "ok  " if path else ("MISSING" if needed else "absent ")
        print(f"  {mark} {tool:<16} {path or '(apt install ' + package + ')'}")
        ok = ok and (bool(path) or not needed)

    # Probe the two servers directly. The first run of --check reported only
    # "master output MISSING (is mod-host up?)", leaving the reader to infer
    # that mod-host was down; say it outright instead.
    print("\nservers:")
    jack_up = bool(engine.jack.snapshot())
    print(f"  {'ok  ' if jack_up else 'MISSING'} jackd           "
          f"{'graph has ports' if jack_up else '(systemctl status jack)'}")
    ok = ok and jack_up

    probe = socket.socket()
    probe.settimeout(2.0)
    try:
        probe.connect(("127.0.0.1", MOD_HOST_PORT))
        mod_up = True
    except OSError as exc:
        mod_up, mod_err = False, str(exc)
    finally:
        probe.close()
    print(f"  {'ok  ' if mod_up else 'MISSING'} mod-host :{MOD_HOST_PORT}   "
          f"{'accepting connections' if mod_up else mod_err}")
    if not mod_up:
        print("          -> systemctl status mod-host; journalctl -u mod-host -n 30")
        print("          -> mod-host is PartOf=jack.service, so restarting jack "
              "stops it")
    ok = ok and mod_up

    print("\nJACK graph:")
    outputs = engine.master_output_ports()
    print(f"  {'ok  ' if outputs else 'MISSING'} master output  "
          f"{outputs or '(is mod-host up?)'}")
    ok = ok and bool(outputs)

    alsa = _midi_through_alsa_port()
    print(f"  {'ok  ' if alsa else 'MISSING'} Midi Through    "
          f"{alsa or '(modprobe snd-seq-dummy)'}")
    bridge = _midi_through_jack_port(engine.jack)
    print(f"  {'ok  ' if bridge else 'MISSING'} a2j bridge      "
          f"{bridge or '(is a2jmidid running?)'}")
    ok = ok and bool(alsa) and bool(bridge)

    if outputs:
        print("\nrecording 1s from the master chain:")
        with tempfile.TemporaryDirectory() as tmp:
            cmd = _capture_cmd(outputs, 1.0, os.path.join(tmp, "capture.wav"))
            print(f"  $ {' '.join(cmd)}")
            wav, error = _capture(outputs, 1.0, tmp)
            if wav is None:
                print(f"  FAILED  {error}")
                probe = subprocess.run(cmd, capture_output=True, timeout=30)
                whole = ((probe.stderr or b"") + (probe.stdout or b"")).decode(
                    errors="replace"
                )
                print("  --- jack_capture output ---")
                for line in whole.splitlines():
                    print(f"  | {line}")
                ok = False
            else:
                size = os.path.getsize(wav)
                lufs = _integrated_lufs(wav)
                print(f"  ok      {size} bytes, ffmpeg reads "
                      f"{'%.1f LUFS' % lufs if lufs is not None else 'NOTHING'}")
                print("          (silence is expected here — nothing is playing)")
                ok = ok and lufs is not None

    print("\n" + ("all stages ready" if ok else "fix the stages marked above"))
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", default=VOICES_MANIFEST)
    parser.add_argument("--soundfonts", default=SOUNDFONT_DIR)
    parser.add_argument("--trims", default=TRIMS_FILE,
                        help="where measured trims are stored")
    parser.add_argument("--category", help="calibrate one category, e.g. Piano")
    parser.add_argument(
        "--midi",
        help="MIDI file played into each voice. Defaults to the generated "
             "calibration passage (tools/calibration_passage.py).",
    )
    parser.add_argument("--target", type=float, default=_DEFAULT_TARGET_LUFS)
    parser.add_argument(
        "--port", action="append", dest="ports",
        help="JACK port to record; repeatable. Defaults to the master chain's "
             "outputs, discovered at run time.",
    )
    parser.add_argument("--only", help="calibrate one voice by name")
    parser.add_argument("--dry-run", action="store_true", help="measure, don't write")
    parser.add_argument(
        "--check", action="store_true",
        help="test each stage of the chain and report which one is broken",
    )
    args = parser.parse_args(argv)

    # The whole library, not just the manifest: most voices here are the split
    # GM set, discovered from the soundfont directory and absent from
    # voices.json entirely. Calibrating only the manifest measured 12 of ~140.
    voices = load_voices(args.manifest, args.soundfonts)
    voices = [v for v in voices if not v.unavailable_reason]
    if args.category:
        voices = [v for v in voices if v.category.lower() == args.category.lower()]
    if args.only:
        voices = [v for v in voices if v.name == args.only]
    if not voices:
        print("no voices to calibrate", file=sys.stderr)
        return 1
    if not args.check:
        minutes = len(voices) * (_CAPTURE_SECONDS + 2.0) / 60.0
        print(f"calibrating {len(voices)} voices (~{minutes:.0f} min)\n")
    engine = EngineManager()
    engine.start()
    # Measure the instruments themselves, not the user's volume setting — so
    # the master volume goes to UNITY, which is MAX_GAIN, not 1.0.
    #
    # set_gain() takes slider units, and the slider's taper (_gain_to_db) maps
    # MAX_GAIN to 0 dB. 1.0 is 40*log10(1/5) = -28 dB. Calibrating through that
    # measured every voice ~28 dB low, so every trim pinned to the +24 dB clamp
    # and the instrument looked broken rather than the rig.
    engine.set_gain(MAX_GAIN)
    passage_dir = tempfile.TemporaryDirectory()
    midi_file = args.midi or write_passage(
        os.path.join(passage_dir.name, "calibration.mid")
    )

    if args.check:
        return check(engine, midi_file)

    trims: dict[str, float] = {}
    for voice in voices:
        lufs, reason = _measure_voice(engine, voice, midi_file, args.ports)
        if lufs is None:
            print(f"  {voice.name:<24} {reason}")
            continue
        if lufs < _SILENCE_LUFS or math.isinf(lufs):
            print(f"  {voice.name:<24} silent — check the voice loads and sounds")
            continue
        trim = _trim_for(lufs, args.target)
        trims[voice.name] = trim
        flag = "  CLAMPED" if _clamped(trim) else ""
        print(f"  {voice.name:<24} {lufs:>7.1f} LUFS  ->  trim {trim:+.1f} dB{flag}")

    if not trims:
        print("\nnothing measured", file=sys.stderr)
        return 1
    clamped = [n for n, t in trims.items() if _clamped(t)]
    if len(clamped) > 1:
        print(f"\n{len(clamped)} of {len(trims)} voices hit the "
              f"{_MAX_TRIM_DB:.0f} dB clamp. That is usually the measurement "
              "path, not the\ninstruments — check the master volume is at unity "
              "and the capture ports are right.")
    if args.dry_run:
        print("\ndry run — manifest unchanged")
        return 0
    _save(args.trims, trims)
    print(f"\nwrote {len(trims)} trim values to {args.trims}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
