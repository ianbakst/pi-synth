#!/bin/bash
set -e
PI_USER="${PI_USER:-synth}"
PI_HOST="${1:-${PI_HOST:-192.168.1.125}}"
PI="$PI_USER@$PI_HOST"
PROJECT="/home/$PI_USER/synth"

echo "Deploying to $PI:$PROJECT"
rsync -avz --delete \
    --exclude '.venv' --exclude '__pycache__' --exclude '.git' \
    --exclude 'os-image' --exclude 'hardware' --exclude '.claude' \
    --exclude 'soundfonts/*.sf2' --exclude 'soundfonts/*.sf3' \
    ./ "$PI:$PROJECT/"

ssh "$PI" "sudo timedatectl set-ntp true; sleep 2; sudo apt-get update -qq -o Acquire::Check-Valid-Until=false; xargs sudo apt-get install -y -qq -o Acquire::Check-Valid-Until=false < $PROJECT/apt-requirements.txt || true"
ssh "$PI" "cd $PROJECT && pip install -e . --quiet"
ssh "$PI" "cd $PROJECT && python3 -m pytest tests/ -v && echo 'ALL TESTS PASSED'"
ssh "$PI" "sudo systemctl restart synth-ui.service"
echo "Deploy complete."
