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

# --- UART0 on GPIOs 14/15: the 5-pin DIN MIDI IN jack ---
# Only the RX leg (GPIO15) is wired to a jack; GPIO14 stays TXD0 and unused.
#
# cm5 only. On BCM2712, uart0 is disabled by default and Bluetooth lives on its
# own uart (`uarta` in bcm2712-rpi-cm5.dtsi), so the `dtoverlay=disable-bt` above
# does nothing for these pins — that overlay only frees GPIO14/15 on pre-Pi-5
# boards, where BT squats on the PL011. `dtparam=uart0=on` is what actually
# enables the node here (see the `uart0 = <&uart0>, "status"` param in
# bcm2712-rpi.dtsi). pi4 needs nothing: disable-bt already sets uart0 "okay" and
# pins it to uart0_pins.
#
# Note we deliberately do NOT load dtoverlay=midi-uart0-pi5. That overlay exists
# because 31250 baud isn't a standard termios speed, and it fakes the UART clock
# by 38400/31250 so a *requested* 38400 comes out as 31250. ttymidi.service
# instead asks for a true 31250 via BOTHER/TCSETS2, which the RP1 UART clock
# divides exactly. Loading both would skew the rate — if the overlay is ever
# added, ttymidi must switch to `-b 38400`.
if [ "${PI_SYNTH_BOARD:-pi4}" = "cm5" ]; then
	if ! grep -qE "^dtparam=uart0=on" "${CONFIG}"; then
		cat >> "${CONFIG}" << 'EOF'

# --- pi-synth: cm5 UART MIDI IN (GPIO15/RXD0) ---
dtparam=uart0=on
EOF
		echo "config.txt: enabled uart0 on GPIOs 14/15 for DIN MIDI"
	else
		echo "config.txt: uart0 already enabled"
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

# Take the serial console off the MIDI pins. pi-gen's base cmdline (stage1/
# 00-boot-files/files/cmdline.txt) ships console=serial0,115200, and serial0 is
# the very UART the DIN MIDI jack feeds: `serial0 = &uart0` in bcm2712-rpi.dtsi
# (cm5), and disable-bt re-aliases serial0 to the PL011 on GPIO14/15 (pi4). Left
# in place, the kernel dumps boot text at 115200 onto the port and systemd's
# getty-generator spawns a login prompt on it from this very argument — both
# fighting the incoming MIDI stream. Dropping console= is therefore what removes
# the getty too; no serial-getty mask is needed. The console stays reachable over
# SSH and the touchscreen (cm5 also keeps its own debug UART, serial10/ttyAMA10,
# on the dedicated connector, which this doesn't touch). Idempotent.
sed -i "s/[[:space:]]*\bconsole=serial0,[0-9]*\b//" "${CMDLINE}"
