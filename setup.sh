#!/bin/bash
#
# setup.sh — Provision a Raspberry Pi from a blank Pi OS Lite image (with the
# PREEMPT_RT kernel already installed) into a fully running multi-engine synth.
#
# Run this ON THE PI, as the `synth` user, from the repo checkout:
#
#     git clone https://github.com/ianbakst/pi-synth.git ~/synth
#     cd ~/synth
#     ./setup.sh
#
# The script is idempotent: re-run it any time. Already-completed steps are
# detected and skipped. It will prompt for your sudo password once.
#
# Flags:
#     --skip-apt     skip the apt update/install step
#     --skip-build   skip building mod-host / sfizz from source
#     --skip-boot    skip writing /boot/firmware config (config.txt / cmdline.txt)
#     --yes          don't pause for confirmation before reboot-relevant changes
#
# What it does, in order:
#     1.  Preflight checks (user, location, sudo, RT kernel)
#     2.  apt update + install system packages
#     3.  Boot config (I2S DAC overlay + CPU isolation) — needs reboot
#     4.  Build mod-host and sfizz from source
#     5.  Project directories, default soundfont, voices manifest
#     6.  sudoers rule so the UI can switch engines
#     7.  Detect audio hardware, pick a JACK device
#     8.  Install + enable systemd services
#     9.  Hardware checks (DAC, touchscreen, MIDI)
#    10.  Summary

set -uo pipefail

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SYNTH_USER="synth"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXPECTED_REPO="/home/${SYNTH_USER}/synth"
HOME_DIR="/home/${SYNTH_USER}"
BUILD_DIR="${HOME_DIR}/build"
SOUNDFONT_DIR="${HOME_DIR}/soundfonts"
INSTRUMENTS_DIR="${HOME_DIR}/instruments"

SKIP_APT=0
SKIP_BUILD=0
SKIP_BOOT=0
ASSUME_YES=0

for arg in "$@"; do
    case "$arg" in
        --skip-apt)   SKIP_APT=1 ;;
        --skip-build) SKIP_BUILD=1 ;;
        --skip-boot)  SKIP_BOOT=1 ;;
        --yes|-y)     ASSUME_YES=1 ;;
        *) echo "Unknown flag: $arg" >&2; exit 1 ;;
    esac
done

# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------
BOLD=$'\033[1m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'
BLUE=$'\033[34m'; DIM=$'\033[2m'; RESET=$'\033[0m'
STEP=0
NEEDS_REBOOT=0
WARNINGS=()

step()  { STEP=$((STEP+1)); printf '\n%s━━━ Step %d: %s%s\n' "$BOLD$BLUE" "$STEP" "$1" "$RESET"; }
ok()    { printf '  %s✓%s %s\n' "$GREEN" "$RESET" "$1"; }
info()  { printf '  %s·%s %s\n' "$DIM" "$RESET" "$1"; }
warn()  { printf '  %s!%s %s\n' "$YELLOW" "$RESET" "$1"; WARNINGS+=("$1"); }
die()   { printf '\n  %s✗ %s%s\n' "$RED" "$1" "$RESET" >&2; exit 1; }

confirm() {
    [ "$ASSUME_YES" -eq 1 ] && return 0
    read -r -p "  ${BOLD}$1 [y/N]${RESET} " reply
    [[ "$reply" =~ ^[Yy]$ ]]
}

# ---------------------------------------------------------------------------
# Step 1 — Preflight
# ---------------------------------------------------------------------------
step "Preflight checks"

[ "$(id -un)" = "$SYNTH_USER" ] || die "Run this as the '$SYNTH_USER' user (currently $(id -un))."
[ "$(id -u)" -ne 0 ] || die "Do not run as root. Run as '$SYNTH_USER'; it will sudo when needed."

if [ "$REPO_DIR" != "$EXPECTED_REPO" ]; then
    warn "Repo is at $REPO_DIR but services expect $EXPECTED_REPO."
    warn "Move it: mv '$REPO_DIR' '$EXPECTED_REPO' — paths are hardcoded in config.py."
    confirm "Continue anyway?" || die "Aborted. Relocate the repo to $EXPECTED_REPO."
else
    ok "Repo location: $REPO_DIR"
fi

sudo -v || die "Need sudo access to install packages and services."
ok "sudo available"

KERNEL="$(uname -r)"
if [[ "$KERNEL" == *"-rt"* || "$(uname -v)" == *"PREEMPT_RT"* ]]; then
    ok "PREEMPT_RT kernel: $KERNEL"
else
    warn "Kernel '$KERNEL' does not look like PREEMPT_RT. Audio will work but with higher latency/xruns."
fi

id -nG "$SYNTH_USER" | grep -qw audio && ok "User in 'audio' group" \
    || { sudo usermod -aG audio "$SYNTH_USER"; warn "Added $SYNTH_USER to 'audio' group — log out/in (or reboot) to take effect."; }

# ---------------------------------------------------------------------------
# Step 2 — System packages
# ---------------------------------------------------------------------------
step "System packages"
if [ "$SKIP_APT" -eq 1 ]; then
    info "Skipped (--skip-apt)"
else
    # Fresh images can have a skewed clock; NTP + ignore Valid-Until avoids apt errors.
    sudo timedatectl set-ntp true 2>/dev/null || true
    sleep 2
    APT_OPTS="-o Acquire::Check-Valid-Until=false"

    info "apt-get update ..."
    sudo apt-get update -qq $APT_OPTS || warn "apt update reported errors (continuing)."

    # Pre-seed jackd2's RT-priority debconf question so the install is non-interactive.
    echo "jackd2 jackd/tweak_rt_limits boolean true" | sudo debconf-set-selections

    info "Installing packages from apt-requirements.txt ..."
    # shellcheck disable=SC2046
    sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq $APT_OPTS \
        $(grep -vE '^\s*#|^\s*$' "$REPO_DIR/apt-requirements.txt") \
        && ok "Base packages installed" \
        || warn "Some packages failed to install — review output above."

    # Dexed: try the repo; it's frequently absent on Pi OS. Non-fatal.
    if sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq $APT_OPTS dexed 2>/dev/null; then
        ok "Dexed installed from apt"
    else
        warn "Dexed not in apt repo — DX7 voices will be inert until you build/install Dexed manually (see AUDIO_UPGRADE.md)."
    fi
fi

# ---------------------------------------------------------------------------
# Step 3 — Boot configuration (I2S DAC + CPU isolation)
# ---------------------------------------------------------------------------
step "Boot configuration"
CONFIG_TXT="/boot/firmware/config.txt"
CMDLINE_TXT="/boot/firmware/cmdline.txt"

if [ "$SKIP_BOOT" -eq 1 ]; then
    info "Skipped (--skip-boot)"
elif [ ! -f "$CONFIG_TXT" ]; then
    warn "$CONFIG_TXT not found — non-standard image. Skipping boot config."
else
    add_config_line() {
        local line="$1"
        if grep -qxF "$line" "$CONFIG_TXT"; then
            info "config.txt already has: $line"
        else
            echo "$line" | sudo tee -a "$CONFIG_TXT" >/dev/null
            ok "config.txt += $line"
            NEEDS_REBOOT=1
        fi
    }
    if ! grep -q "pi-synth" "$CONFIG_TXT"; then
        echo "" | sudo tee -a "$CONFIG_TXT" >/dev/null
        echo "# --- pi-synth ---" | sudo tee -a "$CONFIG_TXT" >/dev/null
    fi
    add_config_line "dtparam=i2s=on"
    add_config_line "dtoverlay=hifiberry-dac"
    add_config_line "camera_auto_detect=0"
    add_config_line "dtoverlay=disable-bt"
    add_config_line "dtparam=audio=off"

    # CPU isolation on the kernel command line (single line; append once).
    ISOL="isolcpus=2,3 nohz_full=2,3 rcu_nocbs=2,3"
    if grep -q "isolcpus=2,3" "$CMDLINE_TXT"; then
        info "cmdline.txt already isolates cores 2,3"
    else
        sudo sed -i "s|\$| $ISOL|" "$CMDLINE_TXT"
        ok "cmdline.txt += $ISOL"
        NEEDS_REBOOT=1
    fi
fi

# ---------------------------------------------------------------------------
# Step 4 — Build mod-host and sfizz from source
# ---------------------------------------------------------------------------
step "Build engines from source (mod-host, sfizz)"
if [ "$SKIP_BUILD" -eq 1 ]; then
    info "Skipped (--skip-build)"
else
    mkdir -p "$BUILD_DIR"

    # --- mod-host ---
    if command -v mod-host >/dev/null 2>&1 || [ -x /usr/local/bin/mod-host ]; then
        ok "mod-host already installed"
    else
        info "Building mod-host (a few minutes) ..."
        if [ ! -d "$BUILD_DIR/mod-host" ]; then
            git clone --depth 1 https://github.com/mod-audio/mod-host.git "$BUILD_DIR/mod-host" \
                || die "Failed to clone mod-host."
        fi
        ( cd "$BUILD_DIR/mod-host" && make -j"$(nproc)" && sudo make install ) \
            && sudo ldconfig && ok "mod-host built and installed to /usr/local/bin" \
            || die "mod-host build failed."
    fi

    # --- sfizz (LV2) ---
    if [ -d /usr/local/lib/lv2/sfizz.lv2 ] || [ -d /usr/lib/lv2/sfizz.lv2 ]; then
        ok "sfizz LV2 already installed"
    else
        info "Building sfizz (5-15 minutes on a Pi 4) ..."
        if [ ! -d "$BUILD_DIR/sfizz" ]; then
            git clone --recursive --depth 1 https://github.com/sfztools/sfizz.git "$BUILD_DIR/sfizz" \
                || die "Failed to clone sfizz."
        fi
        ( cd "$BUILD_DIR/sfizz" && mkdir -p build && cd build \
            && cmake -DCMAKE_BUILD_TYPE=Release -DSFIZZ_JACK=ON .. \
            && make -j"$(nproc)" && sudo make install ) \
            && sudo ldconfig && ok "sfizz built and installed to /usr/local/lib/lv2" \
            || die "sfizz build failed."
    fi
fi

# ---------------------------------------------------------------------------
# Step 5 — Project directories, default soundfont, voices manifest
# ---------------------------------------------------------------------------
step "Project directories and assets"
mkdir -p "$SOUNDFONT_DIR" "$INSTRUMENTS_DIR"
ok "Created $SOUNDFONT_DIR and $INSTRUMENTS_DIR"

# Install the voices manifest (don't clobber an existing edited one).
if [ -f "$INSTRUMENTS_DIR/voices.json" ]; then
    info "voices.json already present — left untouched"
else
    cp "$REPO_DIR/instruments/voices.json" "$INSTRUMENTS_DIR/voices.json"
    ok "Installed voices.json (edit paths to match your downloaded instruments)"
fi

# Default soundfont for the FluidSynth fallback engine.
if [ -e "$SOUNDFONT_DIR/default.sf2" ]; then
    ok "default.sf2 present"
else
    CANDIDATE=$(find "$SOUNDFONT_DIR" -maxdepth 2 -iname '*.sf2' 2>/dev/null | head -1)
    SYSFONT="/usr/share/sounds/sf2/FluidR3_GM.sf2"
    if [ -n "$CANDIDATE" ]; then
        ln -sf "$CANDIDATE" "$SOUNDFONT_DIR/default.sf2"
        ok "Linked default.sf2 -> $CANDIDATE"
    elif [ -f "$SYSFONT" ]; then
        ln -sf "$SYSFONT" "$SOUNDFONT_DIR/default.sf2"
        ok "Linked default.sf2 -> $SYSFONT (system GM font)"
    else
        warn "No .sf2 found. Drop one in $SOUNDFONT_DIR and: ln -s <file> $SOUNDFONT_DIR/default.sf2"
    fi
fi

# ---------------------------------------------------------------------------
# Step 6 — sudoers rule for engine switching
# ---------------------------------------------------------------------------
step "Engine-switching sudoers rule"
# engine-manager.sh (run by the UI) needs to start/stop engine services without
# a password prompt. Scope it tightly to just those units.
SUDOERS_FILE="/etc/sudoers.d/synth-engine"
SUDOERS_CONTENT="${SYNTH_USER} ALL=(root) NOPASSWD: \
/usr/bin/systemctl start fluidsynth-engine.service, \
/usr/bin/systemctl stop fluidsynth-engine.service, \
/usr/bin/systemctl start setbfree.service, \
/usr/bin/systemctl stop setbfree.service"

echo "$SUDOERS_CONTENT" | sudo tee "$SUDOERS_FILE" >/dev/null
sudo chmod 0440 "$SUDOERS_FILE"
if sudo visudo -cf "$SUDOERS_FILE" >/dev/null 2>&1; then
    ok "Installed $SUDOERS_FILE"
else
    sudo rm -f "$SUDOERS_FILE"
    die "sudoers validation failed — rule removed."
fi

chmod +x "$REPO_DIR/scripts/"*.sh
ok "Made scripts executable"

# ---------------------------------------------------------------------------
# Step 7 — Detect audio hardware, choose JACK backend
# ---------------------------------------------------------------------------
step "Audio device detection (JACK backend)"
# The committed jack.service targets the HiFiBerry DAC. If it isn't present
# (e.g. testing without the DAC wired up) we write a drop-in override pointing
# JACK at whatever is available, falling back to the dummy backend.
DROPIN_DIR="/etc/systemd/system/jack.service.d"
DROPIN="$DROPIN_DIR/10-device.conf"
JACK_BASE="/usr/bin/chrt -f 90 /usr/bin/taskset -c 2,3 /usr/bin/jackd"

write_dropin() {
    local backend="$1" note="$2"
    sudo mkdir -p "$DROPIN_DIR"
    sudo tee "$DROPIN" >/dev/null <<EOF
# Auto-generated by setup.sh — $note
[Service]
ExecStart=
ExecStart=$JACK_BASE $backend
EOF
    ok "JACK backend: $note"
}

if aplay -l 2>/dev/null | grep -qi hifiberry; then
    # DAC present — clear any override so the canonical service file is used.
    sudo rm -f "$DROPIN" 2>/dev/null || true
    ok "HiFiBerry DAC detected — using hw:sndrpihifiberry from jack.service"
elif aplay -l 2>/dev/null | grep -qi 'Headphones'; then
    write_dropin "-d alsa -d hw:Headphones -r 48000 -p 256 -n 3" \
        "no DAC — using Pi 3.5mm headphone jack (hw:Headphones)"
    warn "Using built-in headphone jack for testing. Wire up the HiFiBerry and re-run for production audio."
else
    write_dropin "-d dummy -r 48000 -p 128" \
        "no audio hardware — using JACK dummy backend (no sound, services start)"
    warn "No audio output detected. JACK will run on the dummy backend (silent). Connect the DAC and re-run setup.sh."
fi

# ---------------------------------------------------------------------------
# Step 8 — Install and enable systemd services
# ---------------------------------------------------------------------------
step "systemd services"

# Disable the Pi OS default FluidSynth user service — it grabs port 9800/the DAC.
systemctl --user disable --now fluidsynth.service 2>/dev/null \
    && ok "Disabled default per-user fluidsynth.service" \
    || info "No conflicting user fluidsynth.service"

for svc in cpu-performance jack mod-host fluidsynth-engine setbfree synth-ui; do
    sudo cp "$REPO_DIR/systemd/$svc.service" "/etc/systemd/system/$svc.service"
done
ok "Copied 6 service files to /etc/systemd/system"

sudo systemctl daemon-reload
ok "daemon-reload"

# Always-on at boot. fluidsynth-engine + setbfree are on-demand (engine-manager.sh) — NOT enabled.
for svc in cpu-performance jack mod-host synth-ui; do
    sudo systemctl enable "$svc.service" >/dev/null 2>&1
done
ok "Enabled on boot: cpu-performance, jack, mod-host, synth-ui"
info "On-demand (not enabled): fluidsynth-engine, setbfree — started by the UI on voice select"

if confirm "Start the services now?"; then
    sudo systemctl restart jack.service
    sleep 2
    if systemctl is-active --quiet jack.service; then
        ok "jack.service active"
        sudo systemctl restart mod-host.service
        sleep 1
        systemctl is-active --quiet mod-host.service && ok "mod-host.service active" \
            || warn "mod-host failed to start: journalctl -u mod-host -n 30"
        sudo systemctl restart synth-ui.service
        systemctl is-active --quiet synth-ui.service && ok "synth-ui.service active" \
            || warn "synth-ui failed to start: journalctl -u synth-ui -n 30"
    else
        warn "jack.service failed to start: journalctl -u jack -n 30"
    fi
else
    info "Services enabled but not started. They will come up on next boot."
fi

# ---------------------------------------------------------------------------
# Step 9 — Hardware checks
# ---------------------------------------------------------------------------
step "Hardware checks"

# DAC
aplay -l 2>/dev/null | grep -qi hifiberry && ok "DAC: HiFiBerry present" \
    || warn "DAC: HiFiBerry not detected (ok if not wired up / before reboot)."

# Touchscreen — event number can shift; config expects /dev/input/event4.
if [ -e /dev/input/event4 ]; then
    ok "Touchscreen: /dev/input/event4 present"
else
    FOUND=$(grep -lE 'ft5x06|EP0110|generic ft5x06' /sys/class/input/event*/device/name 2>/dev/null \
        | sed 's|/sys/class/input/\(event[0-9]*\)/.*|\1|' | head -1)
    if [ -n "$FOUND" ]; then
        warn "Touchscreen is at /dev/input/$FOUND, not event4. Update TOUCH_DEVICE in config.py and SDL_MOUSEDEV in synth-ui.service."
    else
        warn "Touchscreen not found at /dev/input/event4 (ok before reboot / if not connected)."
    fi
fi

# Framebuffer
[ -e /dev/fb0 ] && ok "Framebuffer: /dev/fb0 present" \
    || warn "No /dev/fb0 — UI has nowhere to draw (check display connection / reboot)."

# MIDI
if aconnect -l 2>/dev/null | grep -qiE 'client (1[6-9]|[2-9][0-9])'; then
    ok "MIDI: external device(s) detected on ALSA sequencer"
else
    info "MIDI: no external keyboard detected (plug one in; midi-connect.sh runs on engine start)."
fi

# RT limits
RTPRIO=$(ulimit -r 2>/dev/null || echo 0)
if [ "$RTPRIO" = "unlimited" ] || { [ "$RTPRIO" -ge 80 ] 2>/dev/null; }; then
    ok "RT priority limit: $RTPRIO"
else
    warn "RT priority limit is $RTPRIO. Ensure /etc/security/limits.conf has '@audio - rtprio 99' and re-login."
fi

# ---------------------------------------------------------------------------
# Step 10 — Summary
# ---------------------------------------------------------------------------
printf '\n%s━━━ Done%s\n' "$BOLD$GREEN" "$RESET"

if [ "${#WARNINGS[@]}" -gt 0 ]; then
    printf '\n%sReview these warnings:%s\n' "$BOLD$YELLOW" "$RESET"
    for w in "${WARNINGS[@]}"; do printf '  %s!%s %s\n' "$YELLOW" "$RESET" "$w"; done
fi

printf '\n%sNext steps:%s\n' "$BOLD" "$RESET"
echo "  • Download instruments and update paths in $INSTRUMENTS_DIR/voices.json"
echo "  • Check status:  systemctl status jack mod-host synth-ui"
echo "  • Live logs:     journalctl -u synth-ui -f"

if [ "$NEEDS_REBOOT" -eq 1 ]; then
    printf '\n%s⟳ A reboot is required%s for boot-config changes (DAC overlay / CPU isolation).\n' "$BOLD$YELLOW" "$RESET"
    if confirm "Reboot now?"; then
        sudo reboot
    fi
else
    echo "  • No reboot required."
fi
