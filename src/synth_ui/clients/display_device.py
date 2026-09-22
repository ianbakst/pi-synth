"""Which DRM device the UI draws to — resolved by name, never by index.

On BCM2712 (CM5) the DSI panel and the GPU are *separate* DRM devices, unlike
the Pi 4's single unified one. A CM5 with HDMI disabled enumerates three cards:

    card0 -> v3d           the GPU. Renders, but has no connectors.
    card1 -> drm-rp1-dsi   the panel. This is the one we want.
    card2 -> vc4-drm       display engine; only Writeback connectors left.

SDL2's KMSDRM backend picks a card by *index* (`SDL_KMSDRM_DEVICE_INDEX`), and
that index is probe order — it is not stable. This board shipped with the index
pinned to 0, which was the panel at the time and is the GPU now; the UI died at
`set_mode` with a card that has nothing to display on.

`/dev/dri/by-path` gives the stable name the index doesn't:

    platform-1f00130000.dsi-card -> ../card1

so we resolve through that and hand SDL the index it insists on. Same principle
as the audio device (CLAUDE.md rule 4): the DAC is `hw:sndrpihifiberry`, never
`hw:1`.

When there is no DSI card — a dev machine, an HDMI build — this does nothing and
leaves SDL to auto-scan for a card with a connected connector. That scan is
correct on a box whose only output is the panel; it is a coin-flip on one with
HDMI attached, which is exactly why we pin it when we can.

The hex address in the node name is the RP1's DSI block and differs by SoC
revision. The `.dsi-card` suffix is the part that holds still, so that is what
we match.
"""

from __future__ import annotations

import glob
import logging
import os

logger = logging.getLogger(__name__)

_BY_PATH_DIR = "/dev/dri/by-path"
_DSI_SUFFIX = ".dsi-card"

SDL_DEVICE_INDEX_VAR = "SDL_KMSDRM_DEVICE_INDEX"


def dsi_card_index(by_path_dir: str = _BY_PATH_DIR) -> int | None:
    """The `cardN` index of the DSI panel, or None if this box has no DSI."""
    links = sorted(glob.glob(os.path.join(by_path_dir, f"*{_DSI_SUFFIX}")))
    if not links:
        return None
    if len(links) > 1:
        # No board here has two DSI panels. Take the lowest-addressed one and
        # say so, rather than picking silently.
        logger.warning("multiple DSI cards %s; using %s", links, links[0])

    try:
        target = os.path.basename(os.readlink(links[0]))
    except OSError as exc:
        logger.warning("could not resolve %s: %s", links[0], exc)
        return None

    if not target.startswith("card"):
        logger.warning("%s points at %s, not a card node", links[0], target)
        return None
    try:
        return int(target[len("card") :])
    except ValueError:
        logger.warning("unparseable card node %s", target)
        return None


def select_kmsdrm_device(
    env: dict[str, str] | None = None,
    by_path_dir: str = _BY_PATH_DIR,
) -> int | None:
    """Point SDL at the DSI card. Call before `pygame.init()`.

    Returns the index chosen, or None if it left SDL to its own devices. An
    index already set in the environment wins — that is the override for
    bringing the UI up on an HDMI monitor.
    """
    env = os.environ if env is None else env

    preset = env.get(SDL_DEVICE_INDEX_VAR)
    if preset:
        logger.info("%s=%s set externally; leaving it", SDL_DEVICE_INDEX_VAR, preset)
        try:
            return int(preset)
        except ValueError:
            return None

    index = dsi_card_index(by_path_dir)
    if index is None:
        logger.info("no DSI card in %s; letting SDL auto-scan", by_path_dir)
        return None

    env[SDL_DEVICE_INDEX_VAR] = str(index)
    logger.info("drawing to DSI card%d", index)
    return index
