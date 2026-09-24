// The browser editor. Draws the instrument's sets and rigs from /api/state,
// polled once a second, and sends each change straight back.
//
// Controls come from the server already classified — knob, toggle, choice,
// and an EQ layout where there is one (clients/controls.py, clients/eq.py) —
// so the browser and the touchscreen show every plugin the same way.
"use strict";

const EQ = window.SynthEQ;
const POLL_MS = 1000;
// Coalesce a dragged control into one request per this many ms, so a knob
// turned across its range is a few dozen requests, not hundreds.
const SEND_MS = 60;

// --- DOM ---------------------------------------------------------------------

const $ = (sel, el = document) => el.querySelector(sel);
const SVG_NS = "http://www.w3.org/2000/svg";

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  setAttrs(el, attrs);
  for (const child of children.flat()) {
    if (child == null || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

function s(tag, attrs = {}) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  return el;
}

function setAttrs(el, attrs) {
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else if (k in el && typeof v !== "string") el[k] = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
}

// --- the API -----------------------------------------------------------------

async function api(method, path, body) {
  const init = { method, credentials: "same-origin", headers: {} };
  if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  const res = await fetch(path, init);
  const data = await res.json().catch(() => ({}));
  if (res.status === 401 && path !== "/api/login") {
    showLogin();
    throw new Error("PIN needed");
  }
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

// A change the user made: send it, then show the result.
async function act(method, path, body) {
  try {
    await api(method, path, body);
  } catch (err) {
    setStatus(err.message, true);
  }
  await refresh();
}

// --- state -------------------------------------------------------------------

let state = null;
let stateText = "";
let catalog = { voices: [], effects: [] };
const view = {
  setId: null,
  rigId: null,
  open: new Set(),        // effect blocks shown expanded, by "rigId/index"
  instrumentOpen: true,
  interacting: 0,         // pointers down on a control; renders wait for them
  dirty: false,
};
// Plugin controls by "effect:<uri>" / "voice:<name>": null while loading.
const controls = new Map();
let polling = false;

function setStatus(text, error = false) {
  const el = $("#status");
  el.textContent = text;
  el.classList.toggle("error", error);
}

async function refresh() {
  try {
    const next = await api("GET", "/api/state");
    setStatus(next.busy ? "loading on the instrument…" : "connected");
    const text = JSON.stringify(next);
    if (text !== stateText) {
      stateText = text;
      state = next;
      if (view.interacting) view.dirty = true;
      else render();
    }
  } catch (err) {
    if (err.message !== "PIN needed") setStatus("can't reach the instrument", true);
  }
}

async function poll() {
  if (polling) return;
  polling = true;
  while (polling) {
    await refresh();
    await new Promise((r) => setTimeout(r, POLL_MS));
  }
}

function beginInteraction() { view.interacting++; }
function endInteraction() {
  view.interacting = Math.max(0, view.interacting - 1);
  if (!view.interacting && view.dirty) {
    view.dirty = false;
    render();
  }
}

function getControls(key, path) {
  if (controls.has(key)) return controls.get(key);
  controls.set(key, null);
  api("GET", path)
    .then((data) => { controls.set(key, data); render(); })
    .catch(() => { controls.delete(key); });
  return null;
}

// --- sending a control, coalesced ---------------------------------------------

const outbox = new Map();   // path -> {method, body builder, pending, timer}

function sendLater(path, merge, method = "PATCH") {
  let entry = outbox.get(path);
  if (!entry) {
    entry = { body: {}, timer: null };
    outbox.set(path, entry);
  }
  merge(entry.body);
  if (entry.timer) return;
  entry.timer = setTimeout(async () => {
    outbox.delete(path);
    try {
      await api(method, path, entry.body);
    } catch (err) {
      setStatus(err.message, true);
    }
  }, SEND_MS);
}

function sendEffectParam(rig, index, symbol, value) {
  sendLater(`/api/rigs/${rig.id}/effects/${index}`, (body) => {
    body.params = { ...(body.params || {}), [symbol]: value };
  });
}

function sendVoiceParam(rig, symbol, value) {
  sendLater(`/api/rigs/${rig.id}/voice-params/${encodeURIComponent(symbol)}`,
            (body) => { body.value = value; }, "PUT");
}

// --- control math: mirrors clients/controls.py Control -------------------------

function toRatio(c, v) {
  if (c.maximum === c.minimum) return 0;
  v = Math.min(c.maximum, Math.max(c.minimum, v));
  if (c.logarithmic) return Math.log(v / c.minimum) / Math.log(c.maximum / c.minimum);
  return (v - c.minimum) / (c.maximum - c.minimum);
}

function fromRatio(c, r) {
  r = Math.min(1, Math.max(0, r));
  let v = c.logarithmic
    ? c.minimum * Math.pow(c.maximum / c.minimum, r)
    : c.minimum + r * (c.maximum - c.minimum);
  if (c.integer) v = Math.round(v);
  return Math.min(c.maximum, Math.max(c.minimum, v));
}

function optionIndex(c, v) {
  let best = 0;
  c.options.forEach(([ov], i) => {
    if (Math.abs(ov - v) < Math.abs(c.options[best][0] - v)) best = i;
  });
  return best;
}

function format(c, v) {
  if (c.kind === "toggle") return v >= 0.5 ? "on" : "off";
  if (c.kind === "choice") return c.options.length ? c.options[optionIndex(c, v)][1] : "";
  const named = c.options.find(([ov]) => ov === Math.round(v));
  if (named) return named[1];
  if (c.integer) return v.toFixed(0);
  if (Math.abs(v) >= 10000) return (v / 1000).toFixed(1) + "k";
  if (Math.abs(v) >= 1000) return (v / 1000).toFixed(2) + "k";
  if (Math.abs(c.maximum - c.minimum) <= 4) return v.toFixed(2);
  return v.toFixed(1);
}

// --- widgets -------------------------------------------------------------------

const KNOB = 56, R = 22, START = 225, SWEEP = 270;

function arcPath(r0, r1) {
  const pt = (r) => {
    const a = (START - SWEEP * r) * Math.PI / 180;
    return [KNOB / 2 + R * Math.cos(a), KNOB / 2 - R * Math.sin(a)];
  };
  const [lo, hi] = r0 < r1 ? [r0, r1] : [r1, r0];
  const [x0, y0] = pt(lo), [x1, y1] = pt(hi);
  const large = (hi - lo) * SWEEP > 180 ? 1 : 0;
  return `M${x0},${y0} A${R},${R} 0 ${large} 1 ${x1},${y1}`;
}

function knob(c, value, onChange) {
  const svg = s("svg", { width: KNOB, height: KNOB, viewBox: `0 0 ${KNOB} ${KNOB}` });
  svg.append(s("path", { d: arcPath(0, 1), class: "track", "stroke-width": 6,
                         fill: "none", "stroke-linecap": "round" }));
  const fill = s("path", { class: "fill", "stroke-width": 6, fill: "none",
                           "stroke-linecap": "round" });
  const dot = s("circle", { r: 5, class: "dot" });
  svg.append(fill, dot);
  const out = h("div", { class: "value" });
  const el = h("div", { class: "ctl knob", title: `${c.name} — drag up/down, ` +
                        "shift for fine, double-click to reset", tabindex: 0 },
               svg, out, h("div", { class: "label" }, c.name));
  // A control either side of zero fills from zero, as on the touchscreen.
  const origin = c.minimum < 0 && c.maximum > 0 ? toRatio(c, 0) : 0;

  function show(v) {
    const r = toRatio(c, v);
    fill.setAttribute("d", Math.abs(r - origin) > 0.004 ? arcPath(origin, r) : "");
    const a = (START - SWEEP * r) * Math.PI / 180;
    dot.setAttribute("cx", KNOB / 2 + R * Math.cos(a));
    dot.setAttribute("cy", KNOB / 2 - R * Math.sin(a));
    out.textContent = format(c, v);
  }
  function set(v) {
    v = fromRatio(c, toRatio(c, v));
    if (v === value) return;
    value = v;
    show(v);
    onChange(v);
  }
  show(value);

  let grab = null;
  el.addEventListener("pointerdown", (e) => {
    el.setPointerCapture(e.pointerId);
    grab = { y: e.clientY, r: toRatio(c, value) };
    el.classList.add("dragging");
    beginInteraction();
  });
  el.addEventListener("pointermove", (e) => {
    if (!grab) return;
    const travel = e.shiftKey ? 800 : 200;
    set(fromRatio(c, grab.r + (grab.y - e.clientY) / travel));
  });
  const release = () => {
    if (!grab) return;
    grab = null;
    el.classList.remove("dragging");
    endInteraction();
  };
  el.addEventListener("pointerup", release);
  el.addEventListener("pointercancel", release);
  el.addEventListener("dblclick", () => set(c.default));
  el.addEventListener("wheel", (e) => {
    e.preventDefault();
    set(fromRatio(c, toRatio(c, value) - Math.sign(e.deltaY) * (e.shiftKey ? 0.002 : 0.02)));
  }, { passive: false });
  el.addEventListener("keydown", (e) => {
    const step = { ArrowUp: 0.02, ArrowRight: 0.02, ArrowDown: -0.02, ArrowLeft: -0.02 }[e.key];
    if (step) { e.preventDefault(); set(fromRatio(c, toRatio(c, value) + step)); }
  });
  return el;
}

function toggle(c, value, onChange) {
  const button = h("button", { type: "button", class: "toggle" });
  const show = () => {
    button.textContent = value >= 0.5 ? "On" : "Off";
    button.classList.toggle("on", value >= 0.5);
    button.setAttribute("aria-pressed", value >= 0.5);
  };
  button.addEventListener("click", () => {
    value = value >= 0.5 ? 0 : 1;
    show();
    onChange(value);
  });
  show();
  return h("div", { class: "ctl" }, button, h("div", { class: "label" }, c.name));
}

function choice(c, value, onChange) {
  if (c.options.length <= 4) {
    const box = h("div", { class: "choice", role: "group", "aria-label": c.name });
    const buttons = c.options.map(([v, label]) => {
      const b = h("button", { type: "button" }, label);
      b.addEventListener("click", () => {
        value = v;
        buttons.forEach((x, i) => x.classList.toggle("on", c.options[i][0] === v));
        onChange(v);
      });
      b.classList.toggle("on", optionIndex(c, value) === c.options.findIndex(([ov]) => ov === v));
      return b;
    });
    box.append(...buttons);
    return h("div", { class: "ctl" }, box, h("div", { class: "label" }, c.name));
  }
  const select = h("select", { "aria-label": c.name },
    c.options.map(([v, label], i) =>
      h("option", { value: String(v), selected: i === optionIndex(c, value) }, label)));
  select.addEventListener("change", () => onChange(Number(select.value)));
  return h("div", { class: "ctl wide" }, select, h("div", { class: "label" }, c.name));
}

function widget(c, value, onChange) {
  if (c.kind === "toggle") return toggle(c, value, onChange);
  if (c.kind === "choice") return choice(c, value, onChange);
  return knob(c, value, onChange);
}

// --- the EQ ---------------------------------------------------------------------

const EQW = 640, EQH = 250, M = { l: 8, r: 8, t: 10, b: 22 };

function eqPanel(layout, list, values, onChange) {
  const byName = Object.fromEntries(list.map((c) => [c.symbol, c]));
  const pw = EQW - M.l - M.r, ph = EQH - M.t - M.b;
  const xFor = (f) => M.l + Math.log(f / layout.freq_lo) / Math.log(layout.freq_hi / layout.freq_lo) * pw;
  const fFor = (x) => layout.freq_lo * Math.pow(layout.freq_hi / layout.freq_lo,
                                                Math.min(1, Math.max(0, (x - M.l) / pw)));
  const clampDb = (db) => Math.max(-layout.db_range, Math.min(layout.db_range, db));
  const yFor = (db) => M.t + ph / 2 - clampDb(db) / layout.db_range * (ph / 2);
  const dbFor = (y) => clampDb((M.t + ph / 2 - y) / (ph / 2) * layout.db_range);
  const freqs = EQ.logFreqs(160, layout.freq_lo, layout.freq_hi);
  let selected = layout.bands.findIndex((b) => EQ.isEnabled(b, values));
  if (selected < 0) selected = 0;

  const svg = s("svg", { viewBox: `0 0 ${EQW} ${EQH}`, role: "img",
                         "aria-label": "EQ curve: drag a band's handle" });
  svg.append(s("rect", { x: M.l, y: M.t, width: pw, height: ph, class: "plot", rx: 4 }));
  for (const f of [50, 100, 200, 500, 1000, 2000, 5000, 10000]) {
    svg.append(s("line", { x1: xFor(f), x2: xFor(f), y1: M.t, y2: M.t + ph, class: "grid" }));
    const label = { 100: "100", 1000: "1k", 10000: "10k" }[f];
    if (label) {
      const t = s("text", { x: xFor(f), y: EQH - 6, class: "axis", "text-anchor": "middle" });
      t.textContent = label;
      svg.append(t);
    }
  }
  for (const db of [-12, -6, 0, 6, 12]) {
    svg.append(s("line", { x1: M.l, x2: M.l + pw, y1: yFor(db), y2: yFor(db),
                           class: db ? "grid" : "zero" }));
    if (db) {
      const t = s("text", { x: M.l + 4, y: yFor(db) - 3, class: "axis" });
      t.textContent = (db > 0 ? "+" : "") + db;
      svg.append(t);
    }
  }
  const area = s("path", { class: "area" });
  const bandCurve = s("path", { class: "band-curve" });
  const curve = s("path", { class: "curve" });
  const handles = s("g");
  svg.append(area, bandCurve, curve, handles);

  const row = h("div", { class: "controls" });
  const title = h("div", { class: "band-title" });

  function pathOf(dbs) {
    return dbs.map((db, i) => `${i ? "L" : "M"}${xFor(freqs[i]).toFixed(1)},${yFor(db).toFixed(1)}`).join("");
  }
  // Kept inside the plot: fil4's high- and low-pass default to 20 Hz and
  // 20 kHz, which would put half of each handle off the graph.
  function handlePos(band) {
    const x = Math.min(M.l + pw - 12, Math.max(M.l + 12, xFor(values[band.freq])));
    return [x, yFor(EQ.gainDb(layout, band, values))];
  }
  function draw() {
    const total = EQ.response(layout, values, freqs);
    const line = pathOf(total);
    curve.setAttribute("d", line);
    area.setAttribute("d", `${line}L${xFor(freqs[freqs.length - 1])},${yFor(0)}L${xFor(freqs[0])},${yFor(0)}Z`);
    const band = layout.bands[selected];
    bandCurve.setAttribute("d", EQ.isEnabled(band, values)
      ? pathOf(freqs.map((f) => EQ.bandDb(layout, band, values, f))) : "");
    handles.replaceChildren();
    const order = layout.bands.map((b, i) => i).filter((i) => i !== selected).concat(selected);
    for (const i of order) {
      const b = layout.bands[i];
      const [x, y] = handlePos(b);
      const g = s("g", { class: "handle" + (EQ.isEnabled(b, values) ? " on" : "") +
                                (i === selected ? " selected" : ""),
                         transform: `translate(${x},${y})`, "data-band": i });
      g.append(s("circle", { r: i === selected ? 14 : 12 }));
      const t = s("text");
      t.textContent = b.label;
      g.append(t);
      handles.append(g);
    }
  }
  function set(symbol, value) {
    const c = byName[symbol];
    if (c) value = Math.min(c.maximum, Math.max(c.minimum, value));
    if (values[symbol] === value) return;
    values[symbol] = value;
    onChange(symbol, value);
  }
  function buildRow() {
    const band = layout.bands[selected];
    title.textContent = `Band ${band.label} · ${band.kind}`;
    row.replaceChildren(...[band.enable, band.freq, band.gain, band.width]
      .filter((sym) => sym && byName[sym])
      .map((sym) => widget(byName[sym], values[sym], (v) => { set(sym, v); draw(); })));
  }

  // Drag a handle: across is frequency, up and down is gain. A band that is
  // off comes on when moved — moving it is asking to hear it.
  let drag = null;
  const toSvg = (e) => {
    const r = svg.getBoundingClientRect();
    return [(e.clientX - r.left) * EQW / r.width, (e.clientY - r.top) * EQH / r.height];
  };
  svg.addEventListener("pointerdown", (e) => {
    const [px, py] = toSvg(e);
    let best = null, bestD = 30;
    layout.bands.forEach((b, i) => {
      const [x, y] = handlePos(b);
      const d = Math.hypot(x - px, y - py) - (i === selected ? 4 : 0);
      if (d < bestD) { best = i; bestD = d; }
    });
    if (best === null) return;
    if (best !== selected) { selected = best; buildRow(); }
    const [hx, hy] = handlePos(layout.bands[best]);
    drag = { dx: hx - px, dy: hy - py };
    svg.setPointerCapture(e.pointerId);
    beginInteraction();
    draw();
  });
  svg.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const [px, py] = toSvg(e);
    const band = layout.bands[selected];
    if (!EQ.isEnabled(band, values)) set(band.enable, 1);
    set(band.freq, fFor(px + drag.dx));
    if (band.gain) set(band.gain, EQ.gainValue(layout, dbFor(py + drag.dy)));
    draw();
    buildRow();
  });
  const release = () => { if (drag) { drag = null; endInteraction(); } };
  svg.addEventListener("pointerup", release);
  svg.addEventListener("pointercancel", release);

  buildRow();
  draw();
  return h("div", { class: "eq" }, svg, title, row);
}

// A plugin's controls, with its EQ drawn as a curve when it has one.
function controlsPanel(data, values, onChange) {
  const list = data.controls;
  for (const c of list) if (!(c.symbol in values)) values[c.symbol] = c.default;
  const eq = data.eq;
  const hidden = new Set(eq ? eq.hidden : []);
  const inBands = new Set(eq ? eq.bands.flatMap((b) => [b.enable, b.freq, b.gain, b.width]) : []);
  const rest = list.filter((c) => !hidden.has(c.symbol) && !inBands.has(c.symbol));
  const set = (sym, v) => { values[sym] = v; onChange(sym, v); };
  return h("div", {},
    eq ? eqPanel(eq, list.filter((c) => !hidden.has(c.symbol)), values, onChange) : null,
    rest.length
      ? h("div", { class: "controls" }, rest.map((c) => widget(c, values[c.symbol], (v) => set(c.symbol, v))))
      : (eq ? null : h("div", { class: "loading" }, "No adjustable parameters")));
}

// --- rendering ------------------------------------------------------------------

function currentSet() {
  return state.sets.find((x) => x.id === view.setId) || null;
}
function currentRig() {
  const set = currentSet();
  return set ? set.rigs.find((r) => r.id === view.rigId) || null : null;
}
function effectName(uri) {
  const e = catalog.effects.find((x) => x.uri === uri);
  return e ? e.name : uri.split(/[/#]/).pop();
}
function effectCategory(uri) {
  const e = catalog.effects.find((x) => x.uri === uri);
  return e ? e.category : "";
}

function render() {
  if (!state) return;
  if (!state.sets.some((x) => x.id === view.setId)) {
    view.setId = state.active_set || (state.sets[0] && state.sets[0].id) || null;
  }
  const set = currentSet();
  if (set && !set.rigs.some((r) => r.id === view.rigId)) {
    const active = set.rigs.find((r) => r.active);
    view.rigId = (active || set.rigs[0] || {}).id || null;
  }
  renderSets();
  renderRigs();
  renderEditor();
}

function listItem({ name, sub, selected, badge, onSelect, tools }) {
  // The badge rides the second line, so a narrow column still has room for
  // the name beside the row's buttons.
  return h("li", { class: selected ? "selected" : "" },
    h("button", { type: "button", class: "name", onclick: onSelect }, name,
      h("span", { class: "sub" }, sub, badge ? " " : null, badge)),
    h("span", { class: "tools" }, tools));
}

function tool(label, title, onclick, disabled = false) {
  return h("button", { type: "button", title, "aria-label": title, onclick, disabled }, label);
}

function renderSets() {
  const ul = $("#sets");
  ul.replaceChildren(...state.sets.map((set, i) => listItem({
    name: set.name,
    sub: `${set.rigs.length} rig${set.rigs.length === 1 ? "" : "s"}`,
    selected: set.id === view.setId,
    badge: set.id === state.active_set ? h("span", { class: "badge live" }, "live") : null,
    onSelect: () => { view.setId = set.id; view.rigId = null; render(); },
    tools: [
      tool("↑", "Move up", () => act("POST", `/api/sets/${set.id}/move`, { to: i - 1 }), i === 0),
      tool("↓", "Move down", () => act("POST", `/api/sets/${set.id}/move`, { to: i + 1 }),
           i === state.sets.length - 1),
      tool("✎", "Rename", () => {
        const name = prompt("Rename set", set.name);
        if (name && name.trim()) act("PATCH", `/api/sets/${set.id}`, { name });
      }),
      tool("✕", "Delete set", () => {
        if (confirm(`Delete "${set.name}" and its ${set.rigs.length} rig(s)?`)) {
          act("DELETE", `/api/sets/${set.id}`);
        }
      }),
    ],
  })));
}

function renderRigs() {
  const ul = $("#rigs");
  const set = currentSet();
  $("#rigs-title").textContent = set ? `Rigs — ${set.name}` : "Rigs";
  $("#new-rig").hidden = !set;
  if (!set) { ul.replaceChildren(); return; }
  ul.replaceChildren(...set.rigs.map((rig, i) => listItem({
    name: rig.name,
    sub: rig.voice,
    selected: rig.id === view.rigId,
    badge: rig.active ? h("span", { class: "badge live" }, "live")
      : rig.unavailable ? h("span", { class: "badge warn", title: rig.unavailable }, "missing") : null,
    onSelect: () => { view.rigId = rig.id; render(); },
    tools: [
      tool("↑", "Move up", () => act("POST", `/api/rigs/${rig.id}/move`, { to: i - 1 }), i === 0),
      tool("↓", "Move down", () => act("POST", `/api/rigs/${rig.id}/move`, { to: i + 1 }),
           i === set.rigs.length - 1),
    ],
  })));
}

function voiceOptions(selected) {
  const groups = new Map();
  for (const v of catalog.voices) {
    if (!groups.has(v.category)) groups.set(v.category, []);
    groups.get(v.category).push(v);
  }
  return [...groups].map(([category, voices]) =>
    h("optgroup", { label: category || "Other" },
      voices.map((v) => h("option", { value: v.name, selected: v.name === selected,
                                       disabled: !!v.unavailable && v.name !== selected },
                          v.unavailable ? `${v.name} (unavailable)` : v.name))));
}

function renderEditor() {
  const root = $("#editor");
  const rig = currentRig();
  if (!rig) {
    root.replaceChildren(h("div", { class: "empty" },
      currentSet() ? "This set has no rigs yet." : "Pick a set."));
    return;
  }
  const busy = state.busy && rig.active;
  const range = state.trim_range_db;

  const trimOut = h("output", {}, fmtDb(rig.trim_db));
  const trim = h("input", { type: "range", min: -range, max: range, step: 0.5,
                            value: rig.trim_db, "aria-label": "Level" });
  trim.addEventListener("pointerdown", beginInteraction);
  trim.addEventListener("pointerup", endInteraction);
  trim.addEventListener("input", () => {
    trimOut.textContent = fmtDb(Number(trim.value));
    rig.trim_db = Number(trim.value);
    sendLater(`/api/rigs/${rig.id}`, (b) => { b.trim_db = Number(trim.value); });
  });

  const voice = h("select", { "aria-label": "Instrument", disabled: busy },
                  voiceOptions(rig.voice));
  voice.addEventListener("change", () => {
    if (rig.active && !confirm("Change the instrument that's playing? Its " +
                               "controls return to the new instrument's settings.")) {
      voice.value = rig.voice;
      return;
    }
    act("PATCH", `/api/rigs/${rig.id}`, { voice: voice.value });
  });

  const copyTo = h("select", { "aria-label": "Copy to set" },
    state.sets.map((x) => h("option", { value: x.id, selected: x.id === view.setId }, x.name)));

  const title = h("input", { class: "title", value: rig.name, "aria-label": "Rig name" });
  title.addEventListener("change", () => {
    if (title.value.trim()) act("PATCH", `/api/rigs/${rig.id}`, { name: title.value });
  });

  root.replaceChildren(...[
    h("div", { class: "rig-head" },
      title,
      rig.active ? h("span", { class: "badge live" }, "live") : h("span", { class: "badge" }, "stored"),
      h("button", { type: "button", class: "danger", onclick: () => {
        if (confirm(`Delete rig "${rig.name}"?`)) act("DELETE", `/api/rigs/${rig.id}`);
      } }, "Delete")),
    h("p", { class: "explain" }, rig.active
      ? "Being played — changes are heard as you make them, and saved."
      : "Not being played — changes are saved, and heard when the rig is chosen on the instrument."),
    busy ? h("div", { class: "banner" }, "The instrument is loading…") : null,
    rig.unavailable ? h("div", { class: "banner" }, `Can't play on this board: ${rig.unavailable}`) : null,
    h("div", { class: "fields" },
      h("label", { class: "field" }, h("span", {}, "Instrument"), voice),
      h("div", { class: "field" }, h("span", {}, "Level"),
        h("div", { class: "row" }, trim, trimOut)),
      state.fixed_velocity_available
        ? h("label", { class: "field" }, h("span", {}, "Velocity"),
            h("div", { class: "row" },
              h("select", { "aria-label": "Velocity", onchange: (e) =>
                  act("PATCH", `/api/rigs/${rig.id}`, { fixed_velocity: e.target.value === "fixed" }) },
                h("option", { value: "played", selected: !rig.fixed_velocity }, "As played"),
                h("option", { value: "fixed", selected: rig.fixed_velocity }, "Fixed"))))
        : null,
      h("div", { class: "field" }, h("span", {}, "Copy to set"),
        h("div", { class: "row" }, copyTo,
          h("button", { type: "button", onclick: () =>
            act("POST", `/api/rigs/${rig.id}/copy`, { set_id: copyTo.value }) }, "Copy")))),
    instrumentBlock(rig),
    ...rig.effects.map((effect, i) => effectBlock(rig, effect, i, busy)),
    addEffect(rig, busy),
  ].filter(Boolean));
}

function fmtDb(db) { return (db > 0 ? "+" : "") + Number(db).toFixed(1) + " dB"; }

function block({ key, name, cat, open, onToggle, tools, body, bypassed }) {
  return h("div", { class: "block" + (bypassed ? " bypassed" : "") },
    h("div", { class: "block-head", onclick: onToggle, "data-key": key },
      h("span", { class: "chev", "aria-hidden": "true" }, open ? "▾" : "▸"),
      h("span", { class: "name" }, name, cat ? h("span", { class: "cat" }, cat) : null),
      h("span", { onclick: (e) => e.stopPropagation() }, tools)),
    open ? h("div", { class: "block-body" }, body()) : null);
}

function instrumentBlock(rig) {
  return block({
    key: "instrument",
    name: rig.voice,
    cat: "Instrument",
    open: view.instrumentOpen,
    onToggle: () => { view.instrumentOpen = !view.instrumentOpen; render(); },
    body: () => {
      const data = getControls("voice:" + rig.voice,
                               `/api/controls/voice?name=${encodeURIComponent(rig.voice)}`);
      if (!data) return h("div", { class: "loading" }, "Reading the instrument's controls…");
      const values = rig.voice_values;
      return controlsPanel(data, values, (sym, v) => {
        rig.voice_values[sym] = v;
        sendVoiceParam(rig, sym, v);
      });
    },
  });
}

function effectBlock(rig, effect, i, busy) {
  const key = `${rig.id}/${i}`;
  const open = view.open.has(key);
  return block({
    key,
    name: effectName(effect.uri),
    cat: effectCategory(effect.uri),
    open,
    bypassed: effect.bypassed,
    onToggle: () => { open ? view.open.delete(key) : view.open.add(key); render(); },
    tools: [
      tool(effect.bypassed ? "Off" : "On", effect.bypassed ? "Turn on" : "Bypass",
           () => act("PATCH", `/api/rigs/${rig.id}/effects/${i}`, { bypassed: !effect.bypassed }),
           busy),
      tool("↑", "Move earlier", () => {
        moveOpen(rig, i, i - 1);
        act("POST", `/api/rigs/${rig.id}/effects/${i}/move`, { to: i - 1 });
      }, busy || i === 0),
      tool("↓", "Move later", () => {
        moveOpen(rig, i, i + 1);
        act("POST", `/api/rigs/${rig.id}/effects/${i}/move`, { to: i + 1 });
      }, busy || i === rig.effects.length - 1),
      tool("✕", "Remove", () => {
        if (confirm(`Remove ${effectName(effect.uri)}? Its settings go with it.`)) {
          view.open.delete(key);
          act("DELETE", `/api/rigs/${rig.id}/effects/${i}`);
        }
      }, busy),
    ],
    body: () => {
      const data = getControls("effect:" + effect.uri,
                               `/api/controls/effect?uri=${encodeURIComponent(effect.uri)}`);
      if (!data) return h("div", { class: "loading" }, "Reading the effect's controls…");
      return controlsPanel(data, effect.params, (sym, v) => {
        effect.params[sym] = v;
        sendEffectParam(rig, i, sym, v);
      });
    },
  });
}

// Keep an effect's block open when it moves, since blocks are keyed by position.
function moveOpen(rig, from, to) {
  const a = `${rig.id}/${from}`, b = `${rig.id}/${to}`;
  const aOpen = view.open.has(a), bOpen = view.open.has(b);
  view.open.delete(a); view.open.delete(b);
  if (aOpen) view.open.add(b);
  if (bOpen) view.open.add(a);
}

function addEffect(rig, busy) {
  const groups = new Map();
  for (const e of catalog.effects) {
    if (!groups.has(e.category)) groups.set(e.category, []);
    groups.get(e.category).push(e);
  }
  const select = h("select", { "aria-label": "Effect to add" },
    [...groups].map(([cat, list]) => h("optgroup", { label: cat },
      list.map((e) => h("option", { value: e.uri, disabled: !!e.unavailable, title: e.note },
                        e.unavailable ? `${e.name} (unavailable)` : e.name)))));
  return h("div", { class: "add-effect" }, select,
    h("button", { type: "button", disabled: busy, onclick: () => {
      view.open.add(`${rig.id}/${rig.effects.length}`);
      act("POST", `/api/rigs/${rig.id}/effects`, { uri: select.value });
    } }, "Add effect"));
}

// --- login, forms, start ------------------------------------------------------

function showLogin() {
  polling = false;
  $("#app").hidden = true;
  $("#login").hidden = false;
  $("#login-form").pin.focus();
}

async function start() {
  $("#login").hidden = true;
  $("#app").hidden = false;
  try {
    catalog = await api("GET", "/api/catalog");
  } catch (err) {
    return;
  }
  $("#new-rig").voice.replaceChildren(...voiceOptions(null));
  poll();
}

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await api("POST", "/api/login", { pin: e.target.pin.value });
    $("#login-error").textContent = "";
    e.target.pin.value = "";
    start();
  } catch (err) {
    $("#login-error").textContent = err.message;
  }
});

$("#new-set").addEventListener("submit", (e) => {
  e.preventDefault();
  act("POST", "/api/sets", { name: e.target.name.value });
  e.target.name.value = "";
});

$("#new-rig").addEventListener("submit", (e) => {
  e.preventDefault();
  if (view.setId) act("POST", `/api/sets/${view.setId}/rigs`, { voice: e.target.voice.value });
});

$("#export").addEventListener("click", async () => {
  try {
    const data = await api("GET", "/api/export");
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const a = h("a", { href: URL.createObjectURL(blob),
                       download: `synth-sets-${new Date().toISOString().slice(0, 10)}.json` });
    a.click();
    URL.revokeObjectURL(a.href);
  } catch (err) {
    setStatus(err.message, true);
  }
});

$("#import").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file) return;
  try {
    const result = await api("POST", "/api/import", JSON.parse(await file.text()));
    setStatus(`imported ${result.added} set(s)`);
    await refresh();
  } catch (err) {
    setStatus(`import failed: ${err.message}`, true);
  }
});

start();
