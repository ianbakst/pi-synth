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
import os
import re
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
            # Show what's actually missing: the plugin URI for a plugin problem,
            # the file path for a file problem. Showing the path next to "plugin
            # not installed" sent you looking for the wrong thing.
            if "plugin" in voice.unavailable_reason:
                detail = spec.uri if spec else ""
            else:
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


def _lv2info(uri: str) -> str | None:
    try:
        p = subprocess.run(
            ["lv2info", uri], capture_output=True, text=True, timeout=30
        )
    except FileNotFoundError:
        print("lv2info not installed (apt install lilv-utils)", file=sys.stderr)
        return None
    except subprocess.TimeoutExpired:
        return None
    if p.returncode != 0:
        print(p.stderr.strip() or f"lv2info failed for {uri}", file=sys.stderr)
        return None
    return p.stdout


def _control_ports(info: str) -> list[tuple[str, str]]:
    """(symbol, name) for each input control port, in port order."""
    ports: list[tuple[str, str]] = []
    for block in info.split("\n\n"):
        if "ControlPort" not in block or "InputPort" not in block:
            continue
        symbol = re.search(r"Symbol:\s*(\S+)", block)
        name = re.search(r"Name:\s*(.+)", block)
        if symbol:
            ports.append((symbol.group(1), name.group(1).strip() if name else ""))
    return ports


def _bundle_dir(info: str) -> str | None:
    m = re.search(r"Bundle:\s*file://(\S+)", info)
    return m.group(1) if m else None


_PREFIX_RE = re.compile(r"@prefix\s+([A-Za-z][\w.-]*):\s*<([^>]+)>\s*\.")
# A turtle resource is either <a full URI> or a prefix:localName.
_TERM = r"(?:<[^>]+>|[A-Za-z][\w.-]*:[\w.-]+)"
_WRITABLE_RE = re.compile(rf"patch:writable\s+((?:{_TERM}\s*,?\s*)+)")
_TERM_RE = re.compile(_TERM)


def _expand(term: str, prefixes: dict[str, str]) -> str:
    """Turn a turtle term into a full URI."""
    if term.startswith("<"):
        return term[1:-1]
    pfx, _, local = term.partition(":")
    return prefixes.get(pfx, pfx + ":") + local


def _patch_properties(bundle: str) -> list[tuple[str, str]]:
    """(property URI, label) for every patch:writable the bundle declares.

    This is the question lv2info can't answer and the one that matters most: a
    plugin that loads an instrument *file* does it through an atom-based patch
    property, not a control port — `param_set` silently no-ops on those.

    Terms are matched in BOTH turtle spellings. Only handling `<full URIs>`
    silently reported "no properties" for Fluida, whose entire parameter set
    (including the soundfont path) is written as `fluida:soundfont` against an
    `@prefix` — which is the more common style, so the omission hid the very
    thing this function exists to find.

    Deliberately a text scan, not an RDF parse: it needs no new dependency on
    the board, and these two spellings cover what plugin bundles actually use.
    """
    found: dict[str, str] = {}
    try:
        names = sorted(os.listdir(bundle))
    except OSError:
        return []
    for name in names:
        if not name.endswith(".ttl"):
            continue
        try:
            with open(os.path.join(bundle, name), errors="replace") as f:
                text = f.read()
        except OSError:
            continue

        prefixes = dict(_PREFIX_RE.findall(text))
        for match in _WRITABLE_RE.finditer(text):
            for term in _TERM_RE.findall(match.group(0).split(None, 1)[1]):
                uri = _expand(term, prefixes)
                found.setdefault(uri, "")
                # The label (and the file-type hint) live on the parameter's own
                # declaration, keyed by the term as written.
                decl = re.search(
                    rf"{re.escape(term)}\s+a\s+lv2:Parameter", text
                )
                if not decl:
                    continue
                window = text[decl.start() : decl.start() + 400]
                label = re.search(r'rdfs:label\s+"([^"]+)"', window)
                ftype = re.search(r'mod:fileTypes\s+"([^"]+)"', window)
                if label:
                    found[uri] = label.group(1)
                if ftype:
                    # Mark file-taking properties: this is the one a voice's
                    # `path` goes through, and it's what makes a soundfont or
                    # sample library reachable at all.
                    found[uri] = f"{found[uri] or 'file'} [file: {ftype.group(1)}]"
    return sorted(found.items())


def _inspect(uri: str) -> int:
    """What a voice needs to drive this plugin: its file property, its controls,
    and its presets."""
    info = _lv2info(uri)
    if info is None:
        return 1

    bundle = _bundle_dir(info)
    print(f"URI:    {uri}")
    print(f"Bundle: {bundle or 'unknown'}\n")

    props = _patch_properties(bundle) if bundle else []
    print("patch:writable properties (use as `file_property`):")
    if props:
        for prop, label in props:
            print(f"  {prop}{f'   [{label}]' if label else ''}")
    else:
        print("  (none — this plugin takes no instrument file)")

    print("\ninput control ports (use as `params`):")
    for symbol, name in _control_ports(info):
        print(f"  {symbol:<24} {name}")

    presets = re.findall(r"Preset:\s*(\S+)", info)
    print("\npresets (use as `preset`):")
    for preset in presets or ["  (none)"]:
        print(f"  {preset}" if presets else preset)
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
