#!/bin/bash -e
#
# Install the pi-synth application into the image.
#   - vendor the repo source to /home/synth/synth (matches synth-ui.service's
#     WorkingDirectory=/home/synth/synth/src)
#   - create the soundfont/instrument dirs and a default.sf2
#   - install the engine-switching sudoers rule
#
# Only runtime dependency is pygame, provided by apt (python3-pygame) in
# 00-base-packages — no venv/pip needed.

APP_DEST="${ROOTFS_DIR}/home/synth/synth"

echo "Vendoring app source -> /home/synth/synth"
mkdir -p "${APP_DEST}"
rsync -a --delete \
	--exclude '.git' \
	--exclude '.venv' \
	--exclude 'os-image' \
	--exclude 'hardware' \
	--exclude '.claude' \
	--exclude '__pycache__' \
	--exclude '*.pyc' \
	--exclude '.pytest_cache' \
	--exclude '.ruff_cache' \
	--exclude 'soundfonts/*.sf2' \
	--exclude 'soundfonts/*.sf3' \
	"${PI_SYNTH_SRC}/" "${APP_DEST}/"

# Soundfont + instrument directories and the voices manifest.
mkdir -p "${ROOTFS_DIR}/home/synth/soundfonts" "${ROOTFS_DIR}/home/synth/instruments"
if [ -f "${PI_SYNTH_SRC}/instruments/voices.json" ]; then
	install -m 644 "${PI_SYNTH_SRC}/instruments/voices.json" \
		"${ROOTFS_DIR}/home/synth/instruments/voices.json"
fi

# Engine-switching sudoers rule (validated below).
install -m 0440 files/sudoers-synth-engine "${ROOTFS_DIR}/etc/sudoers.d/synth-engine"

on_chroot << 'EOF'
set -e
# Default soundfont for the FluidSynth fallback engine: link to the GM font from
# fluid-soundfont-gm (installed in 00-base-packages).
if [ ! -e /home/synth/soundfonts/default.sf2 ] \
	&& [ -f /usr/share/sounds/sf2/FluidR3_GM.sf2 ]; then
	ln -sf /usr/share/sounds/sf2/FluidR3_GM.sf2 /home/synth/soundfonts/default.sf2
fi

# Everything under /home/synth belongs to the synth user.
chown -R synth:synth /home/synth

# Fail the build loudly if the sudoers rule is malformed.
visudo -cf /etc/sudoers.d/synth-engine
EOF
