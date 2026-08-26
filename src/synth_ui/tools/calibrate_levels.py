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
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time

from synth_ui.clients.engine_manager import EngineManager
from synth_ui.clients.voice import Voice, read_voices_manifest
from synth_ui.config import VOICES_MANIFEST

# A voice needs to be sounding before there's anything to measure. Long enough
# to cover attack + sustain on slow instruments, short enough to sit through
# once per voice.
_CAPTURE_SECONDS = 8.0
_DEFAULT_TARGET_LUFS = -18.0
# Below this, treat the capture as silence: the voice never sounded (dead
# plugin, missing sample) and a "trim" computed from it would be nonsense.
_SILENCE_LUFS = -70.0
# Don't let one bad measurement produce an absurd boost.
_MAX_TRIM_DB = 24.0


def _capture(port_glob: str, seconds: float, out_dir: str) -> str | None:
    """Record the master chain's output to a wav. Returns the path, or None."""
    try:
        subprocess.run(
            [
                "jack_capture",
                "--duration", str(seconds),
                "--port", port_glob,
                "--filename", os.path.join(out_dir, "capture.wav"),
            ],
            capture_output=True,
            timeout=seconds + 20,
        )
    except FileNotFoundError:
        print("jack_capture not installed (apt install jack-capture)", file=sys.stderr)
        return None
    except subprocess.TimeoutExpired:
        print("jack_capture timed out", file=sys.stderr)
        return None
    path = os.path.join(out_dir, "capture.wav")
    return path if os.path.exists(path) else None


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


def _play_passage(midi_file: str) -> subprocess.Popen | None:
    """Start the calibration passage into the JACK MIDI graph. Every voice must
    hear the identical performance, or the measurements aren't comparable."""
    try:
        return subprocess.Popen(
            ["jack-smf-player", "-q", midi_file],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        print(
            "jack-smf-player not installed (apt install jack-tools)", file=sys.stderr
        )
        return None


def _measure_voice(
    engine: EngineManager, voice: Voice, midi_file: str, port_glob: str
) -> float | None:
    """Load a voice, play the passage, and return its integrated loudness."""
    if not engine.load_voice(voice):
        print(f"  {voice.name}: failed to load", file=sys.stderr)
        return None
    time.sleep(0.5)  # let the graph settle before recording

    player = _play_passage(midi_file)
    if player is None:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        wav = _capture(port_glob, _CAPTURE_SECONDS, tmp)
        player.terminate()
        if wav is None:
            return None
        return _integrated_lufs(wav)


def _trim_for(lufs: float, target: float) -> float:
    return max(-_MAX_TRIM_DB, min(_MAX_TRIM_DB, target - lufs))


def _write_trims(manifest: str, trims: dict[str, float]) -> None:
    """Update gain_trim_db in place, preserving everything else in the file."""
    with open(manifest) as f:
        entries = json.load(f)
    for entry in entries:
        if entry["name"] in trims:
            entry["gain_trim_db"] = round(trims[entry["name"]], 1)
    with open(manifest, "w") as f:
        json.dump(entries, f, indent=2)
        f.write("\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", default=VOICES_MANIFEST)
    parser.add_argument(
        "--midi", help="MIDI file played into each voice (the same passage for all)"
    )
    parser.add_argument("--target", type=float, default=_DEFAULT_TARGET_LUFS)
    parser.add_argument(
        "--port", default="effect_9*", help="JACK port glob to record (master chain)"
    )
    parser.add_argument("--only", help="calibrate one voice by name")
    parser.add_argument("--dry-run", action="store_true", help="measure, don't write")
    args = parser.parse_args(argv)

    voices = read_voices_manifest(args.manifest)
    if args.only:
        voices = [v for v in voices if v.name == args.only]
    if not voices:
        print("no voices to calibrate", file=sys.stderr)
        return 1
    if not args.midi:
        print(
            "--midi is required: every voice must hear the same passage",
            file=sys.stderr,
        )
        return 1

    engine = EngineManager()
    engine.start()
    # Measure the instruments themselves, not the user's volume setting.
    engine.set_gain(1.0)

    trims: dict[str, float] = {}
    for voice in voices:
        lufs = _measure_voice(engine, voice, args.midi, args.port)
        if lufs is None:
            print(f"  {voice.name:<24} no measurement")
            continue
        if lufs < _SILENCE_LUFS or math.isinf(lufs):
            print(f"  {voice.name:<24} silent — check the voice loads and sounds")
            continue
        trim = _trim_for(lufs, args.target)
        trims[voice.name] = trim
        print(f"  {voice.name:<24} {lufs:>7.1f} LUFS  ->  trim {trim:+.1f} dB")

    if not trims:
        print("\nnothing measured", file=sys.stderr)
        return 1
    if args.dry_run:
        print("\ndry run — manifest unchanged")
        return 0
    _write_trims(args.manifest, trims)
    print(f"\nwrote {len(trims)} trim values to {args.manifest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
