# RT kernel artifacts

The image bakes a **PREEMPT_RT kernel harvested from a working RT board** (there
is no official prebuilt RT kernel to download for Raspberry Pi OS). Drop the
harvested files here — they are **not** committed (`os-image/.gitignore` excludes
them at any depth under `kernel/`; they're large and board-specific). Use Git LFS
if you want them tracked.

Layout is per-board, selected by `PI_SYNTH_BOARD` (`pi4` default, or `cm5`) when
running `../build.sh` — see its usage comment. **pi4** keeps the original flat
layout (unchanged, for backward compatibility); **cm5** lives in its own
subdirectory since it needs a different kernel filename and is a different SoC
generation (BCM2712, not BCM2711 — a from-scratch RT build, not a re-harvest of
the pi4 one).

## Expected layout

```
kernel/
├── kernel8.img            # pi4 RT kernel image                 (required for pi4)
├── modules/<KVER>/        # pi4 /lib/modules/<KVER> tree         (required for pi4)
├── dtb/*.dtb               # pi4 device trees                    (optional)
├── overlays/*.dtbo         # pi4 overlays                        (optional)
└── cm5/
    ├── kernel_2712.img     # cm5 RT kernel image                 (required for cm5)
    ├── modules/<KVER>/     # cm5 /lib/modules/<KVER> tree         (required for cm5)
    ├── dtb/*.dtb            # cm5 device trees                    (optional)
    └── overlays/*.dtbo      # cm5 overlays                        (optional)
```

`<KVER>` is your kernel version string (`uname -r` on the board), e.g.
`6.12.30-rt-v8+` (pi4) or `6.12.96-v8-16k+` (cm5 — note PREEMPT_RT doesn't
always appear in the version *string* itself; confirm with `uname -v` instead,
which should contain `PREEMPT_RT`). The modules tree **must** match the kernel
image, or the kernel boots but half its drivers won't load — always key off
`uname -r`.

Note the boot filename genuinely differs by SoC generation, not just by
convention: BCM2711-and-earlier (pi4) boards default to `kernel8.img`;
BCM2712 (Pi 5/CM5-generation) boards default to `kernel_2712.img`. Installing
under the wrong filename means the firmware silently keeps booting whatever it
already had — stage `01-realtime-kernel` installs to the correct name per
board automatically, but a manual local install (to test-boot before
harvesting) needs the right name too.

## How to harvest (run on your Mac)

### pi4 (flat layout, unchanged)

```bash
PI=synth@192.168.1.125                 # your Pi's user@IP
KVER="$(ssh "$PI" uname -r)"
cd os-image/kernel

scp "$PI:/boot/firmware/kernel8.img" ./kernel8.img
mkdir -p dtb overlays
scp "$PI:/boot/firmware/*.dtb"           ./dtb/
scp "$PI:/boot/firmware/overlays/*.dtbo" ./overlays/
# rsync (not scp -r): copies the dangling build/source symlinks safely
rsync -a --exclude=build --exclude=source "$PI:/lib/modules/$KVER" ./modules/
```

### cm5 (subdirectory layout)

```bash
PI=synth@192.168.1.126                 # your CM5's user@IP
KVER="$(ssh "$PI" uname -r)"
mkdir -p os-image/kernel/cm5
cd os-image/kernel/cm5

scp "$PI:/boot/firmware/kernel_2712.img" ./kernel_2712.img
mkdir -p dtb overlays
scp "$PI:/boot/firmware/bcm2712*.dtb"    ./dtb/
scp "$PI:/boot/firmware/overlays/*.dtbo" ./overlays/
rsync -a --exclude=build --exclude=source "$PI:/lib/modules/$KVER" ./modules/
```

## How it's used

Stage `01-realtime-kernel` overwrites the board's default kernel filename
(`kernel8.img` for pi4, `kernel_2712.img` for cm5 — the base image already sets
`arm_64bit=1` with no custom `kernel=`, so the firmware loads the default by
name — no `config.txt` change needed) with your RT image, installs the modules
tree to `/lib/modules/<KVER>`, refreshes the dtbs/overlays if present, and runs
`depmod -a <KVER>` in-chroot to regenerate dependency metadata.

To confirm after flashing: `uname -v` on the booted board should contain
`PREEMPT_RT`, and `lsmod` should show your drivers loading.

## Custom kernel name?

If the board's `config.txt` set a custom `kernel=` (check with
`grep kernel= /boot/firmware/config.txt`), the harvested image may not be named
`kernel8.img`/`kernel_2712.img` — stage `01` still installs the single `*.img`
it finds in the board's kernel directory under that board's expected default
name, which the firmware boots by default absent a `kernel=` override.
