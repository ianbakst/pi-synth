"""Tests for picking the DRM card the panel is on.

The bug this guards against was live on hardware: the card index was pinned to 0
in a systemd drop-in, probe order shifted, and 0 became the GPU — which has no
connectors, so `set_mode` failed with a misleading recycled SDL error
("Failed to get evdev touchscreen name") that sent the investigation at the
touchscreen and the boot config instead. The properties that matter are that a
real DSI link resolves to its own card whatever number it drew, and that a box
without one still comes up.
"""

import os

from synth_ui.clients.display_device import (
    SDL_DEVICE_INDEX_VAR,
    dsi_card_index,
    select_kmsdrm_device,
)


def by_path(tmp_path, **links):
    """Build a fake /dev/dri/by-path. `links` maps node name -> card target."""
    for name, target in links.items():
        (tmp_path / name).symlink_to(f"../{target}")
    return str(tmp_path)


# The real layout on a CM5 with HDMI disabled: the panel is card1, and card0 is
# the GPU that the old hardcoded index pointed at.
CM5 = {
    "platform-1002000000.v3d-card": "card0",
    "platform-1f00130000.dsi-card": "card1",
    "platform-axi:gpu-card": "card2",
}


class TestResolving:
    def test_finds_the_dsi_card_among_others(self, tmp_path):
        assert dsi_card_index(by_path(tmp_path, **CM5)) == 1

    def test_index_is_read_from_the_link_not_assumed(self, tmp_path):
        """The whole point: whatever number the DSI card drew, we follow it."""
        dir_ = by_path(tmp_path, **{"platform-1f00130000.dsi-card": "card3"})
        assert dsi_card_index(dir_) == 3

    def test_address_in_the_node_name_does_not_matter(self, tmp_path):
        """It differs by SoC revision; only the .dsi-card suffix is stable."""
        dir_ = by_path(tmp_path, **{"platform-deadbeef.dsi-card": "card1"})
        assert dsi_card_index(dir_) == 1

    def test_render_node_is_not_mistaken_for_a_card(self, tmp_path):
        dir_ = by_path(
            tmp_path,
            **{
                "platform-1002000000.v3d-render": "renderD128",
                "platform-1f00130000.dsi-card": "card1",
            },
        )
        assert dsi_card_index(dir_) == 1


class TestMissingHardware:
    def test_no_dsi_card_is_not_an_error(self, tmp_path):
        """A dev machine has none. The UI still has to start."""
        assert dsi_card_index(str(tmp_path)) is None

    def test_missing_directory_is_not_an_error(self, tmp_path):
        assert dsi_card_index(str(tmp_path / "nope")) is None

    def test_dangling_link_is_not_an_error(self, tmp_path):
        (tmp_path / "platform-1f00130000.dsi-card").symlink_to("../card9")
        # Broken symlinks still readlink fine; the name is what we parse.
        assert dsi_card_index(str(tmp_path)) == 9


class TestEnvironment:
    def test_sets_the_sdl_variable(self, tmp_path):
        env: dict[str, str] = {}
        assert select_kmsdrm_device(env, by_path(tmp_path, **CM5)) == 1
        assert env[SDL_DEVICE_INDEX_VAR] == "1"

    def test_leaves_sdl_to_auto_scan_when_there_is_no_dsi(self, tmp_path):
        env: dict[str, str] = {}
        assert select_kmsdrm_device(env, str(tmp_path)) is None
        assert SDL_DEVICE_INDEX_VAR not in env

    def test_an_explicit_override_wins(self, tmp_path):
        """So you can still bring the UI up on an HDMI monitor to debug."""
        env = {SDL_DEVICE_INDEX_VAR: "2"}
        assert select_kmsdrm_device(env, by_path(tmp_path, **CM5)) == 2
        assert env[SDL_DEVICE_INDEX_VAR] == "2"

    def test_defaults_to_the_process_environment(self, tmp_path, monkeypatch):
        monkeypatch.delenv(SDL_DEVICE_INDEX_VAR, raising=False)
        select_kmsdrm_device(by_path_dir=by_path(tmp_path, **CM5))
        assert os.environ[SDL_DEVICE_INDEX_VAR] == "1"
