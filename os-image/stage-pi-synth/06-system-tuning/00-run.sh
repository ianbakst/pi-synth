#!/bin/bash -e
#
# System tuning for low-jitter realtime audio. CPU governor + IRQ affinity are
# handled at runtime by cpu-performance.service (enabled in 05-services); the
# remaining tuning that belongs in the image lives here.

# RT limits for login/interactive sessions.
install -m 644 files/audio-rt.conf "${ROOTFS_DIR}/etc/security/limits.d/audio-rt.conf"

# Screen brightness: the DSI panel's backlight node is root-owned, so the UI
# can't dim the screen without this. See clients/backlight.py.
mkdir -p "${ROOTFS_DIR}/etc/udev/rules.d"
install -m 644 files/99-backlight.rules \
	"${ROOTFS_DIR}/etc/udev/rules.d/99-backlight.rules"

# Journal on disk, capped — see files/journald-persistent.conf for the tradeoff.
mkdir -p "${ROOTFS_DIR}/etc/systemd/journald.conf.d"
install -m 644 files/journald-persistent.conf \
	"${ROOTFS_DIR}/etc/systemd/journald.conf.d/persistent.conf"
# The old drop-in, from when logs were kept in RAM. Removed by name so an image
# rebuilt over an existing rootfs doesn't end up with both and the wrong winner.
rm -f "${ROOTFS_DIR}/etc/systemd/journald.conf.d/volatile.conf"

# --- RemoveIPC=no: stop SSH logouts from killing the audio stack ---
# systemd-logind defaults to RemoveIPC=yes, which destroys every POSIX shared
# memory segment and semaphore owned by a *normal* user (uid >= 1000) as soon as
# that user's last login session ends. jackd runs as 'synth' (uid 1000) as a
# system service, so an `ssh synth@host '<cmd>'` one-liner — a session that opens
# and immediately closes — wipes /dev/shm/jack_default_1000_0, jack_db-1000/ and
# every jack_sem.* out from under the running server.
#
# The failure is nastily silent: jackd holds open fds to the now-unlinked inodes,
# so it keeps running and systemd keeps reporting `active`, but every path is
# gone, so no client can ever connect again. Symptom is a synth that plays fine
# until the first SSH login, then goes permanently quiet with every JACK client
# failing "Cannot connect to server socket err = No such file or directory" —
# diagnosed on hardware by `ls -l /proc/$(pgrep jackd)/fd` showing every JACK
# file marked "(deleted)" against an empty /dev/shm.
#
# The appliance has no reason to reap IPC on logout, and admin here is entirely
# over SSH, so this would fire constantly. Alternative fixes (running jackd as a
# system uid < 1000, or `loginctl enable-linger synth`) are more invasive for the
# same effect.
mkdir -p "${ROOTFS_DIR}/etc/systemd/logind.conf.d"
install -m 644 files/logind-keep-ipc.conf \
	"${ROOTFS_DIR}/etc/systemd/logind.conf.d/10-keep-ipc.conf"

on_chroot << 'EOF'
set -e
# No swap on an appliance (removes a page-fault jitter source). WiFi + SSH are
# intentionally left enabled for development.
apt-get -y purge dphys-swapfile 2>/dev/null || true

# Disable services that add scheduling jitter and aren't needed here.
for svc in triggerhappy ModemManager bluetooth hciuart cron; do
	systemctl disable "${svc}.service" 2>/dev/null || true
done

# avahi-daemon (mDNS) is a deliberate exception, not disabled with the above:
# it publishes <hostname>.local (TARGET_HOSTNAME in os-image/config) so the
# board is reachable without hunting for its IP -- genuinely useful, and
# unlike bluetooth/ModemManager it does no hardware polling and never touches
# the isolated audio cores (1,2,3); it just runs on core 0 alongside SSH and
# the rest of the non-RT stack. If this ever proves to add real jitter, revert
# by moving it back into the loop above.
#
# NOT `|| true`, and the result is *asserted* rather than assumed. On a board
# built with the previous version of this line, avahi-daemon ended up installed
# but `inactive` and not enabled — the enable didn't take, the `2>/dev/null ||
# true` swallowed any sign of it, and the appliance was reachable only by IP.
# Checking is-enabled catches the case where the command reports success without
# creating the wants/ symlink.
systemctl enable avahi-daemon.service
systemctl is-enabled avahi-daemon.service

# Fail the build if the hostname didn't take: `<hostname>.local` is the only way
# to find this box on a network, and a mismatch between /etc/hostname and the
# 127.0.1.1 line in /etc/hosts breaks resolution in ways that are tedious to
# diagnose on an appliance with no console login (getty@tty1 is masked below).
test -s /etc/hostname
grep -q "127.0.1.1[[:space:]]\+$(cat /etc/hostname)" /etc/hosts
echo "hostname: $(cat /etc/hostname).local will be published over mDNS"

# Don't block boot waiting for the network to come "online": the audio stack
# doesn't need the network, this alone costs ~7s of boot, and it can hang boot
# outright if WiFi is flaky. NetworkManager itself still runs — boot just doesn't
# wait for it (WiFi/SSH come up normally, a moment later).
systemctl disable NetworkManager-wait-online.service 2>/dev/null || true

# Boot-time one-shots not wanted on a fixed appliance: the bootloader-EEPROM
# auto-update check (no surprise firmware changes) and the ext4 online-scrub
# reaper (there's no LVM here for it to act on).
for svc in rpi-eeprom-update e2scrub_reap; do
	systemctl disable "${svc}.service" 2>/dev/null || true
done

# No console login on the display: this is a synth appliance, admin is via SSH.
# Masking getty@tty1 removes the login prompt and frees the VT/DRM console so the
# KMSDRM UI owns the display uncontested.
systemctl mask getty@tty1.service 2>/dev/null || true
EOF
