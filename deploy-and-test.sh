#!/bin/bash
set -e

PI="synth@192.168.1.125"
PROJECT="/home/synth/synth"

# Sync code
rsync -avz --exclude '.venv' --exclude '__pycache__' \
  ./ $PI:$PROJECT/

# Install any new system dependencies
ssh $PI "sudo apt-get update -qq && xargs sudo apt-get install -y -qq < $PROJECT/apt-requirements.txt"

# Run tests
ssh $PI "cd $PROJECT && python3 -m pytest tests/ && echo 'ALL TESTS PASSED'"