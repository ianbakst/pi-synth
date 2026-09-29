"""Download and unpack a sample library onto this board.

SoundFonts need none of this — drop a .sf2 in the soundfont directory and it is
discovered on the next library reload, which is the whole point of that design.
SFZ libraries can't work that way: they are a directory of thousands of samples
plus a mapping file, far too large to ship in the OS image and too awkward to
carry over USB.

    python3 -m synth_ui.tools.install_library --list
    python3 -m synth_ui.tools.install_library salamander
    python3 -m synth_ui.tools.install_library salamander --patch

It prints the .sfz path to put in voices.json rather than editing the manifest
itself: which of several mappings a library exposes is a judgement call (dry vs
release-resonance, light vs heavy velocity curves), not something to guess at.

Some mappings don't play properly as shipped, so a library can carry a
`SfzPatch`: a rewritten copy written next to the original, which is left alone.
`--patch` writes it for a library that is already installed, with no download.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import stat
import sys
import tarfile
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass

from synth_ui.config import INSTRUMENTS_DIR


class PatchError(Exception):
    pass


@dataclass
class SfzPatch:
    # Both relative to the library's directory. The output sits beside the
    # source, because an SFZ's sample paths are relative to the file itself.
    source: str
    output: str
    transform: Callable[[str], str]


@dataclass
class Library:
    name: str
    url: str
    megabytes: int
    license: str
    note: str = ""
    patch: SfzPatch | None = None


# Salamander's own group numbers are 1 and 2 (the pedal noises); stay clear.
_RELEASE_GROUP = 10
# Reproduced offline against sfizz with this library: a chord held under 80
# pedalled notes survives pedal-up at 96 voices with these two limits in place,
# and is killed at every voice count up to sfizz's maximum of 256 without them.
_RELEASE_POLYPHONY = 32
_NOTE_POLYPHONY = 2

_GROUP_RE = re.compile(r"^<group>(?P<body>.*?)(?P<eol>\r?\n?)$")


def patch_salamander(text: str) -> str:
    """Stop pedal-up from cutting off the notes still being held.

    Every note let go under the pedal leaves three release samples waiting
    (two string resonances and hammer noise), and sfizz starts all of them at
    once when the pedal comes up. That burst outgrows any voice limit, and
    sfizz makes room by killing its *oldest* voices, which are the keys still
    held down. So:

      - the release samples get a polyphony group of their own, so they steal
        from each other and never from a played note;
      - each note gets `note_polyphony`, so re-striking a key under the pedal
        replaces its older voice instead of stacking another one, as a real
        piano's strings do. That keeps the sustained notes themselves under
        the voice limit on a long pedalled passage.

    Line endings are kept: the shipped file is CRLF.
    """
    lines = text.splitlines(keepends=True)
    release = notes = 0
    for i, line in enumerate(lines):
        m = _GROUP_RE.match(line)
        if not m:
            continue
        body = m.group("body").rstrip()
        opcodes = body.split()
        if "trigger=release" in opcodes:
            body += f" group={_RELEASE_GROUP} polyphony={_RELEASE_POLYPHONY}"
            release += 1
        elif not any(o.startswith(("trigger=", "group=")) for o in opcodes):
            body += f" note_polyphony={_NOTE_POLYPHONY}"
            notes += 1
        else:
            continue
        lines[i] = f"<group>{body}{m.group('eol')}"
    # A different release of the file would be patched half-way without
    # anyone noticing; refuse instead.
    if (release, notes) != (8, 2):
        raise PatchError(
            f"expected 8 release groups and 2 note groups, found {release} and "
            f"{notes}: not the Salamander V3 mapping this patch was written for"
        )
    return "".join(lines)


# 48 kHz because JACK runs at 48 kHz and anything else is resampled on every
# note. WAV rather than the smaller FLAC build: sfizz streams from disk during
# playback, and FLAC would put a decode on the audio thread — which is the last
# place this box needs more work, given sfizz is already the suspected source of
# its xruns.
_BANDSHED = "https://www.bandshed.net/sounds/sfz"

LIBRARIES: dict[str, Library] = {
    "salamander": Library(
        name="Salamander Grand Piano V3",
        url="https://freepats.zenvoid.org/Piano/SalamanderGrandPiano/"
            "SalamanderGrandPianoV3+20161209_48khz24bit.tar.xz",
        megabytes=1208,
        license="CC-BY-3.0 (Alexander Holm)",
        note="Yamaha C5, 16 velocity layers. 48kHz/24-bit WAV.",
        patch=SfzPatch(
            source="SalamanderGrandPianoV3_48khz24bit/SalamanderGrandPianoV3.sfz",
            output="SalamanderGrandPianoV3_48khz24bit/"
                   "SalamanderGrandPianoV3-pisynth.sfz",
            transform=patch_salamander,
        ),
    ),
    # The rest are from the No-Budget Orchestra collection's sample host. Zip
    # archives of 44.1 kHz WAV; sizes are unpacked. Neither keyboard archive
    # carries a licence file; the orchestra has one per instrument.
    "rhodes": Library(
        name="Stereo Rhodes",
        url=f"{_BANDSHED}/stereo_rhodes.zip",
        megabytes=77,
        license="free for music use (bandshed.net, no licence file)",
        note="Fender Rhodes, stereo. Mapping: StereoRhodes/rhodes.sfz",
    ),
    "wurlitzer": Library(
        name="Wurlitzer",
        url=f"{_BANDSHED}/wurt.zip",
        megabytes=8,
        license="free for music use (bandshed.net, no licence file)",
        note="Wurlitzer electric piano. Mapping: Wurt/wurly.sfz",
    ),
    "clavinet": Library(
        name="Clavinet",
        url=f"{_BANDSHED}/clavinet.zip",
        megabytes=19,
        license="free for music use (bandshed.net, no licence file)",
        note="Hohner Clavinet. Mapping: Clavinet/clavinet.sfz",
    ),
    "nbo": Library(
        name="No-Budget Orchestra 2",
        url=f"{_BANDSHED}/nbo_2.zip",
        megabytes=721,
        license="per instrument, see each license.txt (mostly CC BY-SA 4.0, "
                "Jeff Glatt; the rest CC Sampling+/CC0 freesound packs)",
        note="Strings, brass, woodwinds, choir, orchestral percussion — "
             "~250 mappings under NoBudgetOrch/.",
    ),
}


def _safe_members(tar: tarfile.TarFile, dest: str):
    """Yield only members that stay inside `dest`.

    An archive can name "../../etc/whatever" or a symlink pointing out of the
    tree. These come off the public internet, so the extraction is filtered
    rather than trusted.
    """
    dest = os.path.realpath(dest)
    for member in tar:
        target = os.path.realpath(os.path.join(dest, member.name))
        if not (target == dest or target.startswith(dest + os.sep)):
            print(f"  skipping {member.name}: escapes the target directory",
                  file=sys.stderr)
            continue
        if member.issym() or member.islnk():
            link = os.path.realpath(os.path.join(dest, os.path.dirname(member.name),
                                                 member.linkname))
            if not link.startswith(dest + os.sep):
                print(f"  skipping link {member.name}: points outside",
                      file=sys.stderr)
                continue
        yield member


def _safe_zip_names(zf: zipfile.ZipFile, dest: str):
    """The zip counterpart of `_safe_members`: names that stay inside `dest`.
    Symlinks are dropped outright — zipfile would write them out as small text
    files holding the link target, which is never what a sample library meant.
    """
    dest = os.path.realpath(dest)
    for info in zf.infolist():
        target = os.path.realpath(os.path.join(dest, info.filename))
        if not (target == dest or target.startswith(dest + os.sep)):
            print(f"  skipping {info.filename}: escapes the target directory",
                  file=sys.stderr)
            continue
        if stat.S_ISLNK(info.external_attr >> 16):
            print(f"  skipping link {info.filename}", file=sys.stderr)
            continue
        yield info.filename


def _extract(archive: str, target: str) -> None:
    """Unpack a tar (any compression) or zip archive into `target`. Raises
    tarfile.TarError, zipfile.BadZipFile or OSError."""
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(target, members=list(_safe_zip_names(zf, target)))
        return
    with tarfile.open(archive) as tar:
        members = _safe_members(tar, target)
        # `filter="data"` is belt-and-braces on top of _safe_members: it also
        # drops setuid bits and device nodes. It landed in 3.11.4 and becomes
        # the default in 3.14, so ask for it only where it exists rather than
        # pinning the board's Python version.
        if hasattr(tarfile, "data_filter"):
            tar.extractall(target, members=members, filter="data")
        else:
            tar.extractall(target, members=members)


def _free_megabytes(path: str) -> int:
    while not os.path.exists(path):
        path = os.path.dirname(path) or "/"
    return shutil.disk_usage(path).free // (1024 * 1024)


def _download(url: str, dest: str) -> bool:
    def progress(count: int, block: int, total: int) -> None:
        if total <= 0:
            return
        done = min(count * block, total)
        print(f"\r  {done / 1e6:.0f} / {total / 1e6:.0f} MB "
              f"({done * 100 // total}%)", end="", file=sys.stderr)

    # bandshed.net answers Python's default User-Agent with 403.
    opener = urllib.request.build_opener()
    opener.addheaders = [("User-Agent", "pi-synth install_library")]
    urllib.request.install_opener(opener)
    try:
        urllib.request.urlretrieve(url, dest, reporthook=progress)
        print(file=sys.stderr)
        return True
    except OSError as exc:
        print(f"\ndownload failed: {exc}", file=sys.stderr)
        return False


def apply_patch(key: str, root: str) -> int:
    """Write a library's patched mapping from its original. Always from the
    original, so running it again changes nothing."""
    patch = LIBRARIES[key].patch
    if patch is None:
        print(f"{key} has no patch", file=sys.stderr)
        return 0
    target = os.path.join(root, key)
    source = os.path.join(target, patch.source)
    output = os.path.join(target, patch.output)
    try:
        # newline="" keeps CRLF files CRLF.
        with open(source, encoding="utf-8", newline="") as f:
            text = patch.transform(f.read())
        with open(output, "w", encoding="utf-8", newline="") as f:
            f.write(text)
    except (OSError, PatchError) as exc:
        print(f"patch failed: {exc}", file=sys.stderr)
        return 1
    print(f"  patched mapping: {output}")
    return 0


def install(key: str, root: str, keep_archive: bool = False) -> int:
    library = LIBRARIES[key]
    target = os.path.join(root, key)

    # Unpacking needs room for the archive and its contents at once.
    needed = library.megabytes * 2
    free = _free_megabytes(root)
    if free < needed:
        print(f"not enough space: {free} MB free, need about {needed} MB "
              f"(archive + unpacked)", file=sys.stderr)
        return 1

    os.makedirs(target, exist_ok=True)
    archive = os.path.join(target, os.path.basename(library.url))
    print(f"{library.name} — {library.megabytes} MB, {library.license}")
    if not os.path.exists(archive):
        if not _download(library.url, archive):
            return 1
    else:
        print("  archive already present, reusing it")

    print("  unpacking ...", file=sys.stderr)
    try:
        _extract(archive, target)
    except (tarfile.TarError, zipfile.BadZipFile, OSError) as exc:
        print(f"unpack failed: {exc}", file=sys.stderr)
        return 1
    if not keep_archive:
        os.remove(archive)
    if library.patch is not None and apply_patch(key, root) != 0:
        return 1

    mappings = sorted(
        os.path.join(dirpath, f)
        for dirpath, _, files in os.walk(target)
        for f in files
        if f.lower().endswith(".sfz")
    )
    if not mappings:
        print("unpacked, but no .sfz mapping found — is this an SFZ library?",
              file=sys.stderr)
        return 1

    print(f"\ninstalled to {target}")
    print("\n.sfz mappings — put one of these in voices.json as \"path\":")
    for path in mappings:
        print(f"  {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("library", nargs="?", choices=sorted(LIBRARIES))
    parser.add_argument("--root", default=INSTRUMENTS_DIR)
    parser.add_argument("--list", action="store_true", help="show what's available")
    parser.add_argument("--keep-archive", action="store_true",
                        help="don't delete the archive after unpacking")
    parser.add_argument("--patch", action="store_true",
                        help="only rewrite the patched mapping of an installed "
                             "library")
    args = parser.parse_args(argv)

    if args.list or not args.library:
        for key, lib in sorted(LIBRARIES.items()):
            print(f"  {key:<14} {lib.name}  ({lib.megabytes} MB, {lib.license})")
            if lib.note:
                print(f"  {'':<14} {lib.note}")
        return 0
    if args.patch:
        return apply_patch(args.library, args.root)
    return install(args.library, args.root, args.keep_archive)


if __name__ == "__main__":
    sys.exit(main())
