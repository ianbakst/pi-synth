"""Split a SoundFont into one file per preset.

A .sf2 holds up to 128 programs per bank, and the plugin that plays soundfonts
in mod-host (Fluida) exposes the *file* as a patch property but has no bank or
program parameter — instrument selection inside a font is MIDI Program Change
only, which the control plane has no way to send. So every GM voice would
collapse to program 0.

Splitting sidesteps that: one preset per file means program 0 is always the
right instrument, and each voice names its own font.

Why not SFZ, which sfizz already plays? Because sfizz streams samples from the
SD card, and first-touch SD reads are this project's measured xrun source
(~65/min under sustained play even after the core-affinity fix — see
docs/engine-architecture.md). SoundFonts are loaded whole into RAM, so they
have no such stall. Keeping the format and splitting the file is the option
that doesn't trade a real audio problem for a tidier diagram.

    python3 -m synth_ui.tools.split_soundfont \\
        --input /usr/share/sounds/sf2/FluidR3_GM.sf2 \\
        --out ~/soundfonts/gm --programs 0,1,3,4,5,7,16,17,18,62,63,80,81,89,90

Only the samples a preset actually references are copied, so the pieces are a
fraction of the original rather than 17 copies of a 140MB font.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import struct
import sys
from dataclasses import dataclass

from synth_ui.clients.soundfont import gm_category

# --- SF2 2.x record layouts, all little-endian (see the SoundFont spec) ---
_PHDR = "<20sHHHIII"      # name, preset, bank, bagNdx, library, genre, morphology
_BAG = "<HH"              # genNdx, modNdx
_MOD = "<HHhHH"
_GEN = "<HH"              # oper, amount
_INST = "<20sH"           # name, bagNdx
_SHDR = "<20sIIIIIBbHH"   # name, start, end, startloop, endloop, rate, pitch,
                          # correction, sampleLink, sampleType

GEN_INSTRUMENT = 41       # a preset zone's pointer to an instrument
GEN_SAMPLE_ID = 53        # an instrument zone's pointer to a sample
# The spec requires 46 frames of silence between samples so interpolation can't
# read into a neighbour.
_SAMPLE_GAP = 46
# sfSampleType values that mean "this sample is half of a stereo pair".
_LINKED_TYPES = (2, 4, 8)


@dataclass
class SoundFont:
    info: bytes
    smpl: bytes
    phdr: list
    pbag: list
    pmod: list
    pgen: list
    inst: list
    ibag: list
    imod: list
    igen: list
    shdr: list


def _chunks(buf: bytes, start: int, end: int):
    """Yield (id, payload_start, size) for the RIFF chunks in [start, end)."""
    pos = start
    while pos + 8 <= end:
        cid = buf[pos : pos + 4].decode("latin-1")
        size = struct.unpack_from("<I", buf, pos + 4)[0]
        yield cid, pos + 8, size
        pos += 8 + size + (size & 1)   # chunks are word-aligned


def _records(buf: bytes, start: int, size: int, layout: str) -> list[tuple]:
    n = struct.calcsize(layout)
    return [
        struct.unpack_from(layout, buf, start + i * n) for i in range(size // n)
    ]


def read_sf2(path: str) -> SoundFont:
    with open(path, "rb") as f:
        buf = f.read()
    if buf[:4] != b"RIFF" or buf[8:12] != b"sfbk":
        raise ValueError(f"{path} is not a SoundFont (RIFF/sfbk)")

    tables: dict[str, list] = {}
    info = b""
    smpl = b""
    for cid, cstart, csize in _chunks(buf, 12, len(buf)):
        if cid != "LIST":
            continue
        kind = buf[cstart : cstart + 4].decode("latin-1")
        body, body_end = cstart + 4, cstart + csize
        if kind == "INFO":
            info = buf[body:body_end]
        elif kind == "sdta":
            for sid, sstart, ssize in _chunks(buf, body, body_end):
                if sid == "smpl":
                    smpl = buf[sstart : sstart + ssize]
        elif kind == "pdta":
            layouts = {
                "phdr": _PHDR, "pbag": _BAG, "pmod": _MOD, "pgen": _GEN,
                "inst": _INST, "ibag": _BAG, "imod": _MOD, "igen": _GEN,
                "shdr": _SHDR,
            }
            for pid, pstart, psize in _chunks(buf, body, body_end):
                if pid in layouts:
                    tables[pid] = _records(buf, pstart, psize, layouts[pid])

    missing = {"phdr", "pbag", "pgen", "inst", "ibag", "igen", "shdr"} - set(tables)
    if missing:
        raise ValueError(f"{path}: missing pdta tables {sorted(missing)}")
    return SoundFont(
        info=info, smpl=smpl,
        phdr=tables["phdr"], pbag=tables["pbag"],
        pmod=tables.get("pmod", []), pgen=tables["pgen"],
        inst=tables["inst"], ibag=tables["ibag"],
        imod=tables.get("imod", []), igen=tables["igen"],
        shdr=tables["shdr"],
    )


def presets(sf: SoundFont) -> list[tuple[int, int, str]]:
    """(bank, program, name) for every real preset — the terminal EOP is not one."""
    out = []
    for name, program, bank, _bag, *_ in sf.phdr[:-1]:
        out.append((bank, program, name.split(b"\0")[0].decode("latin-1")))
    return out


def _zone_gens(bags: list, gens: list, first: int, last: int):
    """Yield the generator list of each zone in bag range [first, last)."""
    for j in range(first, last):
        yield gens[bags[j][0] : bags[j + 1][0]]


def _referenced(sf: SoundFont, index: int) -> tuple[list[int], list[int]]:
    """The instruments and samples preset `index` actually uses."""
    inst_ids: list[int] = []
    for zone in _zone_gens(sf.pbag, sf.pgen, sf.phdr[index][3], sf.phdr[index + 1][3]):
        for oper, amount in zone:
            if oper == GEN_INSTRUMENT and amount not in inst_ids:
                inst_ids.append(amount)

    sample_ids: list[int] = []
    for i in inst_ids:
        for zone in _zone_gens(sf.ibag, sf.igen, sf.inst[i][1], sf.inst[i + 1][1]):
            for oper, amount in zone:
                if oper == GEN_SAMPLE_ID and amount not in sample_ids:
                    sample_ids.append(amount)

    # Stereo samples are stored as two mono samples pointing at each other; drop
    # one half and the survivor plays as a broken mono.
    for s in list(sample_ids):
        link, stype = sf.shdr[s][8], sf.shdr[s][9]
        if stype not in _LINKED_TYPES or link in sample_ids:
            continue
        if link < len(sf.shdr) - 1:
            sample_ids.append(link)

    return sorted(inst_ids), sorted(sample_ids)


def _chunk(cid: str, data: bytes) -> bytes:
    pad = b"\0" if len(data) & 1 else b""
    return cid.encode("latin-1") + struct.pack("<I", len(data)) + data + pad


def _pack(layout: str, records: list[tuple]) -> bytes:
    return b"".join(struct.pack(layout, *r) for r in records)


def extract(sf: SoundFont, index: int) -> bytes:
    """Build a new SoundFont containing only preset `index`, as program 0."""
    inst_ids, sample_ids = _referenced(sf, index)
    inst_map = {old: new for new, old in enumerate(inst_ids)}
    sample_map = {old: new for new, old in enumerate(sample_ids)}

    # --- samples: copy only what's referenced, re-basing every offset ---
    smpl = bytearray()
    shdr: list[tuple] = []
    for old in sample_ids:
        name, start, end, sloop, eloop, rate, pitch, corr, link, stype = sf.shdr[old]
        new_start = len(smpl) // 2
        smpl += sf.smpl[start * 2 : end * 2]
        smpl += b"\0" * (_SAMPLE_GAP * 2)
        shift = new_start - start
        shdr.append((
            name, start + shift, end + shift, sloop + shift, eloop + shift,
            rate, pitch, corr, sample_map.get(link, 0), stype,
        ))
    shdr.append((b"EOS", 0, 0, 0, 0, 0, 0, 0, 0, 0))

    # --- instruments ---
    ibag: list[tuple] = []
    igen: list[tuple] = []
    imod: list[tuple] = []
    for old in inst_ids:
        first, last = sf.inst[old][1], sf.inst[old + 1][1]
        for j in range(first, last):
            ibag.append((len(igen), len(imod)))
            for oper, amount in sf.igen[sf.ibag[j][0] : sf.ibag[j + 1][0]]:
                if oper == GEN_SAMPLE_ID:
                    amount = sample_map[amount]
                igen.append((oper, amount))
            imod.extend(sf.imod[sf.ibag[j][1] : sf.ibag[j + 1][1]])
    inst: list[tuple] = []
    cursor = 0
    for old in inst_ids:
        inst.append((sf.inst[old][0], cursor))
        cursor += sf.inst[old + 1][1] - sf.inst[old][1]
    inst.append((b"EOI", len(ibag)))
    ibag.append((len(igen), len(imod)))
    igen.append((0, 0))
    imod.append((0, 0, 0, 0, 0))

    # --- the single preset, forced to bank 0 / program 0 ---
    pbag: list[tuple] = []
    pgen: list[tuple] = []
    pmod: list[tuple] = []
    first, last = sf.phdr[index][3], sf.phdr[index + 1][3]
    for j in range(first, last):
        pbag.append((len(pgen), len(pmod)))
        for oper, amount in sf.pgen[sf.pbag[j][0] : sf.pbag[j + 1][0]]:
            if oper == GEN_INSTRUMENT:
                amount = inst_map[amount]
            pgen.append((oper, amount))
        pmod.extend(sf.pmod[sf.pbag[j][1] : sf.pbag[j + 1][1]])
    name = sf.phdr[index][0]
    phdr = [(name, 0, 0, 0, 0, 0, 0), (b"EOP", 0, 0, len(pbag), 0, 0, 0)]
    pbag.append((len(pgen), len(pmod)))
    pgen.append((0, 0))
    pmod.append((0, 0, 0, 0, 0))

    pdta = b"pdta" + b"".join((
        _chunk("phdr", _pack(_PHDR, phdr)),
        _chunk("pbag", _pack(_BAG, pbag)),
        _chunk("pmod", _pack(_MOD, pmod)),
        _chunk("pgen", _pack(_GEN, pgen)),
        _chunk("inst", _pack(_INST, inst)),
        _chunk("ibag", _pack(_BAG, ibag)),
        _chunk("imod", _pack(_MOD, imod)),
        _chunk("igen", _pack(_GEN, igen)),
        _chunk("shdr", _pack(_SHDR, shdr)),
    ))
    body = b"sfbk" + b"".join((
        _chunk("LIST", b"INFO" + sf.info),
        _chunk("LIST", b"sdta" + _chunk("smpl", bytes(smpl))),
        _chunk("LIST", pdta),
    ))
    return _chunk("RIFF", body)


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "preset"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--input", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--bank", type=int, default=0)
    ap.add_argument("--programs", help="comma-separated; default: every preset")
    ap.add_argument("--list", action="store_true", help="list presets, write nothing")
    ap.add_argument(
        "--manifest",
        help="also write voices.json entries for what was split, to this path. "
        "Curate that file rather than re-splitting: the fonts stay on disk.",
    )
    args = ap.parse_args(argv)

    sf = read_sf2(args.input)
    entries = presets(sf)

    if args.list:
        for bank, program, name in entries:
            print(f"  {bank:3d}:{program:3d}  {name}")
        return 0

    wanted = None
    if args.programs:
        wanted = {int(p) for p in args.programs.split(",") if p.strip()}

    os.makedirs(args.out, exist_ok=True)
    voices = []
    total = 0
    for index, (bank, program, name) in enumerate(entries):
        if bank != args.bank or (wanted is not None and program not in wanted):
            continue
        data = extract(sf, index)
        path = os.path.join(args.out, f"{bank:03d}-{program:03d}-{slug(name)}.sf2")
        with open(path, "wb") as f:
            f.write(data)
        print(f"  {len(data) / 1e6:7.1f} MB  {os.path.basename(path)}  ({name})")
        total += len(data)
        # `engine: fluida` resolves to the plugin URI *and* its confirmed
        # soundfont patch property via lv2.PLUGIN_SPECS. No bank/program: the
        # preset was renumbered to 0, which is the whole reason for splitting.
        voices.append({
            "name": name.strip(),
            "engine": "fluida",
            "path": os.path.abspath(path),
            "category": gm_category(bank, program),
        })

    if not voices:
        print("no presets matched", file=sys.stderr)
        return 1
    print(f"\nwrote {len(voices)} soundfonts ({total / 1e6:.0f} MB) to {args.out}")

    if args.manifest:
        with open(args.manifest, "w") as f:
            json.dump(voices, f, indent=2)
            f.write("\n")
        print(f"wrote {args.manifest} — curate it, then merge into voices.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
