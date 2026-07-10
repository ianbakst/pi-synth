#!/bin/bash -e
#
# Install the harvested PREEMPT_RT kernel artifacts into the image.
#
# Raspberry Pi OS boots the kernel via the VideoCore firmware, which loads
# /boot/firmware/kernel8.img (the 64-bit default; base config.txt already sets
# arm_64bit=1 and no custom kernel=). So we simply overwrite kernel8.img with the
# RT kernel, drop in the matching /lib/modules tree, refresh the dtbs/overlays,
# and regenerate module dependency metadata with depmod.
#
# Expected layout in os-image/kernel/ (harvested from a working RT Pi):
#   kernel8.img            the RT kernel image (required)
#   modules/<KVER>/        the /lib/modules/<KVER> tree (required)
#   dtb/*.dtb              device trees (optional; base image ships compatible ones)
#   overlays/*.dtbo        overlays  (optional; base image ships compatible ones)

KSRC="${PI_SYNTH_SRC}/os-image/kernel"
BOOT="${ROOTFS_DIR}/boot/firmware"

# --- kernel image (required) -> overwrite the default kernel8.img ---
IMG="${KSRC}/kernel8.img"
if [ ! -f "${IMG}" ]; then
	# fall back to a single *.img if it was harvested under a custom name
	IMG="$(ls "${KSRC}"/*.img 2>/dev/null | head -1 || true)"
fi
if [ -z "${IMG}" ] || [ ! -f "${IMG}" ]; then
	echo "ERROR: no kernel image in ${KSRC} (expected kernel8.img)." >&2
	echo "       Harvest it from your Pi (see os-image/kernel/README.md)." >&2
	exit 1
fi
echo "Installing RT kernel image: $(basename "${IMG}") -> kernel8.img"
install -m 644 "${IMG}" "${BOOT}/kernel8.img"

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
