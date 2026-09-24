"""Tests for reading this box's own address. No hardware."""

from synth_ui.clients.network import (
    Interface,
    enable_wifi,
    ensure_wifi_on,
    interfaces,
    parse_interfaces,
    set_wifi,
    wifi_enabled,
)

IP_BR = """lo               UNKNOWN        127.0.0.1/8
wlan0            UP             192.168.1.148/24
"""


def test_reads_interface_state_and_address():
    found = parse_interfaces(IP_BR)
    assert found == [Interface(name="wlan0", state="UP", address="192.168.1.148")]
    assert found[0].usable is True


def test_loopback_is_dropped():
    """It answers "is this box on a network" with a misleading yes."""
    assert "lo" not in [i.name for i in parse_interfaces(IP_BR)]


def test_an_interface_with_no_address_is_kept_but_not_usable():
    """Associated-but-no-lease is a different problem from not associated, and
    telling them apart is the whole point of showing this."""
    found = parse_interfaces("wlan0            DOWN\n")
    assert found == [Interface(name="wlan0", state="DOWN", address="")]
    assert found[0].usable is False


def test_an_up_interface_without_a_lease_is_not_usable():
    found = parse_interfaces("wlan0            UP\n")
    assert found[0].usable is False


def test_unreadable_is_none_and_no_interfaces_is_empty():
    """Two different claims: "I cannot tell" and "there is nothing here". The
    screen says something different for each, because the seconds after the
    radio comes back look like the second one."""
    assert interfaces(runner=lambda cmd: (1, "")) is None
    assert interfaces(runner=lambda cmd: (0, "lo  UNKNOWN  127.0.0.1/8\n")) == []


def test_runs_ip_brief_and_ipv4_only():
    seen = []
    interfaces(runner=lambda cmd: (seen.append(cmd) or (0, IP_BR)))
    assert seen == [["ip", "-4", "-br", "addr"]]


# --- the radio ---------------------------------------------------------------

def test_reads_the_radio_state():
    assert wifi_enabled(runner=lambda c: (0, "enabled\n")) is True
    assert wifi_enabled(runner=lambda c: (0, "disabled\n")) is False


def test_an_unreadable_radio_is_unknown_not_off():
    """Reporting "off" for a state we could not read would invite switching on
    a radio that was never off, or worse, off one that is the only way in."""
    assert wifi_enabled(runner=lambda c: (1, "")) is None
    assert wifi_enabled(runner=lambda c: (0, "something else")) is None


def test_setting_the_radio_uses_nmcli_not_rfkill_or_the_supplicant():
    """rfkill isn't installed here, and stopping wpa_supplicant takes WiFi SSH
    down in a way the UI cannot undo."""
    seen = []
    set_wifi(False, runner=lambda c: (seen.append(c) or (0, "")))
    assert seen == [["sudo", "nmcli", "radio", "wifi", "off"]]
    seen.clear()
    set_wifi(True, runner=lambda c: (seen.append(c) or (0, "")))
    assert seen == [["sudo", "nmcli", "radio", "wifi", "on"]]


def test_startup_switches_a_remembered_off_back_on():
    # NetworkManager persists the radio state, and this box has no Ethernet.
    seen = []

    def runner(cmd):
        seen.append(cmd)
        return (0, "disabled\n") if cmd[1] == "-t" else (0, "")

    ensure_wifi_on(runner=runner)
    assert ["sudo", "nmcli", "radio", "wifi", "on"] in seen


def test_startup_leaves_a_radio_that_is_already_on_alone():
    seen = []

    def runner(cmd):
        seen.append(cmd)
        return (0, "enabled\n")

    ensure_wifi_on(runner=runner)
    assert all("sudo" not in c for c in seen)


# --- switching it back on, which is the half that failed --------------------

DOWN = "wlan0            DOWN\n"
UP = "wlan0            UP             192.168.1.148/24\n"
DEVICES = "wlan0:wifi\nlo:loopback\n"


def scripted(*, ip_sequence):
    """A runner whose `ip` output walks a script, so a radio that comes back
    slowly can be played out without sleeping."""
    seen = []
    state = {"i": 0}

    def runner(cmd):
        seen.append(cmd)
        if cmd[0] == "ip":
            out = ip_sequence[min(state["i"], len(ip_sequence) - 1)]
            state["i"] += 1
            return 0, out
        if cmd[:2] == ["nmcli", "-t"] and "DEVICE,TYPE" in cmd:
            return 0, DEVICES
        return 0, ""

    return runner, seen


def test_enabling_waits_for_an_address_not_just_the_radio():
    """The bug: `nmcli radio wifi on` returns before the interface is back, so
    reporting its success said "reachable" when the box was not."""
    runner, seen = scripted(ip_sequence=["", DOWN, UP])
    assert enable_wifi(runner=runner, sleep=lambda s: None) is True
    assert ["sudo", "nmcli", "radio", "wifi", "on"] in seen


def test_enabling_nudges_the_device_while_it_is_not_carrying_an_address():
    runner, seen = scripted(ip_sequence=[DOWN, UP])
    enable_wifi(runner=runner, sleep=lambda s: None)
    assert ["sudo", "nmcli", "device", "connect", "wlan0"] in seen


def test_the_device_name_is_asked_for_not_assumed():
    runner, seen = scripted(ip_sequence=[DOWN, UP])
    enable_wifi(runner=runner, sleep=lambda s: None)
    assert ["nmcli", "-t", "-f", "DEVICE,TYPE", "device"] in seen


def test_enabling_reports_failure_when_it_never_comes_back():
    """The caller has to be able to say so: a power cycle is the way back, and
    the player needs telling rather than a screen that looks fine."""
    runner, _ = scripted(ip_sequence=[DOWN])
    assert enable_wifi(runner=runner, sleep=lambda s: None, timeout=6.0) is False


def test_enabling_gives_up_if_the_radio_itself_refuses():
    def runner(cmd):
        return (1, "") if "radio" in cmd else (0, "")

    assert enable_wifi(runner=runner, sleep=lambda s: None) is False
