# Upgrading Audio Quality — From SoundFonts to Professional Sound

## Context

This document is for a MIDI instrument built on a Raspberry Pi 4 with:
- PREEMPT_RT kernel (6.12.x, built from `rpi-6.12.y` with `bcm2711_rt_defconfig`)
- PCM5102 I2S DAC on ALSA device `hw:2`
- Audio pinned to CPU cores 2-3 with RT priority 80
- USB MIDI keyboard input
- Touchscreen UI (Pygame) on cores 0-1 for voice selection
- FluidSynth currently running as the audio engine via systemd service
- Communication between UI and audio engine via TCP

The SoundFont-based setup works but sounds amateur. This document covers upgrading to professional-quality sound.

## Architecture Change

Currently: FluidSynth is the only audio engine, controlled via TCP on port 9800.

New approach: Multiple audio engines, each specialized for what it does best. A manager process starts the right engine for the selected voice category.

```
Voice selected on touchscreen
    │
    ▼
Engine Manager (script/service)
    │
    ├── Piano/Sampled instruments → sfizz
    ├── Hammond Organ → setBfree
    ├── FM Synth / DX7 sounds → Dexed (via mod-host)
    ├── General MIDI / fallback → FluidSynth
    └── Physical modeling piano → Pianoteq (commercial)
```

**Critical design rule (unchanged):** The audio engine runs independently of the Python UI. If the UI crashes, audio continues. The architecture from CLAUDE.md still applies — the UI sends commands, it never manages the audio process directly.

## Engine 1: sfizz (Sampled Instruments)

sfizz is an SFZ-format sample player. SFZ is dramatically better than SoundFont — more velocity layers, round-robin, release samples, sympathetic resonance, legato transitions.

### Install

```bash
sudo apt-get install sfizz
```

If not available via apt:

```bash
sudo apt-get install build-essential cmake libsndfile1-dev libjack-jackd2-dev
git clone --recursive https://github.com/sfztools/sfizz.git
cd sfizz
mkdir build && cd build
cmake -DCMAKE_BUILD_TYPE=Release ..
make -j4
sudo make install
```

### Run with ALSA

```bash
chrt -f 80 taskset -c 2,3 sfizz_jack &
# or if using ALSA directly, sfizz can be loaded as an LV2 plugin via a host (see mod-host section)
```

sfizz is primarily an LV2 plugin or a JACK client. For direct ALSA usage without JACK, use it through mod-host (see below).

### Professional SFZ Libraries

#### Salamander Grand Piano (free, ~1.2GB)
The gold standard free piano. 16 velocity layers, release samples.

```bash
mkdir -p ~/instruments/piano
cd ~/instruments/piano
# Download from https://freepats.zenvoid.org/Piano/SalamanderGrandPiano/
# Or mirror: https://archive.org/details/SalamanderGrandPianoV3
# Look for the SFZ version, not the SF2 version
```

After downloading and extracting, you'll have a folder with WAV files and a `.sfz` file. The SFZ file is what you load into sfizz.

#### Piano in 162 (free, ~3GB)
Steinway Model D, 100+ velocity layers per note. Extremely realistic but large.

```bash
mkdir -p ~/instruments/piano
# Download from https://ivyaudio.com/Piano-in-162
```

#### Iowa Piano (free, ~500MB)
Dry Steinway recordings from the University of Iowa Electronic Music Studios. Good for adding your own effects.

```bash
mkdir -p ~/instruments/piano
# Download from http://theremin.music.uiowa.edu/MISpiano.html
# These are raw WAV samples — you'll need an SFZ mapping file
```

#### Other SFZ Libraries Worth Getting

- **Versilian Community Orchestra** — free orchestral instruments, very good quality
- **Karoryfer Samples** — free cello, guitars, choir
- **Virtual Playing Orchestra** — full orchestra SFZ set

Store all instruments in `~/instruments/` organized by category:

```
~/instruments/
├── piano/
│   ├── salamander/
│   │   ├── SalamanderGrandPianoV3.sfz
│   │   └── samples/
│   └── piano-in-162/
├── epiano/
├── organ/
├── synth/
└── strings/
```

## Engine 2: setBfree (Hammond B3 Organ)

setBfree physically models a Hammond B3 tonewheel organ. It computes the actual tonewheel sound generation, key click, drawbar settings, percussion, vibrato/chorus, and a Leslie speaker cabinet with rotating horn and drum. It sounds like a real B3 because it IS computing what a real B3 does.

### Install

```bash
sudo apt-get install setbfree
```

If not available:

```bash
sudo apt-get install build-essential libftgl-dev libglu1-mesa-dev libjack-jackd2-dev lv2-dev
git clone https://github.com/pantherb/setBfree.git
cd setBfree
make
sudo make install
```

### Run

```bash
chrt -f 80 taskset -c 2,3 setBfree -p hw:2
```

setBfree accepts MIDI CC messages for drawbar control, Leslie speed, vibrato, and percussion — map these to your foot controller's expression pedals and switches.

### Key MIDI Mappings for setBfree

- CC 1: Modulation (vibrato depth)
- CC 11: Expression
- CC 64: Sustain
- Drawbar CCs vary — check setBfree documentation
- Leslie speed: typically CC 1 or a dedicated CC

### Configuring setBfree

Create a config file at `~/.setBfreerc`:

```
# Audio output
jack.connect.output.left=system:playback_1
jack.connect.output.right=system:playback_2

# Default drawbar registration (Gospel organ sound)
osc.drawbar.upper=88 8000 000

# Leslie speaker
whirl.speed-preset=chorale
```

setBfree has extensive documentation for all its parameters.

## Engine 3: Dexed (DX7 FM Synthesis)

Dexed is a perfect recreation of the Yamaha DX7 FM synthesizer. It plays actual DX7 patches (SysEx format) — there are over 100,000 DX7 patches available online. Classic 80s electric pianos, bells, basses, pads, and the iconic FM sounds.

### Install as LV2 Plugin

```bash
sudo apt-get install dexed
```

If not available, build from source:

```bash
git clone https://github.com/asb2m10/dexed.git
cd dexed
# Build as LV2 plugin
# (build instructions vary — check the repo's README)
```

### DX7 Patch Libraries

DX7 patches come in SysEx (.syx) files, each containing 32 patches.

- **Download from**: https://yamahablackboxes.com/collection/yamaha-dx7-702-702-patches/
- **Also**: http://dxsysex.com/
- These are the actual patches from the 80s — the same sounds you've heard on thousands of records.

Store in `~/instruments/dx7/`:

```
~/instruments/dx7/
├── rom1a.syx    (original factory patches)
├── rom1b.syx
├── epiano-collection.syx
└── pads-and-strings.syx
```

### Famous DX7 Patches to Look For

- E.PIANO 1 (the iconic 80s electric piano — used on countless records)
- BASS 1 (the slap bass sound)
- TUBULAR BELLS
- LATELY BASS (used by Phil Collins, etc.)

## Engine 4: Pianoteq (Commercial, Optional)

Pianoteq is a physically modeled piano — no samples. It computes string vibrations, hammer mechanics, soundboard resonance, and duplex scale resonance in real time. It's what many touring professionals use.

### Why Consider It

- ~50MB install (no huge sample libraries)
- Every parameter is adjustable (hammer hardness, string length, soundboard material)
- Includes grand pianos, uprights, electric pianos (Rhodes, Wurlitzer), harpsichord, vibraphone, and more
- Runs well on Pi 4
- Stage license (~$100) or Standard (~$250) or Pro (~$450)

### Install

Pianoteq provides an ARM Linux build. Download from https://www.modartt.com/pianoteq and follow their Linux installation guide.

### Run

```bash
chrt -f 80 taskset -c 2,3 pianoteq --headless --midi-channel 1 --audio-device hw:2
```

### Trial Version

There's a free trial that plays every other note silently — enough to evaluate whether you want to buy it.

## Plugin Host: mod-host

mod-host lets you load LV2 plugins (sfizz, Dexed, effects) and connect them together. It runs headless and accepts commands via a socket — perfect for your architecture.

### Install

```bash
sudo apt-get install mod-host
```

If not available:

```bash
sudo apt-get install build-essential libjack-jackd2-dev liblilv-dev
git clone https://github.com/mod-audio/mod-host.git
cd mod-host
make
sudo make install
```

### Usage

Start mod-host:

```bash
chrt -f 80 taskset -c 2,3 mod-host -n -p 5555 &
```

Send commands via socket (from your Python UI or a script):

```bash
# Add a plugin (sfizz)
echo "add http://sfztools.github.io/sfizz 0" | nc localhost 5555

# Set a parameter (load an SFZ file)
echo "param_set 0 <sfz_file_param_id> '/home/synth/instruments/piano/salamander/SalamanderGrandPianoV3.sfz'" | nc localhost 5555

# Connect MIDI input
echo "connect midi_in effect_0:midi_in" | nc localhost 5555

# Connect audio output
echo "connect effect_0:out_left system:playback_1" | nc localhost 5555
echo "connect effect_0:out_right system:playback_2" | nc localhost 5555
```

### Why mod-host Is Powerful

You can chain plugins:

```
MIDI in → sfizz (piano) → reverb plugin → EQ plugin → audio out
```

Each plugin is added and connected via simple socket commands. Your Python UI can build and tear down signal chains on the fly.

## JACK Audio (Required for Most of These)

FluidSynth can run on raw ALSA, but setBfree, sfizz (standalone), and mod-host typically use JACK for audio routing. JACK is a low-latency audio server that sits between your applications and ALSA.

### Install

```bash
sudo apt-get install jackd2
```

### Configure for Low Latency

```bash
chrt -f 90 taskset -c 2,3 jackd -d alsa -d hw:2 -r 48000 -p 128 -n 2 &
```

This starts JACK on your DAC with the same buffer settings you used with FluidSynth. JACK's priority should be higher than the audio engines (90 vs 80) since it's the audio router.

### JACK as a Systemd Service

```ini
[Unit]
Description=JACK Audio Server
After=sound.target
Before=audio-engine.target

[Service]
Type=simple
User=synth
ExecStart=/usr/bin/chrt -f 90 /usr/bin/taskset -c 2,3 /usr/bin/jackd -d alsa -d hw:2 -r 48000 -p 128 -n 2
Restart=always
RestartSec=1

[Install]
WantedBy=multi-user.target
```

## Engine Manager

The engine manager replaces the single FluidSynth service. It starts the appropriate engine based on which voice category is selected.

### Concept

```bash
#!/bin/bash
# /usr/local/bin/engine-manager.sh
#
# Called by the Python UI when switching voice categories.
# Usage: engine-manager.sh <engine> [instrument-path]

ENGINE="$1"
INSTRUMENT="$2"

# Stop any running engine
killall -9 fluidsynth setBfree sfizz pianoteq 2>/dev/null
sleep 0.3

case "$ENGINE" in
    fluidsynth)
        chrt -f 80 taskset -c 2,3 fluidsynth \
            -a jack -m jack \
            -o synth.gain=2.0 \
            -s -i "$INSTRUMENT" &
        ;;
    sfizz)
        # Load via mod-host
        echo "remove 0" | nc -q 0 localhost 5555
        echo "add http://sfztools.github.io/sfizz 0" | nc -q 0 localhost 5555
        echo "param_set 0 sfz_file '$INSTRUMENT'" | nc -q 0 localhost 5555
        ;;
    setbfree)
        chrt -f 80 taskset -c 2,3 setBfree &
        ;;
    dexed)
        # Load via mod-host
        echo "remove 0" | nc -q 0 localhost 5555
        echo "add https://asb2m10.github.io/dexed 0" | nc -q 0 localhost 5555
        ;;
    pianoteq)
        chrt -f 80 taskset -c 2,3 pianoteq --headless \
            --midi-channel 1 &
        ;;
esac

# Reconnect MIDI after engine starts
sleep 1.5
/home/synth/midi-instrument/scripts/midi-connect.sh
```

### Integration with the Touchscreen UI

The Python UI's voice list would be organized by engine:

```
~/instruments/
├── voices.json        # manifest of all available voices
├── piano/
├── epiano/
├── organ/
└── dx7/
```

`voices.json`:

```json
[
    {
        "name": "Salamander Grand",
        "engine": "sfizz",
        "path": "/home/synth/instruments/piano/salamander/SalamanderGrandPianoV3.sfz",
        "category": "Piano"
    },
    {
        "name": "Hammond B3",
        "engine": "setbfree",
        "path": "",
        "category": "Organ"
    },
    {
        "name": "DX7 E.Piano",
        "engine": "dexed",
        "path": "/home/synth/instruments/dx7/rom1a.syx",
        "category": "Electric Piano"
    },
    {
        "name": "General MIDI",
        "engine": "fluidsynth",
        "path": "/home/synth/soundfonts/GeneralUser-GS.sf2",
        "category": "General MIDI"
    }
]
```

The UI reads this manifest, displays voices grouped by category, and calls the engine manager when a voice is selected. The engine manager stops the old engine, starts the new one, and reconnects MIDI.

### Communication Protocol

The current FluidSynth TCP approach (port 9800) is FluidSynth-specific. With multiple engines, you need a more general approach.

**Option A — Engine manager listens on a socket.** The Python UI sends commands like `load sfizz /path/to/file.sfz` to a socket. The engine manager translates these to engine-specific commands.

**Option B — The Python UI calls the engine manager script directly.** Simpler but tightly coupled.

**Option C — mod-host as the universal interface.** mod-host already has a socket interface. The Python UI always talks to mod-host. mod-host loads whatever plugin is needed. This is the cleanest because mod-host handles all the JACK routing, plugin lifecycle, and parameter control through one consistent API.

**Recommendation: Option C with mod-host as the permanent service.** JACK and mod-host run as always-on services. The Python UI sends socket commands to mod-host to load/unload plugins. setBfree and Pianoteq would run as standalone JACK clients managed by a simple wrapper.

## Recommended Implementation Order

1. **Install JACK** and verify audio works through it with your DAC
2. **Install sfizz** and load Salamander Grand Piano — this alone will be a dramatic upgrade
3. **Install setBfree** for organ — test it standalone
4. **Install mod-host** and learn to load plugins via its socket interface
5. **Update the Python UI** to read voices.json and call the engine manager
6. **Install Dexed** for FM sounds
7. **Evaluate Pianoteq** trial if you want the best possible piano

## Things That Must Not Change

- Audio engines run on cores 2-3 with RT priority
- JACK runs at priority 90, engines at priority 80
- The Python UI runs on cores 0-1 and never manages audio processes directly
- MIDI auto-connection runs after every engine switch
- The DAC is ALSA device hw:2
- All engine processes must handle being killed cleanly (SIGTERM then SIGKILL)

## Effects

Once you have mod-host running, you can add effects plugins to any engine:

**Essential LV2 effects plugins:**

```bash
sudo apt-get install calf-plugins        # reverb, EQ, compressor, delay
sudo apt-get install lsp-plugins-lv2     # excellent parametric EQ, compressor
sudo apt-get install guitarix-lv2        # amp sims, cabinet sims
sudo apt-get install zam-plugins         # compressor, EQ, delay
sudo apt-get install dragonfly-reverb    # excellent algorithmic reverbs
```

A typical signal chain:

```
MIDI → Piano plugin → Parametric EQ → Plate Reverb → DAC
```

All controlled via mod-host socket commands. Your Python UI could have an effects section where you add/remove/configure effects in the chain.
