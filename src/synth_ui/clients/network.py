"""What address this box is on, for the settings screen.

The instrument has no other way to say. It is reached over WiFi and nothing
else — the carrier board has no Ethernet — so when the association drops there
is no console, no lease and no way in, and the only diagnosis available is a
subnet scan from another machine. Showing the interface and its address turns
that into a glance.

Deliberately a text scan of `ip -4 -br addr` rather than a dependency: the
parsing is trivial, the tool is in the base image, and the alternative (netifaces
or pyroute2) is a package on the RT audio box for one read.
"""

from __future__ import annotations

import logging
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Returns (returncode, stdout). Injectable so tests never shell out.
Runner = Callable[[list[str]], tuple[int, str]]


@dataclass
class Interface:
    name: str
    state: str          # "UP" | "DOWN" | "UNKNOWN", as the kernel reports it
    address: str        # "192.168.1.148", or "" when it has none

    @property
    def usable(self) -> bool:
        return self.state == "UP" and bool(self.address)


def _subprocess_runner(cmd: list[str]) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        return p.returncode, p.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return 1, ""


def parse_interfaces(text: str) -> list[Interface]:
    """Parse `ip -4 -br addr`:

        lo               UNKNOWN        127.0.0.1/8
        wlan0            UP             192.168.1.148/24

    Loopback is dropped — it answers "is the box on a network" with a
    misleading yes. An interface that is up but has no address is kept, because
    "associated but no lease" is a different problem from "not associated" and
    the difference is the whole point of showing this.
    """
    interfaces: list[Interface] = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 2 or fields[0] == "lo":
            continue
        address = fields[2].split("/")[0] if len(fields) > 2 else ""
        interfaces.append(Interface(name=fields[0], state=fields[1], address=address))
    return interfaces


def wifi_enabled(runner: Runner | None = None) -> bool | None:
    """Whether the radio is on. None if NetworkManager can't be asked, which the
    caller shows as unknown rather than guessing a state it might act on."""
    run = runner or _subprocess_runner
    code, out = run(["nmcli", "-t", "radio", "wifi"])
    if code != 0:
        return None
    answer = out.strip().lower()
    return True if answer == "enabled" else False if answer == "disabled" else None


def set_wifi(enabled: bool, runner: Runner | None = None) -> bool:
    """Switch the radio on or off.

    `nmcli radio`, not `rfkill` (not installed here) and emphatically not
    stopping `wpa_supplicant`, which NetworkManager drives and which takes WiFi
    SSH down with it in a way the UI can't undo.

    **Off never survives a reboot.** NetworkManager persists this in
    `/var/lib/NetworkManager/NetworkManager.state`, so `ensure_wifi_on()` undoes
    that at startup. This box has no Ethernet: the one time SSH matters most is
    when the UI won't start, and that is exactly when a remembered "off" would
    leave no way in at all.
    """
    run = runner or _subprocess_runner
    code, _ = run(["sudo", "nmcli", "radio", "wifi", "on" if enabled else "off"])
    return code == 0


def wifi_device(runner: Runner | None = None) -> str:
    """What NetworkManager calls the WiFi device. Asked rather than assumed to
    be `wlan0`, for the same reason the DAC is addressed by name."""
    run = runner or _subprocess_runner
    code, out = run(["nmcli", "-t", "-f", "DEVICE,TYPE", "device"])
    if code != 0:
        return ""
    for line in out.splitlines():
        fields = line.split(":")
        if len(fields) >= 2 and fields[1] == "wifi":
            return fields[0]
    return ""


def enable_wifi(
    runner: Runner | None = None,
    sleep: Callable[[float], None] = time.sleep,
    timeout: float = 40.0,
) -> bool:
    """Switch the radio on and wait until the box is actually reachable again.

    **The radio being on is not the same as the network being back**, and
    treating them as the same is what made this toggle unsafe. `nmcli radio wifi
    on` returns as soon as the block is lifted — before the netdev has
    re-registered, and well before the saved connection has been re-activated
    and a lease taken. Switching a box with no Ethernet off that way, then
    reporting success, leaves it unreachable and says it is fine.

    So this waits for an interface with an address, and nudges NetworkManager
    to activate the device if it is present but idle. Returns False on timeout,
    which the caller must show: a power cycle is then the way back, and the
    player needs to be told that rather than left guessing.

    Blocking, by design — the caller runs it off the UI thread.
    """
    run = runner or _subprocess_runner
    if not set_wifi(True, runner):
        return False

    waited = 0.0
    while waited < timeout:
        found = interfaces(runner) or []
        if any(i.usable for i in found):
            return True
        device = wifi_device(runner)
        if device:
            # Present but not carrying an address: ask for the saved connection.
            # Harmless when it is already connecting.
            run(["sudo", "nmcli", "device", "connect", device])
        sleep(2.0)
        waited += 2.0
    logger.error("wifi did not come back within %.0fs", timeout)
    return False


def ensure_wifi_on(runner: Runner | None = None) -> None:
    """Undo a persisted "off" at startup. See set_wifi."""
    if wifi_enabled(runner) is False:
        logger.info("wifi was left off; switching it back on for this session")
        set_wifi(True, runner)


def interfaces(runner: Runner | None = None) -> list[Interface] | None:
    """Every non-loopback IPv4 interface.

    None when `ip` could not be run at all, and an empty list when it ran and
    found nothing. Those are different claims — "I cannot tell" versus "there is
    no interface here" — and collapsing them into one made the screen report a
    radio that was still coming up as a failure to read anything.
    """
    run = runner or _subprocess_runner
    code, out = run(["ip", "-4", "-br", "addr"])
    if code != 0:
        logger.warning("could not read network interfaces")
        return None
    return parse_interfaces(out)
