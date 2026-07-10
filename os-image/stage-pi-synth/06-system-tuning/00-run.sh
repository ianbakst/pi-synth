#!/bin/bash -e
#
# System tuning for low-jitter realtime audio. CPU governor + IRQ affinity are
# handled at runtime by cpu-performance.service (enabled in 05-services); the
# remaining tuning that belongs in the image lives here.

# RT limits for login/interactive sessions.
install -m 644 files/audio-rt.conf "${ROOTFS_DIR}/etc/security/limits.d/audio-rt.conf"

# RAM-only journal.
mkdir -p "${ROOTFS_DIR}/etc/systemd/journald.conf.d"
install -m 644 files/journald-volatile.conf \
	"${ROOTFS_DIR}/etc/systemd/journald.conf.d/volatile.conf"

on_chroot << 'EOF'
set -e
# No swap on an appliance (removes a page-fault jitter source). WiFi + SSH are
# intentionally left enabled for development.
apt-get -y purge dphys-swapfile 2>/dev/null || true

# Disable services that add scheduling jitter and aren't needed here.
for svc in avahi-daemon triggerhappy ModemManager bluetooth hciuart cron; do
	systemctl disable "${svc}.service" 2>/dev/null || true
done
EOF
