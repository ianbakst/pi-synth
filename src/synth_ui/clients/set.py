"""Sets: the rigs for one song, and the store they all live in.

    set = name + [rig, rig, ...]

A set is what you reach for between songs; a rig is what you reach for within
one. Four rigs for a song means entering its set and seeing four pads, not
hunting for four among thirty — and it means the footswitch walks those four,
which is the whole point.

**A rig belongs to exactly one set, and is stored inside it.** Not referenced by
id from a separate rig store: containment rather than reference. A rig is a
config file's worth of bytes, so copying one into a second song is cheap, and
what it buys is worth more than the saving — a shared rig would change under you
between songs, and per-song copies can't. It also removes every class of bug
that cross-references bring: no dangling ids, no renaming a rig out from under
the thing pointing at it, no set referring to a rig that no longer exists.
Removing a rig from a set *is* deleting it.

So this module owns the whole store (`~/.synth-sets.json`), and rig.py owns only
what a rig is.

## Identity

Both sets and rigs carry a uuid, and their names are metadata. Two songs both
wanting a rig called "Rhodes" is normal, and renaming one must not turn it into
a different rig. `~/.synth-state` points at the active set and rig by id for the
same reason: reordering or renaming must not lose your place.

## Surviving a bad file

The store is the instrument, and nothing else on the board can rebuild it. So
every save first keeps the previous version as `.1` (shifting `.1` to `.2`, and
so on), at most once every few minutes so a slider drag doesn't cycle every copy
out in a second. A store that won't parse is set aside as `.corrupt-<time>`,
never written over, and the newest copy that does parse is loaded instead.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field

from synth_ui.clients.rig import Rig, read_rigs
from synth_ui.clients.storage import write_atomic

logger = logging.getLogger(__name__)

# How many earlier versions of the store to keep, and how far apart. Three,
# five minutes apart, means a mistake noticed within a quarter of an hour can be
# undone — and a file damaged on disk has three chances of an intact copy.
BACKUPS = 3
BACKUP_INTERVAL_S = 300.0


def new_id() -> str:
    return str(uuid.uuid4())


@dataclass
class SongSet:
    """One song's worth of rigs, in performance order.

    Owns its rigs outright, so every operation on a rig is a method here. Order
    is what next/previous and a footswitch step through, so it is the user's to
    arrange and it persists.
    """

    name: str
    id: str = field(default_factory=new_id)
    rigs: list[Rig] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "rigs": [r.to_dict() for r in self.rigs],
        }

    @staticmethod
    def from_dict(entry: dict) -> SongSet:
        return SongSet(
            name=entry["name"],
            id=entry.get("id") or new_id(),
            rigs=[Rig.from_dict(r) for r in entry.get("rigs", [])],
        )

    # --- rigs within this set ---------------------------------------------

    def get(self, rig_id: str) -> Rig | None:
        return next((r for r in self.rigs if r.id == rig_id), None)

    def unique_name(self, base: str) -> str:
        """`base`, or `base 2`, `base 3`... Only a courtesy now that rigs have
        ids — duplicate names are legal — but two identical pads in one set are
        indistinguishable on a touchscreen, so auto-naming still counts up."""
        if not any(r.name == base for r in self.rigs):
            return base
        n = 2
        while any(r.name == f"{base} {n}" for r in self.rigs):
            n += 1
        return f"{base} {n}"

    def create_from_voice(self, voice_name: str) -> Rig:
        """A new rig is an instrument with nothing on it yet, created directly
        into this set — a rig cannot exist outside one."""
        rig = Rig(name=self.unique_name(voice_name), voice=voice_name)
        self.rigs.append(rig)
        return rig

    def replace(self, rig: Rig) -> None:
        """Persist edits to a rig, matched by id — not by name, which is the
        very thing a rename changes."""
        for i, existing in enumerate(self.rigs):
            if existing.id == rig.id:
                self.rigs[i] = rig
                return
        self.rigs.append(rig)

    def remove(self, rig_id: str) -> None:
        self.rigs = [r for r in self.rigs if r.id != rig_id]

    def move(self, from_index: int, to_index: int) -> bool:
        """Reorder. This is performance order — the sequence next/previous and
        a footswitch step through — so it's user-controlled, not creation
        order, and it persists with everything else."""
        if not (0 <= from_index < len(self.rigs)):
            return False
        to_index = max(0, min(to_index, len(self.rigs) - 1))
        if from_index == to_index:
            return False
        rig = self.rigs.pop(from_index)
        self.rigs.insert(to_index, rig)
        return True

    def step(self, current_id: str | None, delta: int) -> Rig | None:
        """The next (or previous) rig in this set, wrapping at the ends.

        Wrapping because stopping dead at the last rig is worse mid-song than
        looping round, and a footswitch has no way to show you you've hit the
        end.
        """
        if not self.rigs:
            return None
        ids = [r.id for r in self.rigs]
        if current_id is None or current_id not in ids:
            return self.rigs[0]
        return self.rigs[(ids.index(current_id) + delta) % len(self.rigs)]


class SetLibrary:
    """Every set on this board, and the operations the UI performs on them.

    Kept out of the screens so the rules — unique-ish naming, "there is always
    somewhere to put a rig", persistence, migration — are testable without
    pygame.
    """

    def __init__(self, path: str, sets: list[SongSet] | None = None):
        self.path = path
        self.sets: list[SongSet] = list(sets or [])
        # Set when the store had to be recovered: what happened, in words the
        # player can act on. The UI shows it once; nothing else reads it.
        self.recovery: str | None = None
        self._read_only = False

    @classmethod
    def load(cls, path: str, legacy_rigs_path: str = "") -> SetLibrary:
        """The store; failing that, its newest intact backup; failing that, the
        pre-sets rig file — but only on a board that has never had a store.

        A store that exists but won't parse is not "no sets". Treating it as
        empty is how it used to get overwritten: bootstrap would find nothing
        and save a fresh "Set 1" straight over the damaged original.
        """
        if not os.path.exists(path):
            sets = _migrate_rigs(legacy_rigs_path) if legacy_rigs_path else []
            library = cls(path, sets)
            if sets:
                library.save()
            return library
        try:
            return cls(path, _parse_sets(path))
        except Exception:
            logger.exception("sets store %s is unreadable", path)
        return cls._recover(path)

    @classmethod
    def _recover(cls, path: str) -> SetLibrary:
        kept = _set_aside(path)
        library = cls(path)
        for backup in backup_paths(path):
            try:
                library.sets = _parse_sets(backup)
            except Exception:
                logger.warning("backup %s is unreadable too", backup)
                continue
            saved = time.strftime("%H:%M on %d %b", time.localtime(
                os.path.getmtime(backup)))
            logger.warning("recovered sets from %s", backup)
            library.recovery = f"Sets file was damaged. Restored the copy from {saved}."
            library.save()
            return library
        if kept is None:
            # Still sitting at `path`: anything saved now would overwrite it.
            library._read_only = True
        library.recovery = (
            "Sets file was damaged and no backup could be read. "
            f"Kept as {os.path.basename(kept)}." if kept else
            "Sets file was damaged and could not be moved. Changes won't be saved."
        )
        return library

    def save(self) -> bool:
        if self._read_only:
            return False
        return write_sets(self.path, self.sets)

    def import_sets(self, data) -> list[SongSet]:
        """Add the sets in an export (the store's own format), alongside the
        ones already here, and save. Raises ValueError for anything else.

        Added, never replacing: a replaced store would take the active rig out
        from under what's playing. Every set and rig gets a fresh id so an
        export can be imported twice, or into the board it came from.
        """
        if not isinstance(data, list):
            raise ValueError("expected a list of sets")
        added = []
        try:
            for entry in data:
                song_set = SongSet.from_dict(entry)
                song_set.id = new_id()
                for rig in song_set.rigs:
                    rig.id = new_id()
                song_set.name = self.unique_name(song_set.name)
                added.append(song_set)
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise ValueError(f"not a sets export: {exc}") from exc
        self.sets.extend(added)
        self.save()
        return added

    def get(self, set_id: str) -> SongSet | None:
        return next((s for s in self.sets if s.id == set_id), None)

    def unique_name(self, base: str) -> str:
        if not any(s.name == base for s in self.sets):
            return base
        n = 2
        while any(s.name == f"{base} {n}" for s in self.sets):
            n += 1
        return f"{base} {n}"

    def create(self, name: str = "New Set") -> SongSet:
        song_set = SongSet(name=self.unique_name(name))
        self.sets.append(song_set)
        self.save()
        return song_set

    def remove(self, set_id: str) -> None:
        """Deleting a set deletes the rigs in it — they live nowhere else. The
        UI is responsible for saying so before calling this."""
        self.sets = [s for s in self.sets if s.id != set_id]
        self.save()

    def move(self, from_index: int, to_index: int) -> bool:
        if not (0 <= from_index < len(self.sets)):
            return False
        to_index = max(0, min(to_index, len(self.sets) - 1))
        if from_index == to_index:
            return False
        song_set = self.sets.pop(from_index)
        self.sets.insert(to_index, song_set)
        self.save()
        return True

    def find_rig(self, rig_id: str) -> tuple[SongSet, Rig] | None:
        """The set holding this rig, and the rig. Used to restore what was
        playing without knowing which set it was in."""
        for song_set in self.sets:
            rig = song_set.get(rig_id)
            if rig is not None:
                return song_set, rig
        return None

    def bootstrap(self, default_voice: str) -> SongSet | None:
        """Guarantee somewhere to play on a freshly flashed card. Without this
        the unit boots to an empty list and makes no sound until a set and a rig
        have been built by hand."""
        if self.sets:
            return self.sets[0]
        if not default_voice:
            return None
        song_set = self.create("Set 1")
        song_set.create_from_voice(default_voice)
        self.save()
        return song_set


def _migrate_rigs(rigs_path: str) -> list[SongSet]:
    """Every rig from the pre-sets store, as one set.

    One set rather than one per rig: the rigs were a flat list the user
    arranged, and that order is the only grouping information there is. The old
    file is left untouched, which makes it the backup for this migration.
    """
    rigs = read_rigs(rigs_path)
    if not rigs:
        return []
    logger.info("migrating %d rigs from %s into a set", len(rigs), rigs_path)
    return [SongSet(name="My Rigs", rigs=rigs)]


def read_sets(path: str) -> list[SongSet]:
    """Parse the store. Missing or malformed = no sets (never fatal: the
    instrument still boots, and bootstrap gives it something to play)."""
    if not os.path.exists(path):
        return []
    try:
        return _parse_sets(path)
    except Exception:
        logger.exception("could not read sets from %s", path)
        return []


def _parse_sets(path: str) -> list[SongSet]:
    """The store's sets, or an exception: for callers that need to tell a
    damaged file from an empty one."""
    with open(path) as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"expected a list of sets, got {type(data).__name__}")
    return [SongSet.from_dict(entry) for entry in data]


def write_sets(
    path: str,
    sets: list[SongSet],
    backups: int = BACKUPS,
    backup_interval: float = BACKUP_INTERVAL_S,
) -> bool:
    """Persist the store atomically and durably (see storage.py) — this is the
    user's own work, unlike the catalog, and isn't reproducible from the image.
    Keeps the version it replaces as a backup when the last one is old enough.
    """
    try:
        if backups > 0:
            _rotate_backups(path, backups, backup_interval)
        write_atomic(
            path, json.dumps([s.to_dict() for s in sets], indent=2) + "\n"
        )
        return True
    except OSError:
        logger.exception("could not write sets to %s", path)
        return False


def backup_paths(path: str, backups: int = BACKUPS) -> list[str]:
    """Existing backups of the store, newest first."""
    return [p for p in (f"{path}.{n}" for n in range(1, backups + 1))
            if os.path.exists(p)]


def _rotate_backups(path: str, backups: int, interval: float) -> None:
    """Shift `.1`..`.n-1` up one and copy the current store to `.1`.

    Skipped while `.1` is younger than `interval`, and skipped for a store that
    doesn't parse — a bad file must never push a good copy off the end.
    """
    newest = f"{path}.1"
    if not os.path.exists(path):
        return
    if os.path.exists(newest) and time.time() - os.path.getmtime(newest) < interval:
        return
    try:
        _parse_sets(path)
    except Exception:
        return
    for n in range(backups - 1, 0, -1):
        if os.path.exists(f"{path}.{n}"):
            os.replace(f"{path}.{n}", f"{path}.{n + 1}")
    with open(path, "rb") as f:
        write_atomic(newest, f.read())


def _set_aside(path: str) -> str | None:
    """Move a damaged store out of the way, keeping it: it may still be
    repairable by hand, and whatever is saved next must not land on top of it.
    Returns where it went, or None if it couldn't be moved."""
    kept = f"{path}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}"
    try:
        os.replace(path, kept)
        return kept
    except OSError:
        logger.exception("could not set aside damaged store %s", path)
        return None
