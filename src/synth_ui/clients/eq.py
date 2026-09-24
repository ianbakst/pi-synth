"""Equalisers as bands, so any EQ plugin can be drawn and dragged as a curve.

    EqLayout = which of a plugin's controls make up each band
             + how that plugin family turns a band into a response

An EQ plugin is a list of bands — shelves, peaks, high- and low-pass — and each
band is the same four things: on/off, frequency, gain, width. Plugins differ
only in the symbols they use and in their units. Calf states gain as a linear
multiplier and width as Q; x42 fil4 uses dB and octaves of bandwidth. A layout
is that mapping, and supporting another EQ is a table entry here.

## The curve is computed, not read back

No plugin reports its own frequency response over the mod-host socket, so the
curve is calculated from the band settings — the same way each plugin's own GUI
draws it:

- **Calf** builds every band from the RBJ "Audio EQ Cookbook" biquads, with
  its level control as the linear peak gain.
- **fil4** has its own filters: parametric sections after Fons Adriaensen,
  RBJ shelves with its bandwidth mapped to a Q, and a resonant high/low-pass.
  The formulas follow fil4's GUI (gui/fil4.c, update_filter/update_iir/
  get_highpass_response/get_lowpass_response), which draws what its DSP does.

Kept free of pygame so a browser can be sent the same layout and values and
draw the same curve.
"""

from __future__ import annotations

import cmath
import math
from dataclasses import dataclass

# JACK's rate on this box (scripts/start-jack.sh). A biquad's response depends
# on it near the top of the band.
SAMPLE_RATE = 48000.0

LOWSHELF = "lowshelf"
HIGHSHELF = "highshelf"
PEAK = "peak"
HIGHPASS = "highpass"
LOWPASS = "lowpass"

DB = "db"
LINEAR = "linear"

# How a plugin family computes a band.
RBJ = "rbj"
FIL4 = "fil4"

# The graph's range. Every EQ here can reach further (Calf ±36 dB), but past
# ±18 a drag on a 480-pixel panel is too coarse to be useful; the knob beneath
# still reaches the plugin's full range.
DB_RANGE = 18.0
FREQ_LO = 20.0
FREQ_HI = 20000.0


@dataclass(frozen=True)
class Band:
    kind: str
    label: str          # shown on the band's handle: "L", "1", "HP"
    enable: str         # control symbols
    freq: str
    gain: str = ""      # none for high/low-pass
    width: str = ""

    @property
    def has_gain(self) -> bool:
        return bool(self.gain)

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(s for s in (self.enable, self.freq, self.gain, self.width) if s)


@dataclass(frozen=True)
class EqLayout:
    bands: tuple[Band, ...]
    model: str
    gain_unit: str = DB
    # Controls with no effect on the sound — a GUI's zoom, a meter's reset.
    hidden: tuple[str, ...] = ()

    @property
    def band_symbols(self) -> set[str]:
        return {s for band in self.bands for s in band.symbols}

    # --- units ------------------------------------------------------------

    def gain_db(self, band: Band, values: dict[str, float]) -> float:
        if not band.has_gain:
            return 0.0
        value = values[band.gain]
        if self.gain_unit == LINEAR:
            return 20.0 * math.log10(max(value, 1e-6))
        return value

    def gain_value(self, db: float) -> float:
        """A gain in dB, in the plugin's own unit."""
        return 10.0 ** (db / 20.0) if self.gain_unit == LINEAR else db

    # --- response ---------------------------------------------------------

    def band_db(self, band: Band, values: dict[str, float], freq: float) -> float:
        """One band's contribution at `freq`, in dB. Zero when it's off."""
        if not is_enabled(band, values):
            return 0.0
        f0 = values[band.freq]
        width = values[band.width] if band.width else 0.0
        gain = self.gain_db(band, values)
        if self.model == FIL4:
            return _fil4_db(band.kind, f0, gain, width, freq)
        return _rbj_db(band.kind, f0, gain, width or math.sqrt(0.5), freq)

    def response(self, values: dict[str, float], freqs: list[float]) -> list[float]:
        """The whole EQ at each frequency, in dB: the bands are in series, so
        their dB add."""
        return [sum(self.band_db(b, values, f) for b in self.bands) for f in freqs]


def is_enabled(band: Band, values: dict[str, float]) -> bool:
    """Non-zero is on. Calf's band switch is Off/On/Left/Right/Mid/Side, and
    every setting but Off is some form of on."""
    return values.get(band.enable, 0.0) >= 0.5


def log_freqs(count: int, lo: float = FREQ_LO, hi: float = FREQ_HI) -> list[float]:
    """`count` frequencies evenly spaced in octaves — one per pixel column or
    so of a log-frequency axis."""
    step = math.log(hi / lo) / max(1, count - 1)
    return [lo * math.exp(step * i) for i in range(count)]


# --- RBJ cookbook biquads (Calf) -------------------------------------------

def _biquad_db(b: tuple[float, float, float], a: tuple[float, float, float],
               freq: float) -> float:
    z = cmath.exp(-1j * 2.0 * math.pi * freq / SAMPLE_RATE)
    h = (b[0] + b[1] * z + b[2] * z * z) / (a[0] + a[1] * z + a[2] * z * z)
    return 20.0 * math.log10(max(abs(h), 1e-9))


def _rbj(kind: str, f0: float, gain_db: float, q: float):
    f0 = min(f0, SAMPLE_RATE * 0.49)
    w0 = 2.0 * math.pi * f0 / SAMPLE_RATE
    cos, sin = math.cos(w0), math.sin(w0)
    alpha = sin / (2.0 * max(q, 1e-3))
    a_ = 10.0 ** (gain_db / 40.0)
    if kind == PEAK:
        return (
            (1 + alpha * a_, -2 * cos, 1 - alpha * a_),
            (1 + alpha / a_, -2 * cos, 1 - alpha / a_),
        )
    if kind in (LOWSHELF, HIGHSHELF):
        s = 2 * math.sqrt(a_) * alpha
        if kind == LOWSHELF:
            return (
                (a_ * ((a_ + 1) - (a_ - 1) * cos + s),
                 2 * a_ * ((a_ - 1) - (a_ + 1) * cos),
                 a_ * ((a_ + 1) - (a_ - 1) * cos - s)),
                ((a_ + 1) + (a_ - 1) * cos + s,
                 -2 * ((a_ - 1) + (a_ + 1) * cos),
                 (a_ + 1) + (a_ - 1) * cos - s),
            )
        return (
            (a_ * ((a_ + 1) + (a_ - 1) * cos + s),
             -2 * a_ * ((a_ - 1) + (a_ + 1) * cos),
             a_ * ((a_ + 1) + (a_ - 1) * cos - s)),
            ((a_ + 1) - (a_ - 1) * cos + s,
             2 * ((a_ - 1) - (a_ + 1) * cos),
             (a_ + 1) - (a_ - 1) * cos - s),
        )
    if kind == LOWPASS:
        return (
            ((1 - cos) / 2, 1 - cos, (1 - cos) / 2),
            (1 + alpha, -2 * cos, 1 - alpha),
        )
    return (  # HIGHPASS
        ((1 + cos) / 2, -(1 + cos), (1 + cos) / 2),
        (1 + alpha, -2 * cos, 1 - alpha),
    )


def _rbj_db(kind: str, f0: float, gain_db: float, q: float, freq: float) -> float:
    b, a = _rbj(kind, f0, gain_db, q)
    return _biquad_db(b, a, freq)


# --- fil4 --------------------------------------------------------------------

def _fil4_section_db(f0: float, gain_db: float, bw: float, freq: float) -> float:
    """A parametric section (fil4 src/filters.h), bandwidth in octaves."""
    ratio = min(max(f0 / SAMPLE_RATE, 0.0002), 0.4998)
    g = 10.0 ** (0.05 * gain_db)
    b = 7.0 * bw * ratio / math.sqrt(g)
    s2 = (1.0 - b) / (1.0 + b)
    s1 = -math.cos(2 * math.pi * ratio) * (1.0 + s2)
    gd = 0.5 * (g - 1.0) * (1.0 - s2)

    w = 2 * math.pi * freq / SAMPLE_RATE
    x = math.cos(2 * w) + s1 * math.cos(w) + s2
    y = math.sin(2 * w) + s1 * math.sin(w)
    t1 = math.hypot(x, y)
    t2 = math.hypot(x + gd * (math.cos(2 * w) - 1.0), y + gd * math.sin(2 * w))
    return 20.0 * math.log10(max(t2, 1e-9) / max(t1, 1e-9))


def _fil4_shelf_db(kind: str, f0: float, gain_db: float, bw: float,
                   freq: float) -> float:
    """An RBJ shelf, with fil4's bandwidth mapped to a Q (gui/fil4.c
    update_iir: 2^-4..2^2 octaves onto Q 2^-1.5..2^0.5)."""
    q = min(max(0.2129 + bw / 2.25, 0.25), 2.0)
    f0 = min(max(f0, SAMPLE_RATE * 0.0004), SAMPLE_RATE * 0.47)
    return _rbj_db(kind, f0, gain_db, q, freq)


def _fil4_highpass_db(f0: float, resonance: float, freq: float) -> float:
    f0 = min(max(f0, 5.0), SAMPLE_RATE / 12.0)
    r = min(max(0.7 + 0.78 * math.tanh(1.82 * (resonance - 0.8)), 0.0), 1.6)
    q = 3.01 * math.sqrt(r / (r + 2)) if r < 1.3 else math.sqrt(4 - 0.09 / (r - 1.09))
    wr = f0 / freq
    return -10.0 * math.log10(max((1 + wr * wr) ** 2 - (q * wr) ** 2, 1e-9))


def _fil4_lowpass_db(f0: float, resonance: float, freq: float) -> float:
    f0 = min(max(f0, SAMPLE_RATE * 0.0002), SAMPLE_RATE * 0.4998)
    r = 3.0 * resonance ** 3.20772
    q = math.sqrt(4.0 * r / (1.0 + r))
    w = math.sin(math.pi * freq / SAMPLE_RATE)
    wc = math.sin(math.pi * f0 / SAMPLE_RATE)
    ratio = w / wc
    db = -10.0 * math.log10(max((1 + ratio * ratio) ** 2 - (q * ratio) ** 2, 1e-9))
    # fil4 always follows its low-pass with a fixed -6 dB shelf at fs/3.
    return db + _fil4_shelf_db(HIGHSHELF, SAMPLE_RATE / 3.0, -6.0, 0.5, freq)


def _fil4_db(kind: str, f0: float, gain_db: float, width: float,
             freq: float) -> float:
    if kind == PEAK:
        return _fil4_section_db(f0, gain_db, width, freq)
    if kind in (LOWSHELF, HIGHSHELF):
        return _fil4_shelf_db(kind, f0, gain_db, width, freq)
    if kind == HIGHPASS:
        return _fil4_highpass_db(f0, width, freq)
    return _fil4_lowpass_db(f0, width, freq)


# --- the plugins -------------------------------------------------------------

_CALF = "http://calf.sourceforge.net/plugins/"
_FIL4 = "http://gareus.org/oss/lv2/fil4#"

_CALF_EQ5 = EqLayout(
    model=RBJ,
    gain_unit=LINEAR,
    bands=(
        Band(LOWSHELF, "L", "ls_active", "ls_freq", "ls_level", "ls_q"),
        Band(PEAK, "1", "p1_active", "p1_freq", "p1_level", "p1_q"),
        Band(PEAK, "2", "p2_active", "p2_freq", "p2_level", "p2_q"),
        Band(PEAK, "3", "p3_active", "p3_freq", "p3_level", "p3_q"),
        Band(HIGHSHELF, "H", "hs_active", "hs_freq", "hs_level", "hs_q"),
    ),
    # Both only change what Calf's own GUI draws.
    hidden=("individuals", "zoom"),
)

# Mono and stereo share every control symbol (checked on the board).
_FIL4_LAYOUT = EqLayout(
    model=FIL4,
    gain_unit=DB,
    bands=(
        Band(HIGHPASS, "HP", "HighPass", "HPfreq", width="HPQ"),
        Band(LOWSHELF, "L", "LSsec", "LSfreq", "LSgain", "LSq"),
        Band(PEAK, "1", "sec1", "freq1", "gain1", "q1"),
        Band(PEAK, "2", "sec2", "freq2", "gain2", "q2"),
        Band(PEAK, "3", "sec3", "freq3", "gain3", "q3"),
        Band(PEAK, "4", "sec4", "freq4", "gain4", "q4"),
        Band(HIGHSHELF, "H", "HSsec", "HSfreq", "HSgain", "HSq"),
        Band(LOWPASS, "LP", "LowPass", "LPfreq", width="LPQ"),
    ),
    # Resets the peak meter in fil4's GUI; it does nothing to the audio.
    hidden=("peakreset",),
)

EQ_LAYOUTS: dict[str, EqLayout] = {
    _CALF + "Equalizer5Band": _CALF_EQ5,
    _FIL4 + "mono": _FIL4_LAYOUT,
    _FIL4 + "stereo": _FIL4_LAYOUT,
}


def eq_layout_for(uri: str, symbols: set[str] | None = None) -> EqLayout | None:
    """The EQ layout for a plugin, or None if it isn't one we can draw.

    Given the symbols the plugin actually has, a layout naming one it doesn't
    is refused rather than drawn: a band bound to a missing control would send
    values mod-host accepts and ignores, which is the silent failure the params
    screen exists to avoid.
    """
    layout = EQ_LAYOUTS.get(uri)
    if layout is None:
        return None
    if symbols is not None and not layout.band_symbols <= symbols:
        return None
    return layout
