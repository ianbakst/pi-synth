"""The browser's EQ curve (web/eq.js) must match clients/eq.py.

Two copies of the same formulas, kept honest by running both on the same
settings. Needs Node; skipped where there is none (the board).
"""

import json
import os
import shutil
import subprocess

import pytest

from synth_ui.clients.eq import EQ_LAYOUTS, log_freqs

NODE = shutil.which("node")
EQ_JS = os.path.join(
    os.path.dirname(__file__), "..", "src", "synth_ui", "web", "eq.js"
)

pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")


def _cases():
    """Every band of every layout on, at settings that exercise each branch:
    boosts and cuts, narrow and wide, and resonance either side of fil4's
    pole-clamp at 1.3."""
    cases = []
    for uri, layout in EQ_LAYOUTS.items():
        for gain, width, res in ((6.0, 0.7, 0.3), (-9.0, 2.5, 1.4), (12.0, 0.1, 0.97)):
            values = {}
            for i, band in enumerate(layout.bands):
                values[band.enable] = 1.0
                values[band.freq] = 60.0 * 2.3 ** i
                if band.gain:
                    values[band.gain] = layout.gain_value(gain if i % 2 else -gain)
                if band.width:
                    values[band.width] = res if band.kind in ("highpass",
                                                             "lowpass") else width
            cases.append({"uri": uri, "layout": layout.to_dict(), "values": values})
    return cases


def test_the_browser_draws_the_same_curve():
    freqs = log_freqs(64)
    cases = _cases()
    script = f"""
        const eq = require({json.dumps(os.path.abspath(EQ_JS))});
        const cases = JSON.parse(require("fs").readFileSync(0, "utf8"));
        const freqs = {json.dumps(freqs)};
        console.log(JSON.stringify(cases.map(c => eq.response(c.layout, c.values,
                                                              freqs))));
    """
    out = subprocess.run(
        [NODE, "-e", script], input=json.dumps(cases),
        capture_output=True, text=True, check=True,
    )
    got = json.loads(out.stdout)
    for case, js in zip(cases, got):
        py = EQ_LAYOUTS[case["uri"]].response(case["values"], freqs)
        assert js == pytest.approx(py, abs=1e-6), case["uri"]
