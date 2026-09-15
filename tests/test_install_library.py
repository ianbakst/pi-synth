"""Tests for the sample-library installer.

The extraction filter is the part worth testing: these archives come off the
public internet, and tarfile will happily write outside the target directory if
asked to.
"""

import os
import tarfile

import pytest

from synth_ui.tools.install_library import LIBRARIES, _safe_members, install, main


def _archive(tmp_path, entries):
    """Build a tar whose members are (name, is_symlink, linkname)."""
    path = tmp_path / "a.tar"
    with tarfile.open(path, "w") as tar:
        for name, linkname in entries:
            info = tarfile.TarInfo(name)
            if linkname:
                info.type, info.linkname = tarfile.SYMTYPE, linkname
                tar.addfile(info)
            else:
                data = b"x"
                info.size = len(data)
                tar.addfile(info, __import__("io").BytesIO(data))
    return path


class TestSafeMembers:
    def test_ordinary_members_pass(self, tmp_path):
        archive = _archive(tmp_path, [("lib/piano.sfz", None), ("lib/a.wav", None)])
        dest = tmp_path / "out"
        dest.mkdir()
        with tarfile.open(archive) as tar:
            names = [m.name for m in _safe_members(tar, str(dest))]
        assert names == ["lib/piano.sfz", "lib/a.wav"]

    def test_path_traversal_is_skipped(self, tmp_path):
        """"../../etc/passwd" must not be written outside the target."""
        archive = _archive(tmp_path, [("../escape.txt", None), ("ok.sfz", None)])
        dest = tmp_path / "out"
        dest.mkdir()
        with tarfile.open(archive) as tar:
            names = [m.name for m in _safe_members(tar, str(dest))]
        assert names == ["ok.sfz"]

    def test_symlink_pointing_outside_is_skipped(self, tmp_path):
        archive = _archive(tmp_path, [("evil", "../../../../etc/passwd"),
                                      ("ok.sfz", None)])
        dest = tmp_path / "out"
        dest.mkdir()
        with tarfile.open(archive) as tar:
            names = [m.name for m in _safe_members(tar, str(dest))]
        assert names == ["ok.sfz"]


class TestInstall:
    def test_refuses_when_the_disk_is_too_small(self, tmp_path, monkeypatch, capsys):
        """Better to say so than to fill the card and fail mid-unpack."""
        monkeypatch.setattr(
            "synth_ui.tools.install_library._free_megabytes", lambda _p: 10
        )
        assert install("salamander", str(tmp_path)) == 1
        assert "not enough space" in capsys.readouterr().err

    def test_reports_the_sfz_paths_it_found(self, tmp_path, monkeypatch, capsys):
        archive = _archive(tmp_path, [("lib/Piano.sfz", None), ("lib/a.wav", None)])
        monkeypatch.setattr(
            "synth_ui.tools.install_library._free_megabytes", lambda _p: 999_999
        )

        def fake_download(url, dest):
            os.replace(str(archive), dest)
            return True

        monkeypatch.setattr("synth_ui.tools.install_library._download", fake_download)
        monkeypatch.setitem(
            LIBRARIES, "salamander",
            LIBRARIES["salamander"].__class__(
                name="t", url="http://example/a.tar", megabytes=1, license="x"
            ),
        )
        assert install("salamander", str(tmp_path / "root")) == 0
        assert "Piano.sfz" in capsys.readouterr().out


class TestCLI:
    def test_list_shows_the_catalogue(self, capsys):
        assert main(["--list"]) == 0
        assert "salamander" in capsys.readouterr().out

    def test_no_argument_lists_rather_than_erroring(self, capsys):
        assert main([]) == 0
        assert "salamander" in capsys.readouterr().out

    def test_unknown_library_is_rejected(self):
        with pytest.raises(SystemExit):
            main(["nonexistent"])


class TestCatalogue:
    def test_every_entry_is_complete(self):
        for key, lib in LIBRARIES.items():
            assert lib.url.startswith("https://"), key
            assert lib.megabytes > 0, key
            assert lib.license, key
