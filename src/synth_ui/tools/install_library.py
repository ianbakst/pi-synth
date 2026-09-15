"""Download and unpack a sample library onto this board.

SoundFonts need none of this — drop a .sf2 in the soundfont directory and it is
discovered on the next library reload, which is the whole point of that design.
SFZ libraries can't work that way: they are a directory of thousands of samples
plus a mapping file, far too large to ship in the OS image and too awkward to
carry over USB.

    python3 -m synth_ui.tools.install_library --list
    python3 -m synth_ui.tools.install_library salamander

It prints the .sfz path to put in voices.json rather than editing the manifest
itself: which of several mappings a library exposes is a judgement call (dry vs
release-resonance, light vs heavy velocity curves), not something to guess at.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tarfile
import urllib.request
from dataclasses import dataclass

from synth_ui.config import INSTRUMENTS_DIR


@dataclass
class Library:
    name: str
    url: str
    megabytes: int
    license: str
    note: str = ""


# 48 kHz because JACK runs at 48 kHz and anything else is resampled on every
# note. WAV rather than the smaller FLAC build: sfizz streams from disk during
# playback, and FLAC would put a decode on the audio thread — which is the last
# place this box needs more work, given sfizz is already the suspected source of
# its xruns.
LIBRARIES: dict[str, Library] = {
    "salamander": Library(
        name="Salamander Grand Piano V3",
        url="https://freepats.zenvoid.org/Piano/SalamanderGrandPiano/"
            "SalamanderGrandPianoV3+20161209_48khz24bit.tar.xz",
        megabytes=1208,
        license="CC-BY-3.0 (Alexander Holm)",
        note="Yamaha C5, 16 velocity layers. 48kHz/24-bit WAV.",
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

    try:
        urllib.request.urlretrieve(url, dest, reporthook=progress)
        print(file=sys.stderr)
        return True
    except OSError as exc:
        print(f"\ndownload failed: {exc}", file=sys.stderr)
        return False


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
    except (tarfile.TarError, OSError) as exc:
        print(f"unpack failed: {exc}", file=sys.stderr)
        return 1
    if not keep_archive:
        os.remove(archive)

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
                        help="don't delete the tarball after unpacking")
    args = parser.parse_args(argv)

    if args.list or not args.library:
        for key, lib in sorted(LIBRARIES.items()):
            print(f"  {key:<14} {lib.name}  ({lib.megabytes} MB, {lib.license})")
            if lib.note:
                print(f"  {'':<14} {lib.note}")
        return 0
    return install(args.library, args.root, args.keep_archive)


if __name__ == "__main__":
    sys.exit(main())
