// The EQ curve, computed in the browser so it follows a drag without a round
// trip over WiFi. A line-for-line port of clients/eq.py — which explains the
// formulas and where they come from — held to it by tests/test_web_eq.py,
// which runs both on the same settings and compares.
(function (root) {
  "use strict";

  const TAU = 2 * Math.PI;

  function isEnabled(band, values) {
    return (values[band.enable] || 0) >= 0.5;
  }

  function gainDb(layout, band, values) {
    if (!band.gain) return 0;
    const v = values[band.gain];
    return layout.gain_unit === "linear" ? 20 * Math.log10(Math.max(v, 1e-6)) : v;
  }

  function gainValue(layout, db) {
    return layout.gain_unit === "linear" ? Math.pow(10, db / 20) : db;
  }

  // |H(e^jw)| of a biquad, in dB.
  function biquadDb(b, a, freq, fs) {
    const w = TAU * freq / fs;
    // z^-1 and z^-2 as (re, im).
    const c1 = Math.cos(w), s1 = -Math.sin(w);
    const c2 = Math.cos(2 * w), s2 = -Math.sin(2 * w);
    const nr = b[0] + b[1] * c1 + b[2] * c2, ni = b[1] * s1 + b[2] * s2;
    const dr = a[0] + a[1] * c1 + a[2] * c2, di = a[1] * s1 + a[2] * s2;
    const mag = Math.sqrt((nr * nr + ni * ni) / (dr * dr + di * di));
    return 20 * Math.log10(Math.max(mag, 1e-9));
  }

  function rbj(kind, f0, gain, q, fs) {
    f0 = Math.min(f0, fs * 0.49);
    const w0 = TAU * f0 / fs;
    const cos = Math.cos(w0), sin = Math.sin(w0);
    const alpha = sin / (2 * Math.max(q, 1e-3));
    const A = Math.pow(10, gain / 40);
    if (kind === "peak") {
      return [[1 + alpha * A, -2 * cos, 1 - alpha * A],
              [1 + alpha / A, -2 * cos, 1 - alpha / A]];
    }
    if (kind === "lowshelf" || kind === "highshelf") {
      const s = 2 * Math.sqrt(A) * alpha;
      if (kind === "lowshelf") {
        return [[A * ((A + 1) - (A - 1) * cos + s),
                 2 * A * ((A - 1) - (A + 1) * cos),
                 A * ((A + 1) - (A - 1) * cos - s)],
                [(A + 1) + (A - 1) * cos + s,
                 -2 * ((A - 1) + (A + 1) * cos),
                 (A + 1) + (A - 1) * cos - s]];
      }
      return [[A * ((A + 1) + (A - 1) * cos + s),
               -2 * A * ((A - 1) + (A + 1) * cos),
               A * ((A + 1) + (A - 1) * cos - s)],
              [(A + 1) - (A - 1) * cos + s,
               2 * ((A - 1) - (A + 1) * cos),
               (A + 1) - (A - 1) * cos - s]];
    }
    if (kind === "lowpass") {
      return [[(1 - cos) / 2, 1 - cos, (1 - cos) / 2],
              [1 + alpha, -2 * cos, 1 - alpha]];
    }
    return [[(1 + cos) / 2, -(1 + cos), (1 + cos) / 2],
            [1 + alpha, -2 * cos, 1 - alpha]];
  }

  function rbjDb(kind, f0, gain, q, freq, fs) {
    const [b, a] = rbj(kind, f0, gain, q, fs);
    return biquadDb(b, a, freq, fs);
  }

  // --- fil4 ---------------------------------------------------------------

  function fil4SectionDb(f0, gain, bw, freq, fs) {
    const ratio = Math.min(Math.max(f0 / fs, 0.0002), 0.4998);
    const g = Math.pow(10, 0.05 * gain);
    const b = 7 * bw * ratio / Math.sqrt(g);
    const s2 = (1 - b) / (1 + b);
    const s1 = -Math.cos(TAU * ratio) * (1 + s2);
    const gd = 0.5 * (g - 1) * (1 - s2);
    const w = TAU * freq / fs;
    const x = Math.cos(2 * w) + s1 * Math.cos(w) + s2;
    const y = Math.sin(2 * w) + s1 * Math.sin(w);
    const t1 = Math.hypot(x, y);
    const t2 = Math.hypot(x + gd * (Math.cos(2 * w) - 1), y + gd * Math.sin(2 * w));
    return 20 * Math.log10(Math.max(t2, 1e-9) / Math.max(t1, 1e-9));
  }

  function fil4ShelfDb(kind, f0, gain, bw, freq, fs) {
    const q = Math.min(Math.max(0.2129 + bw / 2.25, 0.25), 2.0);
    f0 = Math.min(Math.max(f0, fs * 0.0004), fs * 0.47);
    return rbjDb(kind, f0, gain, q, freq, fs);
  }

  function fil4HighpassDb(f0, res, freq, fs) {
    f0 = Math.min(Math.max(f0, 5), fs / 12);
    const r = Math.min(Math.max(0.7 + 0.78 * Math.tanh(1.82 * (res - 0.8)), 0), 1.6);
    const q = r < 1.3 ? 3.01 * Math.sqrt(r / (r + 2)) : Math.sqrt(4 - 0.09 / (r - 1.09));
    const wr = f0 / freq;
    return -10 * Math.log10(Math.max(Math.pow(1 + wr * wr, 2) - Math.pow(q * wr, 2), 1e-9));
  }

  function fil4LowpassDb(f0, res, freq, fs) {
    f0 = Math.min(Math.max(f0, fs * 0.0002), fs * 0.4998);
    const r = 3 * Math.pow(res, 3.20772);
    const q = Math.sqrt(4 * r / (1 + r));
    const ratio = Math.sin(Math.PI * freq / fs) / Math.sin(Math.PI * f0 / fs);
    const db = -10 * Math.log10(
      Math.max(Math.pow(1 + ratio * ratio, 2) - Math.pow(q * ratio, 2), 1e-9));
    return db + fil4ShelfDb("highshelf", fs / 3, -6, 0.5, freq, fs);
  }

  function fil4Db(kind, f0, gain, width, freq, fs) {
    if (kind === "peak") return fil4SectionDb(f0, gain, width, freq, fs);
    if (kind === "lowshelf" || kind === "highshelf") {
      return fil4ShelfDb(kind, f0, gain, width, freq, fs);
    }
    if (kind === "highpass") return fil4HighpassDb(f0, width, freq, fs);
    return fil4LowpassDb(f0, width, freq, fs);
  }

  // --- the layout ---------------------------------------------------------

  function bandDb(layout, band, values, freq) {
    if (!isEnabled(band, values)) return 0;
    const f0 = values[band.freq];
    const width = band.width ? values[band.width] : 0;
    const gain = gainDb(layout, band, values);
    const fs = layout.sample_rate;
    if (layout.model === "fil4") return fil4Db(band.kind, f0, gain, width, freq, fs);
    return rbjDb(band.kind, f0, gain, width || Math.SQRT1_2, freq, fs);
  }

  function response(layout, values, freqs) {
    return freqs.map((f) =>
      layout.bands.reduce((sum, band) => sum + bandDb(layout, band, values, f), 0));
  }

  function logFreqs(count, lo, hi) {
    const step = Math.log(hi / lo) / Math.max(1, count - 1);
    return Array.from({ length: count }, (_, i) => lo * Math.exp(step * i));
  }

  const api = { isEnabled, gainDb, gainValue, bandDb, response, logFreqs };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.SynthEQ = api;
})(this);
