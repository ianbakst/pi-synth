#!/bin/bash -e
#
# WiFi provisioning (optional). If os-image/wifi.env exists, bake a NetworkManager
# connection into the image so the Pi joins WiFi on first boot. Absent = no WiFi
# preconfigured (Ethernet still works). See os-image/wifi.env.example.
#
# This is the up-to-date path for Raspberry Pi OS bookworm, which uses
# NetworkManager: a /etc/NetworkManager/system-connections/*.nmconnection profile
# — NOT the obsolete boot-partition wpa_supplicant.conf.

# --- WiFi power saving: off, always -------------------------------------------
#
# Written before the wifi.env check below, because this applies to any unit on
# WiFi whether or not the image baked a connection into it.
#
# The symptom it fixes: the board stays associated and keeps answering ARP —
# which is broadcast — while dropping unicast, so it holds its address and
# looks online from its own screen but answers neither ping nor SSH. It comes
# back on a power cycle, or whenever the Pi itself sends something. That is
# brcmfmac's power save, and it bit this board three times in one afternoon.
#
# NetworkManager's default is `0` (use the global default), which leaves the
# driver's own choice in place; `2` is disable. There is nothing to weigh here:
# the instrument is mains-powered and its only route in is this radio.
PS_CONF="${ROOTFS_DIR}/etc/NetworkManager/conf.d/wifi-powersave-off.conf"
mkdir -p "$(dirname "${PS_CONF}")"
cat > "${PS_CONF}" <<'EOF'
[connection]
wifi.powersave = 2
EOF
echo "wifi: disabled WiFi power saving"

WIFI_ENV="${PI_SYNTH_SRC}/os-image/wifi.env"
if [ ! -f "${WIFI_ENV}" ]; then
	echo "wifi: no os-image/wifi.env — skipping WiFi provisioning"
	exit 0
fi

# shellcheck disable=SC1090
. "${WIFI_ENV}"

if [ -z "${WIFI_SSID:-}" ] || [ -z "${WIFI_PSK:-}" ]; then
	echo "wifi: WIFI_SSID/WIFI_PSK not set in wifi.env — skipping" >&2
	exit 0
fi

CONN_DIR="${ROOTFS_DIR}/etc/NetworkManager/system-connections"
CONN_FILE="${CONN_DIR}/synth-wifi.nmconnection"
mkdir -p "${CONN_DIR}"

{
	echo "[connection]"
	echo "id=${WIFI_SSID}"
	echo "type=wifi"
	echo "autoconnect=true"
	echo ""
	echo "[wifi]"
	echo "mode=infrastructure"
	echo "ssid=${WIFI_SSID}"
	[ "${WIFI_HIDDEN:-0}" = "1" ] && echo "hidden=true"
	echo ""
	echo "[wifi-security]"
	echo "key-mgmt=wpa-psk"
	echo "psk=${WIFI_PSK}"
	echo ""
	echo "[ipv4]"
	echo "method=auto"
	echo ""
	echo "[ipv6]"
	echo "method=auto"
} > "${CONN_FILE}"

# NetworkManager REQUIRES 0600 root:root for a keyfile holding a plaintext psk,
# or it ignores the profile.
chmod 600 "${CONN_FILE}"
chown 0:0 "${CONN_FILE}"
echo "wifi: wrote NetworkManager profile for SSID '${WIFI_SSID}'"

# Optional per-wifi.env regulatory country (else os-image/config's WPA_COUNTRY).
# The WLAN radio stays blocked until a country is set.
if [ -n "${WIFI_COUNTRY:-}" ]; then
	on_chroot <<EOF
SUDO_USER="synth" raspi-config nonint do_wifi_country "${WIFI_COUNTRY}" || true
EOF
	echo "wifi: set WLAN country to ${WIFI_COUNTRY}"
fi
