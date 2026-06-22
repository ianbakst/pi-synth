# Bill of Materials

Master parts list to build one MIDI instrument. Quantities are per unit.

Prices are rough placeholders (USD) — update with your actual sources. For the
components *on* the custom audio board, see the board-level BOM:
[pcb/pi-audio-hat/bom/](pcb/pi-audio-hat/bom/). Wiring/pinout reference lives in
[../CLAUDE.md](../CLAUDE.md) (Hardware section).

## Core electronics

| Qty | Component | Spec / Notes | Source / Part # | Approx. Cost |
|-----|-----------|--------------|-----------------|--------------|
| 1 | Raspberry Pi 4 Model B | 4GB recommended (2GB min; 8GB for large sample sets) | TBD | ~$100 |
| 1 | microSD card | 32GB+, A1/A2, Pi OS Lite 64-bit | https://a.co/d/0dI9rsD4 | ~$10 |
| 1 | PCM5102A I2S DAC module | Teyleten Robot PCM5102A; SCK pad shorted to GND | https://a.co/d/094ECi6M | ~$13 (for 3) |
| 1 | `pi-audio-hat` carrier board | Custom PCB that mounts the PCM5102A module onto the Pi; components in PCB BOM | see PCB BOM | TBD |
| 2 | Audio transformer | 600:600 for audio isolation and balanced conversion | https://a.co/d/03pa9yuA | ~$10 (for 10) |

## Display & input

| Qty | Component | Spec / Notes | Source / Part # | Approx. Cost |
|-----|-----------|--------------|-----------------|--------------|
| 1 | Touchscreen | 4.3", 800×480, ft5x06 capacitive controller | https://a.co/d/042zTxz3 | ~$45 |
| 1 | USB MIDI keyboard | Class-compliant USB MIDI | user-supplied | — |

## Enclosure & mechanical

| Qty | Component | Spec / Notes | Source / Part # | Approx. Cost |
|-----|-----------|--------------|-----------------|--------------|
| 1 | Enclosure | 3D-printed, see [enclosure/enclosure.stl](enclosure/enclosure.stl) | self-printed | filament |
| 1 | Heatsink + fan | For Pi 4 — prevents thermal throttling under sustained audio load | TBD | ~$8 |
| — | Fasteners | M2.5 standoffs/screws for Pi + board + screen mounting | TBD | ~$5 |

## Wiring & connectors

| Qty | Component | Spec / Notes | Source / Part # | Approx. Cost |
|-----|-----------|--------------|-----------------|--------------|
| 2 | XLR male socket | male. one left, one right for balanced output | TBD | ~$3 |
| 1 | Midi female socket |
| 1 | switch | ground lift switch |
| 1 | usb-c power port | Could be any one, but this one was measured to fit | 
| 1 | usb-c vertical breakout board |
| 1 | Header pins / socket | To seat the PCM5102A module on the `pi-audio-hat` and the HAT on the Pi's 40-pin GPIO | TBD | ~$3 |

---

**Notes**
- The `pi-audio-hat` is a **carrier/mount** for the PCM5102A module — you need
  both. The module provides the DAC; the HAT seats it on the Pi and carries the
  board's own components (listed in the PCB BOM).
- DAC wiring (BCK/DIN/LCK → GPIO18/21/19) and boot config (`dtoverlay=hifiberry-dac`)
  are documented in [../CLAUDE.md](../CLAUDE.md), not duplicated here.
