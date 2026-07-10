#!/bin/bash -e
#
# Install and enable the systemd units. Unit files are the single source of
# truth in ../../systemd (copied from the repo, not duplicated here).
#
# Always-on at boot:  cpu-performance, jack, a2jmidid, mod-host, synth-ui
# On-demand (started by the UI via engine-manager.sh, NOT enabled):
#                     fluidsynth-engine, setbfree

for unit in cpu-performance jack a2jmidid mod-host fluidsynth-engine setbfree synth-ui; do
	install -m 644 "${PI_SYNTH_SRC}/systemd/${unit}.service" \
		"${ROOTFS_DIR}/etc/systemd/system/${unit}.service"
done

on_chroot << 'EOF'
set -e
# Stock fluidsynth.service grabs port 9800 / the audio device — keep it out.
systemctl mask fluidsynth.service 2>/dev/null || true

systemctl enable cpu-performance.service
systemctl enable jack.service
systemctl enable a2jmidid.service
systemctl enable mod-host.service
systemctl enable synth-ui.service
EOF
