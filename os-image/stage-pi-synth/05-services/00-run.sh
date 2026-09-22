#!/bin/bash -e
#
# Install and enable the systemd units. Unit files are the single source of
# truth in ../../systemd (copied from the repo, not duplicated here).
#
# Always-on at boot:  cpu-performance, jack, a2jmidid, ttymidi, mod-host, synth-ui
# On-demand (started by the UI / EngineManager via systemctl, NOT enabled):
#                     none at present. ProcessEngine remains for a future
#                     Pianoteq; soundfonts are played by Fluida inside mod-host.
#   mod-host is always-on again: it hosts the master chain (trim + limiter) that
#   every voice and every effect feeds through, so it can't be tied to whether a
#   mod-host *instrument* happens to be active. See systemd/mod-host.service for
#   the pi4 xrun history this reverses, and docs/voice-library.md.
#   ttymidi is the DIN-MIDI counterpart to a2jmidid (USB): always-on, but its
#   ConditionPathExists=/dev/ttyAMA0 makes it inert on a board with no UART MIDI.
for unit in cpu-performance jack a2jmidid ttymidi mod-host synth-ui; do
	install -m 644 "${PI_SYNTH_SRC}/systemd/${unit}.service" \
		"${ROOTFS_DIR}/etc/systemd/system/${unit}.service"
done

# --- DRM card selection is NOT done here; see clients/display_device.py ---
# On BCM2712/RP1 the DSI panel and the GPU are separate DRM devices (drm-rp1-dsi
# vs v3d vs vc4-drm), unlike pi4's single unified one, and SDL picks between them
# by a probe-order index. This used to ship a drop-in pinning
# SDL_KMSDRM_DEVICE_INDEX=0, which was the panel when it was written and became
# the GPU later -- a card with no connectors, so the UI crash-looped in
# set_mode(). The UI now resolves the index from /dev/dri/by-path at startup.
# Do not reintroduce a hardcoded index here; a stale one is worse than none,
# because SDL's own auto-scan would at least have found the panel.
on_chroot << 'EOF'
set -e
# Stock fluidsynth.service (Debian's packaged service) grabs the audio device.
# We no longer run fluidsynth ourselves — Fluida plays soundfonts inside
# mod-host — but the package is still installed for libfluidsynth, so Debian's
# unit can still appear and take the DAC out from under JACK
# (EADDRINUSE) and starves the DAC. Pi OS ships it as BOTH a system unit and a
# per-user unit, so mask both — masking only the system one lets the user unit
# respawn and squat 9800.
systemctl mask fluidsynth.service 2>/dev/null || true
systemctl --global mask fluidsynth.service 2>/dev/null || true

systemctl enable cpu-performance.service
systemctl enable jack.service
systemctl enable a2jmidid.service
systemctl enable ttymidi.service
systemctl enable mod-host.service
systemctl enable synth-ui.service
EOF
