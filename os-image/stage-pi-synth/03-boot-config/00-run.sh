#!/bin/bash -e
#
# Boot configuration: I2S DAC overlay, CPU isolation, and a quiet appliance boot.
# Edits the FAT-partition files in the target rootfs directly (host-side).

CONFIG="${ROOTFS_DIR}/boot/firmware/config.txt"
CMDLINE="${ROOTFS_DIR}/boot/firmware/cmdline.txt"

# --- config.txt: DAC + disable onboard audio/BT/camera, blank rainbow splash ---
if ! grep -q "^# --- pi-synth ---" "${CONFIG}"; then
	cat >> "${CONFIG}" << 'EOF'

# --- pi-synth ---
dtparam=i2s=on
dtoverlay=hifiberry-dac
camera_auto_detect=0
dtoverlay=disable-bt
dtparam=audio=off
disable_splash=1
EOF
	echo "config.txt: appended pi-synth block"
else
	echo "config.txt: pi-synth block already present"
fi

# --- cmdline.txt: isolate cores 2,3 for JACK/engines + quiet boot ---
# Single-line file; append our args once if not already there.
# Isolate cores 1,2,3 for audio: core 1 = JACK, core 2 = instrument engine,
# core 3 = effects (reserved). Core 0 is left for the OS + UI. See
# docs/engine-architecture.md.
ISOL="isolcpus=1,2,3 nohz_full=1,2,3 rcu_nocbs=1,2,3"
QUIET="quiet loglevel=3 vt.global_cursor_default=0 logo.nologo"
if ! grep -q "isolcpus=1,2,3" "${CMDLINE}"; then
	sed -i "s|\$| ${ISOL} ${QUIET}|" "${CMDLINE}"
	echo "cmdline.txt: appended CPU isolation + quiet-boot args"
else
	echo "cmdline.txt: CPU isolation already present"
fi
