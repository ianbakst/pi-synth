"""Backing the sets up to a USB stick, and bringing them back.

The file is exactly what the browser editor's Export downloads, and named the
same way (`synth-sets-*.json`), so the two are interchangeable: a backup made
here can be imported from a laptop, and an export copied onto a stick can be
restored from the touchscreen.

Sticks are mounted by udev/99-usb-automount.rules under /media/synth/<device>,
on demand and unmounted again seconds after the last access — so by the time
the screen says a backup is written, the stick is safe to pull.
"""

from __future__ import annotations

import getpass
import glob
import json
import logging
import os
import socket
import time
from dataclasses import dataclass

from synth_ui.clients.storage import write_atomic

logger = logging.getLogger(__name__)

PREFIX = "synth-sets-"


def media_roots() -> list[str]:
    """Where removable drives are mounted: the automount rule's directory, then
    the places a desktop or a hand-typed `mount` would put them."""
    return [f"/media/{getpass.getuser()}", "/media", "/mnt"]


def usb_drives(roots: list[str] | None = None) -> list[str]:
    """Mounted drives, one directory each, in a stable order."""
    roots = media_roots() if roots is None else roots
    drives: list[str] = []
    seen: set[str] = set()
    for root in roots:
        try:
            entries = sorted(os.scandir(root), key=lambda e: e.name)
        except OSError:
            continue
        for entry in entries:
            path = entry.path
            # /media/<user> is itself a child of /media; it holds drives, it
            # isn't one.
            if path in roots:
                continue
            try:
                if not (entry.is_dir() and os.path.ismount(path)):
                    continue
            except OSError:
                continue       # a stick pulled while its mount was being read
            real = os.path.realpath(path)
            if real not in seen:
                seen.add(real)
                drives.append(path)
    return drives


def write_backup(drive: str, sets: list[dict]) -> str:
    """Write `sets` to a new file at the top of `drive`. Returns its path;
    raises OSError. Never overwrites an earlier backup: the name carries the
    minute, and the board's name, so one stick can serve several boards."""
    stamp = time.strftime("%Y-%m-%d-%H%M")
    host = socket.gethostname().split(".")[0] or "synth"
    path = os.path.join(drive, f"{PREFIX}{host}-{stamp}.json")
    n = 2
    while os.path.exists(path):
        path = os.path.join(drive, f"{PREFIX}{host}-{stamp}-{n}.json")
        n += 1
    write_atomic(path, json.dumps(sets, indent=2) + "\n")
    return path


@dataclass(frozen=True)
class Backup:
    path: str
    sets: list[dict]

    @property
    def name(self) -> str:
        return os.path.basename(self.path)


def newest_backup(drives: list[str]) -> Backup | None:
    """The most recently written backup that parses, on any drive.

    The newest rather than a choice between them: restoring is for a fresh or
    rebuilt card, where the last backup is the one wanted. An older one can
    still be imported from the browser editor.
    """
    candidates: list[tuple[float, str]] = []
    for drive in drives:
        for path in glob.glob(os.path.join(drive, f"{PREFIX}*.json")):
            if os.path.basename(path).startswith("."):
                continue       # macOS's ._ resource-fork shadows on FAT sticks
            try:
                candidates.append((os.path.getmtime(path), path))
            except OSError:
                continue
    for _mtime, path in sorted(candidates, reverse=True):
        try:
            with open(path) as f:
                data = json.load(f)
        except (OSError, ValueError):
            logger.warning("skipping unreadable backup %s", path)
            continue
        if isinstance(data, list):
            return Backup(path, data)
    return None
