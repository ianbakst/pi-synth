#!/bin/bash -e
#
# Build the engines that aren't packaged: mod-host (LV2 host for sfizz/Dexed) and
# sfizz (LV2, JACK-enabled). Mirrors the source builds in the old setup.sh, but
# runs at image-build time in the chroot. This whole file is piped to on_chroot.

set -e
BUILD=/tmp/build
mkdir -p "${BUILD}"

# --- mod-host -> /usr/local/bin/mod-host ---
if ! command -v mod-host >/dev/null 2>&1 && [ ! -x /usr/local/bin/mod-host ]; then
	echo "Building mod-host ..."
	git clone --depth 1 https://github.com/mod-audio/mod-host.git "${BUILD}/mod-host"
	make -C "${BUILD}/mod-host" -j"$(nproc)"
	make -C "${BUILD}/mod-host" install
	ldconfig
fi

# --- sfizz (LV2) -> /usr/local/lib/lv2/sfizz.lv2 ---
if [ ! -d /usr/local/lib/lv2/sfizz.lv2 ] && [ ! -d /usr/lib/lv2/sfizz.lv2 ]; then
	echo "Building sfizz (this takes a while) ..."
	git clone --recursive --depth 1 https://github.com/sfztools/sfizz.git "${BUILD}/sfizz"
	cmake -S "${BUILD}/sfizz" -B "${BUILD}/sfizz/build" \
		-DCMAKE_BUILD_TYPE=Release -DSFIZZ_JACK=ON
	cmake --build "${BUILD}/sfizz/build" -j"$(nproc)"
	cmake --install "${BUILD}/sfizz/build"
	ldconfig
fi

# Don't leave build trees in the image.
rm -rf "${BUILD}"
