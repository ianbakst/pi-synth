"""Writing the board's own files so that pulling the plug can't damage them.

The box has no power switch: it is turned off by pulling the plug, whenever the
player is done, including in the middle of a save. A plain `open(path, "w")`
truncates the file first and fills it after, so a pull in between leaves an
empty file where the user's work was. Temp file + rename fixes the ordering —
the old file stays whole until the new one replaces it in one step — but only
once the new contents have actually reached the card, which is what the fsyncs
are for. Without them the rename can be on disk before the data it points at.
"""

from __future__ import annotations

import os
import tempfile


def write_atomic(path: str, data: str | bytes, durable: bool = True) -> None:
    """Replace `path` with `data`: afterwards the file holds either the old
    contents or the new, never a mix and never nothing. Raises OSError.

    `durable=False` skips the fsyncs, for a file that is rewritten many times a
    second and would only lose its latest value (on ext4, which orders a
    replacing rename after its data, the result is still never empty).
    """
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data.encode() if isinstance(data, str) else data)
            if durable:
                f.flush()
                os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    if durable:
        fsync_directory(directory)


def fsync_directory(directory: str) -> None:
    """Make a rename in `directory` durable. Best effort: some filesystems
    (and every non-Linux dev machine) refuse to open or sync a directory, and
    that must not turn a successful save into a failed one."""
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)
