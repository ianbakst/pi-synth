#!/bin/bash
# Wait for FluidSynth to be ready
sleep 1

FLUID_CLIENT=$(aconnect -l | grep -i "FLUID" | head -1 | sed 's/client \([0-9]*\).*/\1/')

if [ -z "$FLUID_CLIENT" ]; then
    echo "FluidSynth not found in ALSA sequencer"
    exit 1
fi

aconnect -l | grep "^client" | while read line; do
    CLIENT_NUM=$(echo "$line" | sed 's/client \([0-9]*\).*/\1/')
    CLIENT_NAME=$(echo "$line" | sed "s/client [0-9]*: '\(.*\)'.*/\1/")

    if [ "$CLIENT_NUM" -gt 15 ] && [ "$CLIENT_NUM" != "$FLUID_CLIENT" ]; then
        echo "Connecting $CLIENT_NAME ($CLIENT_NUM:0) to FluidSynth ($FLUID_CLIENT:0)"
        aconnect $CLIENT_NUM:0 $FLUID_CLIENT:0
    fi
done
