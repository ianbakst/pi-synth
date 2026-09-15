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

# --- mod-ttymidi -> /usr/local/bin/ttymidi ---
# The serial->JACK MIDI bridge for the 5-pin DIN MIDI IN jack on UART0 (GPIO15).
# From mod-audio, same org as mod-host above; it's what MOD ships for DIN MIDI on
# their own hardware. Chosen over the various ttymidi forks because it is
# JACK-native (registers ttymidi:MIDI_in as JackPortIsPhysical|JackPortIsTerminal,
# exactly like a2jmidid does for USB keyboards, which is what lets JackGraph treat
# both transports under one rule), it sets a true 31250 baud via BOTHER/TCSETS2
# rather than needing the midi-uart0 clock-fudge overlay, and it handles MIDI
# running status and realtime bytes.
#
# Only the binary is installed: `make install` would also drop a JACK internal
# client (ttymidi.so) into jack's libdir, which we don't load.
if [ ! -x /usr/local/bin/ttymidi ]; then
	echo "Building mod-ttymidi ..."
	git clone --depth 1 https://github.com/mod-audio/mod-ttymidi.git "${BUILD}/mod-ttymidi"
	make -C "${BUILD}/mod-ttymidi" ttymidi -j"$(nproc)"
	install -m 755 "${BUILD}/mod-ttymidi/ttymidi" /usr/local/bin/ttymidi
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

# --- Optional instrument plugins ---------------------------------------------
# b_synth and Fluida are *additive*: without them the organ and soundfont voices
# fall back to their process engines and the rest of the library is unaffected.
# So a failure here warns loudly and continues rather than discarding a 45-minute
# image build — the voice then greys out on the board with "plugin not installed"
# (clients/voice.py validate), which is the same signal, delivered later.
# Anything the appliance genuinely can't boot without (kernel, app, services,
# hostname) stays fatal.
PLUGIN_WARNINGS=""

# ARM has no SSE. setBfree's common.mk hardcodes `-msse -msse2 -mfpmath=sse`
# into OPTIMIZATIONS, so every compile fails on aarch64 with "unrecognized
# command-line option". Overriding OPTIMIZATIONS on the make command line beats
# any assignment in the makefile; NEON is baseline on aarch64, so dropping the
# x86 vector flags costs nothing. We never build this image for x86.
ARM_OPTIMIZATIONS="-ffast-math -fomit-frame-pointer -O3 -fno-finite-math-only"

# Is a plugin URI already provided by anything on this system? Ask lv2ls
# (lilv-utils, installed in 00-packages) rather than guessing at bundle paths.
# Guessing cost us a whole redundant build once: Debian's setbfree ships its LV2
# at /usr/lib/lv2/b_synth -- WITHOUT the .lv2 suffix -- so a `[ -d .../b_synth.lv2 ]`
# check missed it, and we compiled a second copy of a plugin that was already
# there. The URI is what mod-host resolves; the directory name is a packaging
# detail we have no business predicting.
have_lv2() { lv2ls 2>/dev/null | grep -qxF "$1"; }

# --- setBfree LV2 (b_synth) ---
# The tonewheel organ as a plugin instead of a process. Debian's `setbfree`
# package ships this LV2 itself (at /usr/lib/lv2/b_synth), so this build is a
# fallback for when it doesn't -- normally it is skipped entirely.
#
# Same DSP as the standalone (tonewheels, key click, percussion, and the whirl
# Leslie in-plugin), which is why this is the organ of choice over Calf Organ /
# mda Combo: those are additive/divide-down models, not a B3.
if ! have_lv2 "http://gareus.org/oss/lv2/b_synth"; then
	echo "Building setBfree LV2 (b_synth) ..."
	git clone --depth 1 https://github.com/pantherb/setBfree.git "${BUILD}/setBfree"
	# Build the whole tree, tolerating the parts that need a GUI toolkit (the
	# standalone JACK/GL app and the robtk-based Leslie UI announce themselves
	# as skipped and are not wanted headless). b_synth.so compiles the src/,
	# b_whirl/, b_overdrive/ and b_reverb/ sources directly into itself, so it
	# doesn't depend on those subdirs producing their own artifacts.
	make -C "${BUILD}/setBfree" -j"$(nproc)" \
		OPTIMIZATIONS="${ARM_OPTIMIZATIONS}" || true

	# Install the bundle by hand: the top-level `make install` also wants to
	# install the standalone binary that was deliberately not built. Guarded as
	# one expression so `set -e` can't abort the stage on a partial build.
	if [ -f "${BUILD}/setBfree/b_synth/b_synth.so" ] \
		&& install -d /usr/local/lib/lv2/b_synth.lv2 \
		&& install -m 644 "${BUILD}/setBfree/b_synth/b_synth.so" \
			"${BUILD}"/setBfree/b_synth/*.ttl /usr/local/lib/lv2/b_synth.lv2/; then
		ldconfig
		echo "b_synth.lv2 installed"
	else
		PLUGIN_WARNINGS="${PLUGIN_WARNINGS} b_synth"
	fi
else
	echo "b_synth already provided by an installed package — skipping build"
fi

# --- Fluida LV2 (SoundFont player) -> /usr/local/lib/lv2/Fluida.lv2 ---
# Puts .sf2 playback inside mod-host so the soundfont voices stop needing a
# separate fluidsynth process. That process engine is now gone entirely.
#
# Chosen over Calf Fluidsynth (already installed) because Fluida is built as a
# headless-first LV2: the soundfont path and the instrument selection are
# exposed as atom patch properties / control ports the host can drive, which is
# exactly what mod-host `patch_set` and `param_set` need. Calf's file selection
# is driven from its GTK UI, so it may not be reachable from a headless host at
# all — verify with `verify_voices --inspect` before preferring it.
#
# NOTE: the DSP is NOT independent of the GUI here. Fluida's top-level Makefile
# builds its bundled libxputty (cairo/X11) before anything else and aborts the
# run if that fails, which is exactly what happened when the image lacked cairo
# headers -- "optional plugin failed" with the DSP never attempted. The GUI is
# built and then simply not loaded; libcairo2-dev/libx11-dev are in 00-packages
# for that reason.
if [ ! -d /usr/local/lib/lv2/Fluida.lv2 ] && [ ! -d /usr/lib/lv2/Fluida.lv2 ]; then
	echo "Building Fluida LV2 ..."
	git clone --recursive --depth 1 https://github.com/brummer10/Fluida.lv2.git \
		"${BUILD}/Fluida.lv2"
	# Same OPTIMIZATIONS override as setBfree: these audio-plugin makefiles
	# routinely assume x86 vector flags.
	if make -C "${BUILD}/Fluida.lv2" -j"$(nproc)" \
		OPTIMIZATIONS="${ARM_OPTIMIZATIONS}" \
		&& make -C "${BUILD}/Fluida.lv2" install PREFIX=/usr/local; then
		ldconfig
		echo "Fluida.lv2 installed"
	else
		PLUGIN_WARNINGS="${PLUGIN_WARNINGS} Fluida"
	fi
fi

if [ -n "${PLUGIN_WARNINGS}" ]; then
	echo "############################################################"
	echo "WARNING: optional plugin(s) failed to build:${PLUGIN_WARNINGS}"
	echo "The image is still usable — the affected voices will show as"
	echo "'plugin not installed' in verify_voices and grey out in the UI."
	echo "############################################################"
fi

# Don't leave build trees in the image.
rm -rf "${BUILD}"
