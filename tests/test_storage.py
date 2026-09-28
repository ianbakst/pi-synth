"""Tests for writing the board's own files safely. No hardware."""

import pytest

from synth_ui.clients import storage
from synth_ui.clients.storage import write_atomic


def test_writes_text_and_bytes(tmp_path):
    write_atomic(str(tmp_path / "a"), "text")
    write_atomic(str(tmp_path / "b"), b"\x00bytes")
    assert (tmp_path / "a").read_text() == "text"
    assert (tmp_path / "b").read_bytes() == b"\x00bytes"


def test_replaces_and_leaves_no_temp_file(tmp_path):
    path = tmp_path / "f"
    path.write_text("old")
    write_atomic(str(path), "new")
    assert path.read_text() == "new"
    assert [p.name for p in tmp_path.iterdir()] == ["f"]


def test_creates_the_directory(tmp_path):
    write_atomic(str(tmp_path / "sub" / "f"), "x")
    assert (tmp_path / "sub" / "f").read_text() == "x"


def test_a_failed_write_leaves_the_old_file_whole(tmp_path, monkeypatch):
    path = tmp_path / "f"
    path.write_text("old")

    def boom(fd):
        raise OSError("card pulled")

    monkeypatch.setattr(storage.os, "fsync", boom)
    with pytest.raises(OSError):
        write_atomic(str(path), "new")
    assert path.read_text() == "old"
    assert [p.name for p in tmp_path.iterdir()] == ["f"]


def test_not_durable_skips_fsync(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(storage.os, "fsync", calls.append)
    write_atomic(str(tmp_path / "f"), "x", durable=False)
    assert calls == []
    assert (tmp_path / "f").read_text() == "x"
