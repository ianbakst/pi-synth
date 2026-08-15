"""Tests for AudioDevices — driven by canned `aplay -l` output, no ALSA needed."""

from synth_ui.clients.audio_devices import AudioDevices, Card

TWO_CARDS = """\
**** List of PLAYBACK Hardware Devices ****
card 0: sndrpihifiberry [snd_rpi_hifiberry_dac], device 0: HifiBerry DAC
  Subdevices: 1/1
  Subdevice #0: subdevice #0
card 1: Headphones [bcm2835 Headphones], device 0: Headphones
  Subdevices: 8/8
  Subdevice #0: subdevice #0
"""

# A USB interface only (no HiFiBerry) — e.g. HiFiBerry unplugged / renamed.
USB_ONLY = """\
**** List of PLAYBACK Hardware Devices ****
card 2: USB [Scarlett 2i2 USB], device 0: USB Audio [USB Audio]
  Subdevices: 1/1
  Subdevice #0: subdevice #0
"""

NO_CARDS = ""  # aplay prints "no soundcards found" to stderr; stdout empty


def dev(text):
    return AudioDevices(reader=lambda: text)


def test_list_cards_parses_id_name_index():
    cards = dev(TWO_CARDS).list_cards()
    assert cards == [
        Card(id="sndrpihifiberry", name="snd_rpi_hifiberry_dac", index=0),
        Card(id="Headphones", name="bcm2835 Headphones", index=1),
    ]
    assert cards[0].device == "hw:sndrpihifiberry"


def test_list_cards_dedupes_by_id():
    # Same card listed twice (two devices) -> one Card.
    text = TWO_CARDS + (
        "card 0: sndrpihifiberry [snd_rpi_hifiberry_dac], device 1: x\n"
    )
    ids = [c.id for c in dev(text).list_cards()]
    assert ids == ["sndrpihifiberry", "Headphones"]


def test_resolve_prefers_valid_saved_choice():
    assert dev(TWO_CARDS).resolve(saved="Headphones") == "Headphones"


def test_resolve_falls_back_to_hifiberry_when_saved_missing():
    # Saved card isn't present -> prefer the HiFiBerry.
    assert dev(TWO_CARDS).resolve(saved="Scarlett") == "sndrpihifiberry"
    assert dev(TWO_CARDS).resolve(saved=None) == "sndrpihifiberry"


def test_resolve_falls_back_to_first_card_when_no_hifiberry():
    assert dev(USB_ONLY).resolve(saved="gone") == "USB"


def test_resolve_none_when_no_hardware():
    assert dev(NO_CARDS).list_cards() == []
    assert dev(NO_CARDS).resolve(saved="anything") is None
