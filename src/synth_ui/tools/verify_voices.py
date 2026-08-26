"""Check the voice manifest against what's actually installed on this board.

Run it on the Pi after flashing, or after adding voices:

    python3 -m synth_ui.tools.verify_voices              # validate the manifest
    python3 -m synth_ui.tools.verify_voices --list       # every installed LV2 URI
    python3 -m synth_ui.tools.verify_voices --inspect <uri>

`--inspect` is the codify half of the loop: it prints a plugin's control-port
symbols and patch properties, which is exactly what a voice needs for `params`
and `file_property`. That's how an unverified URI in voices.json (or an empty
`file_property`, like Dexed's today) gets filled in without guessing.

Exit status is non-zero if any voice is unusable, so an image build or deploy
can gate on it.
"""

from __future__ import annotations

import argparse
import subprocess
import sys

from synth_ui.clients.lv2 import LV2World, spec_for
from synth_ui.clients.voice import annotate, read_voices_manifest
from synth_ui.config import VOICES_MANIFEST


def _check(manifest: str) -> int:
    voices = read_voices_manifest(manifest)
    if not voices:
        print(f"no voices read from {manifest}", file=sys.stderr)
        return 1

    world = LV2World()
    if not world.readable:
        print("warning: lv2ls unavailable — plugin URIs not checked\n")
    annotate(voices, has_uri=world.has)

    width = max(len(v.name) for v in voices)
    broken = 0
    for voice in voices:
        if voice.available:
            print(f"  ok    {voice.name:<{width}}  {voice.engine}")
        else:
            broken += 1
            spec = spec_for(voice.engine, voice.uri, voice.file_property)
            detail = voice.path or (spec.uri if spec else "")
            print(
                f"  FAIL  {voice.name:<{width}}  "
                f"{voice.unavailable_reason}: {detail}"
            )

    print(f"\n{len(voices) - broken}/{len(voices)} voices usable")
    return 1 if broken else 0


def _list_uris() -> int:
    world = LV2World()
    if not world.readable:
        print("lv2ls unavailable", file=sys.stderr)
        return 1
    for uri in sorted(world.uris()):
        print(uri)
    return 0


def _inspect(uri: str) -> int:
    """Dump a plugin's ports and patch properties via lv2info."""
    try:
        p = subprocess.run(
            ["lv2info", uri], capture_output=True, text=True, timeout=30
        )
    except FileNotFoundError:
        print("lv2info not installed (apt install lilv-utils)", file=sys.stderr)
        return 1
    if p.returncode != 0:
        print(p.stderr.strip() or f"lv2info failed for {uri}", file=sys.stderr)
        return 1
    print(p.stdout)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", default=VOICES_MANIFEST)
    parser.add_argument(
        "--list", action="store_true", help="print every installed LV2 URI"
    )
    parser.add_argument(
        "--inspect", metavar="URI", help="print a plugin's ports and patch properties"
    )
    args = parser.parse_args(argv)

    if args.list:
        return _list_uris()
    if args.inspect:
        return _inspect(args.inspect)
    return _check(args.manifest)


if __name__ == "__main__":
    sys.exit(main())
