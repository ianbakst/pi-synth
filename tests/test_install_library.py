"""Tests for the sample-library installer.

The extraction filter is the part worth testing: these archives come off the
public internet, and tarfile will happily write outside the target directory if
asked to.
"""

import os
import tarfile

import pytest

from synth_ui.tools.install_library import (
    LIBRARIES,
    PatchError,
    _safe_members,
    install,
    main,
    patch_salamander,
)


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


# The shipped mapping's group headers, verbatim, CRLF as shipped; one region
# under each is enough.
_SALAMANDER = "".join(
    line + "\r\n"
    for line in [
        "//Notes",
        "<group> amp_veltrack=73 ampeg_release=1",
        "<region> sample=48khz24bit\\A0v1.wav lokey=21 hikey=22 lovel=1 hivel=26",
        "<group> amp_veltrack=73 ampeg_release=5",
        "<region> sample=48khz24bit\\F#6v1.wav lokey=89 hikey=91 lovel=1 hivel=26",
        "<group> trigger=release volume=-4 amp_veltrack=94 rt_decay=6",
        "<group> trigger=release volume=-4 amp_veltrack=94 rt_decay=7",
        "<group> trigger=release volume=-4 amp_veltrack=90 rt_decay=8 ",
        "<group> trigger=release volume=-4 amp_veltrack=90 rt_decay=9",
        "<group> trigger=release amp_veltrack=95 rt_decay=7",
        "<group> trigger=release amp_veltrack=90 rt_decay=7",
        "<group> trigger=release amp_veltrack=96 rt_decay=2",
        "<group> trigger=release pitch_keytrack=0 volume=-37 amp_veltrack=82 "
        "rt_decay=2",
        "<region> sample=48khz24bit\\rel1.wav lokey=21 hikey=21",
        "<group> group=1 hikey=-1 lokey=-1 on_locc64=126 on_hicc64=127 off_by=2 "
        "volume=-20",
        "<group> group=2 hikey=-1 lokey=-1 on_locc64=0 on_hicc64=1 volume=-19",
    ]
)


class TestSalamanderPatch:
    def _groups(self, text):
        return [line for line in text.split("\r\n") if line.startswith("<group>")]

    def test_release_samples_get_their_own_capped_group(self):
        """So the pedal-up burst steals from itself, not from held notes."""
        release = [g for g in self._groups(patch_salamander(_SALAMANDER))
                   if "trigger=release" in g]
        assert len(release) == 8
        assert all(g.endswith(" group=10 polyphony=32") for g in release)

    def test_notes_get_note_polyphony(self):
        notes = [g for g in self._groups(patch_salamander(_SALAMANDER))
                 if "ampeg_release" in g]
        assert notes == [
            "<group> amp_veltrack=73 ampeg_release=1 note_polyphony=2",
            "<group> amp_veltrack=73 ampeg_release=5 note_polyphony=2",
        ]

    def test_pedal_noise_groups_are_untouched(self):
        """Their group numbers drive off_by; the release group must not
        collide with them either."""
        pedal = [g for g in self._groups(patch_salamander(_SALAMANDER))
                 if "on_locc64" in g]
        assert pedal == [g for g in self._groups(_SALAMANDER) if "on_locc64" in g]

    def test_crlf_and_everything_else_survive(self):
        out = patch_salamander(_SALAMANDER)
        assert out.count("\r\n") == _SALAMANDER.count("\r\n")
        assert "\n" not in out.replace("\r\n", "")
        kept = [line for line in _SALAMANDER.split("\r\n")
                if not line.startswith("<group>")]
        assert [line for line in out.split("\r\n")
                if not line.startswith("<group>")] == kept

    def test_an_unexpected_file_is_refused_rather_than_half_patched(self):
        with pytest.raises(PatchError):
            patch_salamander("<group> trigger=release rt_decay=2\r\n")

    def test_patch_writes_beside_the_original_and_leaves_it_alone(self, tmp_path):
        patch = LIBRARIES["salamander"].patch
        source = tmp_path / "salamander" / patch.source
        source.parent.mkdir(parents=True)
        source.write_bytes(_SALAMANDER.encode())
        assert main(["salamander", "--patch", "--root", str(tmp_path)]) == 0
        output = tmp_path / "salamander" / patch.output
        assert output.parent == source.parent
        assert output.read_bytes() == patch_salamander(_SALAMANDER).encode()
        assert source.read_bytes() == _SALAMANDER.encode()

    def test_patch_without_the_library_fails(self, tmp_path, capsys):
        assert main(["salamander", "--patch", "--root", str(tmp_path)]) == 1
        assert "patch failed" in capsys.readouterr().err


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


def _zip(tmp_path, names, links=()):
    import stat
    import zipfile

    path = tmp_path / "a.zip"
    with zipfile.ZipFile(path, "w") as zf:
        for name in names:
            zf.writestr(name, b"x")
        for name, target in links:
            info = zipfile.ZipInfo(name)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            zf.writestr(info, target)
    return path


class TestZip:
    """bandshed.net's libraries are zips; same filtering as tar."""

    def test_unpacks_a_zip(self, tmp_path):
        from synth_ui.tools.install_library import _extract

        archive = _zip(tmp_path, ["Wurt/wurly.sfz", "Wurt/a.wav"])
        _extract(str(archive), str(tmp_path / "out"))
        assert (tmp_path / "out" / "Wurt" / "wurly.sfz").read_bytes() == b"x"

    def test_traversal_and_links_are_skipped(self, tmp_path):
        import zipfile

        from synth_ui.tools.install_library import _safe_zip_names

        archive = _zip(tmp_path, ["../escape.txt", "ok.sfz"],
                       links=[("evil", "/etc/passwd")])
        dest = tmp_path / "out"
        dest.mkdir()
        with zipfile.ZipFile(archive) as zf:
            assert list(_safe_zip_names(zf, str(dest))) == ["ok.sfz"]

    def test_install_reports_mappings_from_a_zip(self, tmp_path, monkeypatch, capsys):
        archive = _zip(tmp_path, ["Clavinet/clavinet.sfz", "Clavinet/a.wav"])
        monkeypatch.setattr(
            "synth_ui.tools.install_library._free_megabytes", lambda _p: 999_999
        )
        monkeypatch.setattr(
            "synth_ui.tools.install_library._download",
            lambda url, dest: os.replace(str(archive), dest) or True,
        )
        assert install("clavinet", str(tmp_path / "root")) == 0
        assert "clavinet.sfz" in capsys.readouterr().out
        assert not (tmp_path / "root" / "clavinet" / "clavinet.zip").exists()
