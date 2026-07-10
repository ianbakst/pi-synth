# RT kernel artifacts

The image bakes a **PREEMPT_RT kernel harvested from a working RT Pi** (there is
no official prebuilt RT kernel to download for Raspberry Pi OS). Drop the
harvested files here — they are **not** committed (`os-image/.gitignore` excludes
them; they're large and board-specific). Use Git LFS if you want them tracked.

## Expected layout

```
kernel/
├── kernel8.img          # the RT kernel image                (required)
├── modules/<KVER>/      # the /lib/modules/<KVER> tree        (required)
├── dtb/*.dtb            # device trees                        (optional)
└── overlays/*.dtbo      # overlays                            (optional)
```

`<KVER>` is your kernel version string (`uname -r` on the Pi), e.g.
`6.12.30-rt-v8+`. The modules tree **must** match `kernel8.img`, or the kernel
boots but half its drivers won't load — always key off `uname -r`.

## How to harvest (run on your Mac)

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

## How it's used

Stage `01-realtime-kernel` overwrites `/boot/firmware/kernel8.img` with your RT
image (the base image already sets `arm_64bit=1` with no custom `kernel=`, so the
firmware loads it by default — no `config.txt` change needed), installs the
modules tree to `/lib/modules/<KVER>`, refreshes the dtbs/overlays if present,
and runs `depmod -a <KVER>` in-chroot to regenerate dependency metadata.

To confirm after flashing: `uname -v` on the booted Pi should contain
`PREEMPT_RT`, and `lsmod` should show your drivers loading.

## Custom kernel name?

If your Pi's `config.txt` set a custom `kernel=` (check with
`grep kernel= /boot/firmware/config.txt`), the harvested image may not be named
`kernel8.img` — stage `01` still installs the single `*.img` it finds here as
`kernel8.img`, which the 64-bit firmware boots by default.
