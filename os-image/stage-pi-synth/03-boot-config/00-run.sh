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

# --- Disable HDMI audio (the vc4hdmi0/1 ALSA cards) ---
# This appliance outputs only through the HiFiBerry DAC and its touchscreen isn't
# HDMI, so the KMS driver's HDMI audio codecs are pure clutter — they appear as
# ALSA playback cards and (before start-jack.sh filtered them) even got picked at
# boot, crashing the audio stack. `,noaudio` on the vc4-kms-v3d overlay stops
# them registering at all. Only touches HDMI *audio*; HDMI display is unaffected.
if grep -qE "^dtoverlay=vc4-kms-v3d" "${CONFIG}"; then
	if ! grep -qE "^dtoverlay=vc4-kms-v3d[^[:space:]]*noaudio" "${CONFIG}"; then
		sed -i -E "s/^(dtoverlay=vc4-kms-v3d[^[:space:]]*)/\1,noaudio/" "${CONFIG}"
		echo "config.txt: disabled HDMI audio (vc4-kms-v3d,noaudio)"
	else
		echo "config.txt: HDMI audio already disabled"
	fi
else
	echo "config.txt: no vc4-kms-v3d overlay — HDMI audio not present to disable"
fi

# --- cm5 only: DSI touchscreen (Waveshare Nano board) ---
# Confirmed on hardware: on this SoC generation (BCM2712's RP1 DSI driver),
# display_auto_detect alone hung/crashed the board with this panel connected
# (required a re-flash to recover — see docs/engine-architecture.md history).
# Disabling auto-detect and specifying the panel's overlay explicitly is what's
# actually proven stable; it's also the common pattern on this generation even
# for Raspberry Pi's own official panels. dtoverlay= is additive, not a
# replacement — this coexists with vc4-kms-v3d (still required as the base KMS
# driver) and hifiberry-dac (audio, unrelated). Not applied for pi4: its
# touchscreen already works fine via plain display_auto_detect=1 (untouched
# above), and forcing this cm5-specific panel overlay onto different hardware
# would be wrong.
if [ "${PI_SYNTH_BOARD:-pi4}" = "cm5" ]; then
	if ! grep -qE "^dtoverlay=vc4-kms-dsi-7inch" "${CONFIG}"; then
		cat >> "${CONFIG}" << 'EOF'

# --- pi-synth: cm5 DSI touchscreen ---
display_auto_detect=0
dtoverlay=vc4-kms-dsi-7inch,dsi0
EOF
		echo "config.txt: added cm5 DSI touchscreen overlay, disabled auto-detect"
	else
		echo "config.txt: cm5 DSI touchscreen overlay already present"
	fi
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

# Move the Linux console off the display (tty1 -> tty3): kernel + systemd boot
# text no longer scrolls on the touchscreen. The login prompt is removed
# separately by masking getty@tty1 (06-system-tuning). Idempotent.
sed -i "s/\bconsole=tty1\b/console=tty3/" "${CMDLINE}"
