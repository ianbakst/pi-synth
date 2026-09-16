"""Tests for screen brightness.

Two properties matter and neither is obvious from reading the class: it must be
a harmless no-op where no backlight exists (dev machines, HDMI builds), and it
must never let the panel go fully dark — a black screen hides the control you
would need to undo it.
"""

from synth_ui.clients.backlight import MINIMUM_FRACTION, Backlight
from synth_ui.ui.app import _load_brightness, _save_brightness


def panel(tmp_path, maximum="255", current="255", name="0-0045"):
    device = tmp_path / name
    device.mkdir()
    (device / "max_brightness").write_text(maximum + "\n")
    (device / "brightness").write_text(current + "\n")
    return Backlight(class_dir=str(tmp_path))


class TestMissingHardware:
    def test_no_device_is_not_an_error(self, tmp_path):
        b = Backlight(class_dir=str(tmp_path))
        assert not b.available

    def test_setting_brightness_without_hardware_is_a_no_op(self, tmp_path):
        assert Backlight(class_dir=str(tmp_path)).set(128) is False

    def test_fraction_reads_full_without_hardware(self, tmp_path):
        """So a UI built against it doesn't show a dimmed screen that isn't."""
        assert Backlight(class_dir=str(tmp_path)).fraction() == 1.0

    def test_unreadable_max_brightness_disables_it(self, tmp_path):
        (tmp_path / "0-0045").mkdir()
        assert not Backlight(class_dir=str(tmp_path)).available


class TestReadWrite:
    def test_reads_the_panel_range(self, tmp_path):
        b = panel(tmp_path, maximum="255")
        assert b.available and b.maximum == 255

    def test_set_writes_the_value(self, tmp_path):
        b = panel(tmp_path)
        assert b.set(120)
        assert b.get() == 120

    def test_set_fraction_scales_to_the_range(self, tmp_path):
        b = panel(tmp_path, maximum="100")
        b.set_fraction(0.5)
        assert b.get() == 50

    def test_a_smaller_range_is_honoured(self, tmp_path):
        """Not every panel is 0..255; writing 255 to a 31-max device must clamp."""
        b = panel(tmp_path, maximum="31")
        b.set(255)
        assert b.get() == 31


class TestNeverGoesDark:
    def test_zero_is_clamped_to_the_floor(self, tmp_path):
        b = panel(tmp_path, maximum="255")
        b.set(0)
        assert b.get() == b.minimum > 0

    def test_the_floor_is_a_fraction_of_the_range(self, tmp_path):
        b = panel(tmp_path, maximum="255")
        assert b.minimum == int(255 * MINIMUM_FRACTION)

    def test_the_floor_is_at_least_one_on_a_tiny_range(self, tmp_path):
        b = panel(tmp_path, maximum="5")
        assert b.minimum >= 1

    def test_set_fraction_zero_also_clamps(self, tmp_path):
        b = panel(tmp_path, maximum="255")
        b.set_fraction(0.0)
        assert b.get() == b.minimum


class TestPersistence:
    def test_round_trip(self, tmp_path, monkeypatch):
        path = str(tmp_path / "b")
        monkeypatch.setattr("synth_ui.ui.app.BRIGHTNESS_FILE", path)
        _save_brightness(0.42)
        assert abs(_load_brightness() - 0.42) < 0.001

    def test_missing_file_is_full_brightness(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "synth_ui.ui.app.BRIGHTNESS_FILE", str(tmp_path / "absent")
        )
        assert _load_brightness() == 1.0

    def test_corrupt_file_is_full_brightness(self, tmp_path, monkeypatch):
        path = tmp_path / "b"
        path.write_text("not a number")
        monkeypatch.setattr("synth_ui.ui.app.BRIGHTNESS_FILE", str(path))
        assert _load_brightness() == 1.0

    def test_out_of_range_file_is_full_brightness(self, tmp_path, monkeypatch):
        """A saved 0.0 would otherwise boot the instrument to a dark screen."""
        path = tmp_path / "b"
        path.write_text("-3")
        monkeypatch.setattr("synth_ui.ui.app.BRIGHTNESS_FILE", str(path))
        assert _load_brightness() == 1.0
