#!/bin/bash -e
#
# Build the engines that aren't packaged: mod-host (the LV2 plugin host) and the
# sfizz LV2 plugin. Runs at image-build time in the chroot; this whole file is
# piped to on_chroot.
#
# NOTE: Dexed (the two DX7 voices in voices.json) is NOT built here yet — it
# needs the same verify-on-hardware-then-codify treatment sfizz got. Until then
# those voices will fail to load. See docs/engine-architecture.md.

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

# --- sfizz LV2 plugin -> /usr/local/lib/lv2/sfizz.lv2 ---
# The LV2 plugin lives in the sfizz-UI repo, NOT sfztools/sfizz (that repo builds
# only the core library + a standalone JACK client we don't use). mod-host loads
# sfizz as an LV2 plugin (URI http://sfztools.github.io/sfizz), so we must build
# the plugin. Headless: PLUGIN_LV2_UI=OFF avoids the cairo/X11 GUI deps; JACK/
# shared/render/VST3 targets are off (unused here). Verified on hardware before
# codifying — this is the recipe that actually produces sfizz.lv2.
if [ ! -d /usr/local/lib/lv2/sfizz.lv2 ] && [ ! -d /usr/lib/lv2/sfizz.lv2 ]; then
	echo "Building sfizz LV2 plugin (this takes a while) ..."
	git clone --recursive https://github.com/sfztools/sfizz-ui.git "${BUILD}/sfizz-ui"
	cmake -S "${BUILD}/sfizz-ui" -B "${BUILD}/sfizz-ui/build" \
		-DCMAKE_BUILD_TYPE=Release \
		-DPLUGIN_LV2=ON -DPLUGIN_LV2_UI=OFF \
		-DSFIZZ_JACK=OFF -DSFIZZ_SHARED=OFF -DSFIZZ_RENDER=OFF -DPLUGIN_VST3=OFF
	cmake --build "${BUILD}/sfizz-ui/build" -j"$(nproc)"
	cmake --install "${BUILD}/sfizz-ui/build"
	ldconfig
fi

# Don't leave build trees in the image.
rm -rf "${BUILD}"
