#!/bin/bash
#
# build.sh — build the pi-synth appliance image with pi-gen, in Docker.
#
# Keeps the pi-gen submodule pristine (git-wise): we bind-mount this whole repo
# read-only into the build container (via PIGEN_DOCKER_OPTS) and reference the
# stage, kernel artifacts, and app source from there. The config's STAGE_LIST /
# PI_SYNTH_SRC point at that mount (/pi-synth-src).
#
# Config delivery: we copy os-image/config to os-image/pi-gen/config rather than
# passing `build-docker.sh -c`. pi-gen auto-detects a config in its own dir and
# bakes it into the image (COPY . /pi-gen/), so the inner build.sh sources it
# directly. This deliberately avoids build-docker.sh's `-c` path rewrite, which
# uses a GNU-sed-only `\s` and silently breaks on macOS (BSD sed) — leaving the
# container trying to source the *host* config path. pi-gen already gitignores
# `config`, so the copied file never dirties the submodule.
#
# Prerequisites:
#   - Docker running (Docker Desktop on macOS; Apple Silicon builds arm64 natively).
#   - Your harvested PREEMPT_RT kernel artifacts in os-image/kernel/ (kernel8.img
#     + modules/<KVER>/; see os-image/kernel/README.md).
#
# Usage:
#   ./build.sh            # full build
#   CONTINUE=1 ./build.sh # resume after a failed/interrupted stage
#
# Output image lands in os-image/pi-gen/deploy/ (gitignored).

set -euo pipefail

die() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OS_IMAGE="${REPO}/os-image"

command -v docker >/dev/null 2>&1 || die "docker not found. Install/start Docker Desktop."
docker info >/dev/null 2>&1 || die "docker daemon not reachable. Start Docker."

[ -e "${OS_IMAGE}/pi-gen/build-docker.sh" ] || \
    die "pi-gen submodule missing. Run: git submodule update --init os-image/pi-gen"

# Validate the harvested RT kernel artifacts (see os-image/kernel/README.md).
shopt -s nullglob
imgs=("${OS_IMAGE}"/kernel/kernel8.img "${OS_IMAGE}"/kernel/*.img)
moddirs=("${OS_IMAGE}"/kernel/modules/*/)
shopt -u nullglob
[ "${#imgs[@]}" -gt 0 ] || \
    die "no kernel image in os-image/kernel/ (expected kernel8.img). Harvest it from your Pi — see os-image/kernel/README.md."
[ "${#moddirs[@]}" -gt 0 ] || \
    die "no modules/<version> tree in os-image/kernel/modules/. rsync /lib/modules/\$(uname -r) from your Pi — see os-image/kernel/README.md."
[ "${#moddirs[@]}" -eq 1 ] || \
    die "multiple module trees in os-image/kernel/modules/ (${moddirs[*]##*/modules/}). Leave exactly one."

echo "Using RT kernel image:   $(basename "${imgs[0]}")"
echo "Using RT kernel modules: $(basename "${moddirs[0]%/}")"

# Deliver our config the pi-gen-native way (gitignored inside the submodule).
cp "${OS_IMAGE}/config" "${OS_IMAGE}/pi-gen/config"
echo "Config staged at pi-gen/config"

# A failed/interrupted run leaves a 'pigen_work' container that build-docker.sh
# will refuse to run over. Clear it for a fresh build; keep it when resuming.
if [ "${CONTINUE:-0}" != "1" ]; then
    docker rm -v pigen_work >/dev/null 2>&1 && echo "Removed stale pigen_work container" || true
fi

# Expose this repo to the container. The config references /pi-synth-src.
export PIGEN_DOCKER_OPTS="-v ${REPO}:/pi-synth-src:ro ${PIGEN_DOCKER_OPTS:-}"

echo "Mounting repo read-only into build container at /pi-synth-src"
cd "${OS_IMAGE}/pi-gen"
exec ./build-docker.sh
