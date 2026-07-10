#!/bin/bash -e

# Continue building on top of the previous stage's rootfs (stage2 / Lite).
if [ ! -d "${ROOTFS_DIR}" ]; then
	copy_previous
fi
