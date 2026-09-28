"""Tests for backing sets up to a USB stick. No hardware: a temp directory
stands in for /media, with ismount patched to say which children are drives."""

import json
import os

import pytest

from synth_ui.clients import usb_backup
from synth_ui.clients.usb_backup import newest_backup, usb_drives, write_backup

SETS = [{"id": "a", "name": "Gig", "rigs": []}]


@pytest.fixture
def media(tmp_path, monkeypatch):
    """/media with one user dir holding one stick, and a plain folder that
    isn't a mount."""
    user = tmp_path / "synth"
    stick = user / "sda1"
    stick.mkdir(parents=True)
    (tmp_path / "not-a-drive").mkdir()
    mounts = {str(stick)}
    monkeypatch.setattr(usb_backup.os.path, "ismount", lambda p: str(p) in mounts)
    return tmp_path, user, stick


def test_finds_mounted_drives_only(media):
    root, user, stick = media
    assert usb_drives([str(user), str(root)]) == [str(stick)]


def test_a_missing_root_is_not_an_error(tmp_path):
    assert usb_drives([str(tmp_path / "nope")]) == []


def test_backup_round_trips_and_uses_the_export_name(media):
    _root, _user, stick = media
    path = write_backup(str(stick), SETS)
    assert os.path.basename(path).startswith("synth-sets-")
    backup = newest_backup([str(stick)])
    assert backup is not None and backup.path == path and backup.sets == SETS


def test_a_second_backup_in_the_same_minute_does_not_overwrite(media):
    _root, _user, stick = media
    first = write_backup(str(stick), SETS)
    second = write_backup(str(stick), [])
    assert first != second
    assert json.loads(open(first).read()) == SETS


def test_restore_picks_the_newest_readable_backup(media):
    _root, _user, stick = media
    old = stick / "synth-sets-2026-01-01.json"       # a browser export
    old.write_text(json.dumps(SETS))
    torn = stick / "synth-sets-board-2026-09-27-1200.json"
    torn.write_text("{ torn")
    os.utime(old, (1, 1))
    backup = newest_backup([str(stick)])
    assert backup is not None and backup.name == old.name


def test_ignores_other_files_and_mac_shadow_files(media):
    _root, _user, stick = media
    (stick / "notes.json").write_text(json.dumps(SETS))
    (stick / "._synth-sets-x.json").write_text(json.dumps(SETS))
    assert newest_backup([str(stick)]) is None
