#!/bin/bash
set -e
PI_USER="${PI_USER:-synth}"
PI_HOST="${1:-${PI_HOST:-192.168.1.148}}"
PI="$PI_USER@$PI_HOST"
PROJECT="/home/$PI_USER/synth"

# --- one authentication for the whole deploy ---
# Every ssh/rsync below used to open its own connection, and each one prompted
# for the password — five times per deploy. Instead, open a single master
# connection up front and have everything else ride on it (OpenSSH connection
# multiplexing). You authenticate once; the rest reuse the socket and are faster
# too, since they skip the handshake.
#
# Zero prompts: install a key once with `ssh-copy-id synth@synth.local`.
#
# %C is a hash of the connection details: short (unix sockets are length-limited)
# and unique per host, so deploying to two boards can't cross wires.
SSH_OPTS=(-o ControlMaster=auto -o "ControlPath=/tmp/synth-deploy-%C" -o ControlPersist=120)
ssh "${SSH_OPTS[@]}" -fN "$PI"
trap 'ssh "${SSH_OPTS[@]}" -O exit "$PI" 2>/dev/null || true' EXIT

echo "Deploying to $PI:$PROJECT"
rsync -avz --delete -e "ssh ${SSH_OPTS[*]}" \
    --exclude '.venv' --exclude '__pycache__' --exclude '.git' \
    --exclude 'os-image' --exclude 'hardware' --exclude '.claude' \
    --exclude 'soundfonts/*.sf2' --exclude 'soundfonts/*.sf3' \
    ./ "$PI:$PROJECT/"

# grep -v '^#': apt-requirements.txt is commented, and xargs would otherwise hand
# apt the comment words as package names — which fails the whole install, not
# just the bad line.
ssh "${SSH_OPTS[@]}" "$PI" "sudo timedatectl set-ntp true; sleep 2; sudo apt-get update -qq -o Acquire::Check-Valid-Until=false; grep -v '^#' $PROJECT/apt-requirements.txt | xargs sudo apt-get install -y -qq -o Acquire::Check-Valid-Until=false || true"
# ssh "${SSH_OPTS[@]}" "$PI" "cd $PROJECT && python3 -m pytest tests/ -v && echo 'ALL TESTS PASSED'"

# --- things rsync alone doesn't put where the running system reads them ---
#
# 1) systemd units. The repo's systemd/ is the source of truth, but rsync only
#    lands it in the project dir; the running system reads /etc/systemd/system.
#    Installing by hand was forgotten twice, once leaving mod-host without
#    PartOf=jack.service (so it stayed wired to a dead server after an audio
#    device change).
# 2) voices.json. The app reads ~/instruments/voices.json (config.VOICES_MANIFEST),
#    NOT the copy under the project dir that rsync updates. Every voice change
#    needed a manual cp to take effect.
#
# Content-compared with cmp, so daemon-reload is skipped on an unchanged deploy.
ssh "${SSH_OPTS[@]}" "$PI" "bash -s" << EOF
set -e
changed=0
for unit in $PROJECT/systemd/*.service; do
    name=\$(basename "\$unit")
    if ! cmp -s "\$unit" "/etc/systemd/system/\$name"; then
        sudo install -m 644 "\$unit" "/etc/systemd/system/\$name"
        echo "installed \$name"
        changed=1
    fi
done
[ "\$changed" = 1 ] && sudo systemctl daemon-reload || true

mkdir -p ~/instruments
if ! cmp -s "$PROJECT/instruments/voices.json" ~/instruments/voices.json; then
    cp "$PROJECT/instruments/voices.json" ~/instruments/voices.json
    echo "updated ~/instruments/voices.json"
fi
EOF

ssh "${SSH_OPTS[@]}" "$PI" "sudo systemctl restart synth-ui.service"

# The Pi has no git checkout, so nothing over there can say what it's running --
# and rsync sends the working tree, so "what's deployed" isn't a commit either.
# Record it here.
REMOTE_HOST=$(ssh "${SSH_OPTS[@]}" "$PI" hostname)
BRANCH=$(git rev-parse --abbrev-ref HEAD)
COMMIT=$(git rev-parse --short HEAD)
git diff --quiet || COMMIT="$COMMIT+dirty"
echo "Deploy complete -> ${REMOTE_HOST} (${PI_HOST}), ${BRANCH} ${COMMIT}"
