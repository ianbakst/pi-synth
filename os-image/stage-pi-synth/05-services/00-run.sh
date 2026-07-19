#!/bin/bash -e
#
# Install and enable the systemd units. Unit files are the single source of
# truth in ../../systemd (copied from the repo, not duplicated here).
#
# Always-on at boot:  cpu-performance, jack, a2jmidid, synth-ui
# On-demand (started by the UI / EngineManager via systemctl, NOT enabled):
#                     fluidsynth-engine, setbfree, mod-host
#   mod-host used to be always-on, but hardware validation found it sitting on
#   core 2 alongside another active instrument engine (e.g. fluidsynth) causes
#   continuous JACK XRuns even fully idle — two RT clients contending for one
#   isolated core. EngineManager now starts/stops it exactly like the other
#   instrument engines (see ModHostEngine in src/synth_ui/clients/engine.py).
# Installed but NOT enabled (Phase 3, pending hardware confirmation of its JACK
# client name):  mod-host-fx
for unit in cpu-performance jack a2jmidid mod-host mod-host-fx \
            fluidsynth-engine setbfree synth-ui; do
	install -m 644 "${PI_SYNTH_SRC}/systemd/${unit}.service" \
		"${ROOTFS_DIR}/etc/systemd/system/${unit}.service"
done

on_chroot << 'EOF'
set -e
# Stock fluidsynth.service (Debian's packaged service) grabs port 9800 and the
# audio device, which blocks our fluidsynth-engine from binding its shell server
# (EADDRINUSE) and starves the DAC. Pi OS ships it as BOTH a system unit and a
# per-user unit, so mask both — masking only the system one lets the user unit
# respawn and squat 9800.
systemctl mask fluidsynth.service 2>/dev/null || true
systemctl --global mask fluidsynth.service 2>/dev/null || true

systemctl enable cpu-performance.service
systemctl enable jack.service
systemctl enable a2jmidid.service
systemctl enable synth-ui.service
EOF
