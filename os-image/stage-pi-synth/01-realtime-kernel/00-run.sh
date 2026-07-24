#!/bin/bash -e
#
# Install the harvested PREEMPT_RT kernel artifacts into the image.
#
# Raspberry Pi OS boots the kernel via the VideoCore firmware, which loads a
# board-specific default filename (base config.txt sets arm_64bit=1, no custom
# kernel= override): kernel8.img on Pi 4-and-earlier, kernel_2712.img on
# Pi 5/CM5-generation (BCM2712) boards. We overwrite that file with the RT
# kernel, drop in the matching /lib/modules tree, refresh the dtbs/overlays,
# and regenerate module dependency metadata with depmod.
#
# PI_SYNTH_BOARD (from os-image/config, default pi4) selects both the source
# layout under os-image/kernel/ and the target filename:
#   pi4 (default): os-image/kernel/kernel8.img          -> kernel8.img
#   cm5:           os-image/kernel/cm5/kernel_2712.img  -> kernel_2712.img
#
# Expected layout — see os-image/kernel/README.md:
#   <board-dir>/<image>.img   the RT kernel image (required)
#   <board-dir>/modules/<KVER>/  the /lib/modules/<KVER> tree (required)
#   <board-dir>/dtb/*.dtb        device trees (optional; base image ships compatible ones)
#   <board-dir>/overlays/*.dtbo  overlays  (optional; base image ships compatible ones)

BOARD="${PI_SYNTH_BOARD:-pi4}"
case "${BOARD}" in
	pi4) KSRC="${PI_SYNTH_SRC}/os-image/kernel"       ; KIMG_NAME="kernel8.img" ;;
	cm5) KSRC="${PI_SYNTH_SRC}/os-image/kernel/cm5"   ; KIMG_NAME="kernel_2712.img" ;;
	*)
		echo "ERROR: unknown PI_SYNTH_BOARD '${BOARD}' (expected pi4 or cm5)." >&2
		exit 1
		;;
esac
BOOT="${ROOTFS_DIR}/boot/firmware"

# --- kernel image (required) -> overwrite the board's default kernel filename ---
IMG="${KSRC}/${KIMG_NAME}"
if [ ! -f "${IMG}" ]; then
	# fall back to a single *.img if it was harvested under a different name
	IMG="$(ls "${KSRC}"/*.img 2>/dev/null | head -1 || true)"
fi
if [ -z "${IMG}" ] || [ ! -f "${IMG}" ]; then
	echo "ERROR: no kernel image in ${KSRC} (expected ${KIMG_NAME})." >&2
	echo "       Harvest it (see os-image/kernel/README.md)." >&2
	exit 1
fi
echo "Installing RT kernel image (${BOARD}): $(basename "${IMG}") -> ${KIMG_NAME}"
install -m 644 "${IMG}" "${BOOT}/${KIMG_NAME}"

# --- modules (required) -> /lib/modules/<KVER> ---
MODDIR="$(find "${KSRC}/modules" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | head -1 || true)"
if [ -z "${MODDIR}" ]; then
	echo "ERROR: no modules/<version> tree in ${KSRC}/modules/." >&2
	echo "       rsync /lib/modules/\$(uname -r) from your Pi (see kernel/README.md)." >&2
	exit 1
fi
KVER="$(basename "${MODDIR}")"
echo "Installing RT kernel modules: ${KVER}"
mkdir -p "${ROOTFS_DIR}/lib/modules"
rsync -a "${MODDIR}" "${ROOTFS_DIR}/lib/modules/"

# --- dtbs + overlays (optional) ---
if compgen -G "${KSRC}/dtb/*.dtb" >/dev/null; then
	echo "Installing harvested device trees"
	cp "${KSRC}"/dtb/*.dtb "${BOOT}/"
fi
if compgen -G "${KSRC}/overlays/*.dtbo" >/dev/null; then
	echo "Installing harvested overlays"
	mkdir -p "${BOOT}/overlays"
	cp "${KSRC}"/overlays/*.dtbo "${BOOT}/overlays/"
fi

# --- regenerate module dependency metadata against the installed tree ---
on_chroot << EOF
depmod -a "${KVER}"
EOF
