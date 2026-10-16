"use strict";
/* Lemma UI. Everything user-visible is built with textContent (no innerHTML on data). */

const $ = (id) => document.getElementById(id);
const S = {
  schema: null, examples: null, exampleKey: null, rule: null, rules: [],
  checkOld: null, checkNew: null, lits: [], litsOrig: [], origNew: "",
  diff: null, grid: null, base: null, baseKey: null, ax: "sbp", ay: "dbp", point: null, runId: 0, gridId: 0,
};
const CLASS_LABEL = { "-1": "not a plausible patient", 0: "neither version fires", 1: "both versions fire", 2: "only the old version fires", 3: "only the new version fires" };
const RESULT_LABEL = { true: "fires", false: "does not fire", null: "undecided: data missing", "true": "fires", "false": "does not fire", "null": "undecided: data missing" };

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === false || v == null) continue;
    if (k === "text") el.textContent = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat(Infinity)) if (k != null) el.append(k.nodeType ? k : document.createTextNode(k));
  return el;
}
async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.message || (Array.isArray(j.detail) ? "Invalid request" : j.detail) || r.statusText);
  return j;
}
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
const pct = (x) => (x * 100).toFixed(x < 0.1 && x > 0 ? 1 : 0) + "%";
const num = (v) => (Math.round(v * 1000) / 1000).toString();
const decimals = (step) => { const s = String(step); return s.includes(".") ? s.split(".")[1].length : 0; };

/* ---------- boot ---------- */
async function boot() {
  [S.schema, S.examples] = await Promise.all([api("/api/schema"), api("/api/examples")]);
  const sel = $("example");
  const names = Object.keys(S.examples.rules);
  const opts = [];
  if (names.includes("bp_initiation_v1") && names.includes("bp_initiation_v2")) opts.push(["bp_initiation_v1>bp_initiation_v2", "Blood pressure: v1 to v2 (guideline update)"]);
  for (const n of names) opts.push([n + ">" + n, `${n.replace(/_/g, " ")}: try your own change`]);
  for (const [v, t] of opts) sel.append(h("option", { value: v, text: t }));
  sel.addEventListener("change", () => loadExample(sel.value));
  $("rule").addEventListener("change", () => { S.rule = $("rule").value; S.baseKey = null; S.revealNext = true; runDiff(); });
  $("src-old").addEventListener("input", scheduleRun);
  $("src-new").addEventListener("input", () => { S.origNew ||= ""; scheduleRun(); });
  $("reset").addEventListener("click", () => { $("src-new").value = S.origNew; scheduleRun(0); });
  $("axis-x").addEventListener("change", () => { S.ax = $("axis-x").value; buildContext(); runGrid(); });
  $("axis-y").addEventListener("change", () => { S.ay = $("axis-y").value; buildContext(); runGrid(); });
  initTabs();
  loadExample(sel.value);
}

function loadExample(key) {
  const [a, b] = key.split(">");
  S.exampleKey = key;
  $("src-old").value = S.examples.rules[a];
  $("src-new").value = S.examples.rules[b];
  S.origNew = S.examples.rules[b];
  $("h-old").textContent = a === b ? "Current version" : "Old version";
  $("h-new").textContent = a === b ? "Your edited version" : "New version";
  S.rule = null; S.baseKey = null; S.point = null; S.revealNext = true;
  resetTabs();
  scheduleRun(0);
}

/* ---------- check + diff ---------- */
const scheduleRun = (() => { const d = debounce(run, 300); return (ms) => (ms === 0 ? run() : d()); })();

function renderDiags(ul, st, res) {
  ul.replaceChildren();
  for (const d of res.diagnostics) ul.append(h("li", { "data-sev": d.severity }, h("code", { text: d.line ? `${d.line}:${d.col} ${d.code}` : d.code }), " " + d.message));
  st.textContent = res.ok ? "valid" : "has errors";
  st.dataset.ok = res.ok;
}

async function run() {
  const id = ++S.runId;
  const [a, b] = [$("src-old").value, $("src-new").value];
  let ra, rb;
  try { [ra, rb] = await Promise.all([api("/api/check", { source: a }), api("/api/check", { source: b })]); }
  catch (e) { return verdict("error", "Could not reach the Lemma server.", e.message); }
  if (id !== S.runId) return;
  S.checkOld = ra; S.checkNew = rb;
  renderDiags($("diag-old"), $("st-old"), ra);
  renderDiags($("diag-new"), $("st-new"), rb);
  syncSliders(rb);
  if (!ra.ok || !rb.ok) return verdict("error", "Fix the errors above to compare.", "Both versions must pass the check first.");
  const common = ra.rules.filter((r) => rb.rules.includes(r));
  if (!common.length) return verdict("error", "The two versions share no rule name.", "Keep the rule you want to compare under the same name in both.");
  const sel = $("rule");
  if (S.rule == null || !common.includes(S.rule)) S.rule = common[common.length - 1];
  sel.replaceChildren(...common.map((r) => h("option", { value: r, text: r })));
  sel.value = S.rule;
  await runDiff(id);
}

async function runDiff(id = ++S.runId) {
  if (!S.checkNew?.ok || !S.checkOld?.ok) return;
  const [a, b] = [$("src-old").value, $("src-new").value];
  verdict("idle", "Comparing…", "");
  let d;
  try { d = await api("/api/diff", { a, b, rule: S.rule, summary: true }); }
  catch (e) { return verdict("error", "Could not compare these versions.", e.message); }
  if (id !== S.runId) return;
  S.diff = d;
  const key = S.exampleKey + "|" + S.rule;
  if (S.baseKey !== key) { initBase(); S.baseKey = key; }
  populateAxes();
  buildContext();
  renderWitnesses();
  await runGrid();
}

function verdict(state, title, sub) {
  const v = $("verdict");
  v.dataset.state = state;
  v.replaceChildren(h("strong", { text: title }), sub ? h("span", { class: "sub", text: sub }) : "");
}

/* ---------- base patient + context panel ---------- */
function initBase() {
  const sch = S.schema;
  const w = S.diff?.gained?.patient || S.diff?.lost?.patient;
  const base = { age: sch.age.typical, sex: sch.sexes[0], obs: {}, conditions: [], medications: [] };
  for (const [c, o] of Object.entries(sch.observations)) base.obs[c] = { value: o.typical, present: true };
  if (w) {
    base.age = w.age; base.sex = w.sex; base.conditions = [...w.conditions]; base.medications = [...w.medications];
    for (const c of Object.keys(base.obs)) base.obs[c] = w.observations[c] ? { value: Number(w.observations[c].value ?? base.obs[c].value), present: w.observations[c].value != null } : { value: base.obs[c].value, present: false };
  }
  S.base = base;
  const uses = [...new Set([...(S.checkOld.uses || []), ...(S.checkNew.uses || [])])];
  S.ax = uses[0] || "sbp";
  S.ay = uses[1] || Object.keys(sch.observations).find((c) => c !== S.ax);
  S.point = null;
}

function populateAxes() {
  const opts = [["age", "Age (years)"], ...Object.entries(S.schema.observations).map(([c, o]) => [c, `${o.label} (${o.unit})`])];
  for (const [id, key] of [["axis-x", "ax"], ["axis-y", "ay"]]) {
    const el = $(id);
    el.replaceChildren(...opts.map(([v, t]) => h("option", { value: v, text: t })));
    el.value = S[key];
  }
}

function buildContext() {
  const box = $("ctx"), B = S.base, sch = S.schema;
  box.replaceChildren();
  const axes = [S.ax, S.ay];
  if (!axes.includes("age")) box.append(h("div", { class: "line" }, h("label", { for: "c-age", text: "Age" }), h("input", { id: "c-age", type: "number", min: sch.age.min, max: sch.age.max, value: B.age, onchange: (e) => { B.age = Number(e.target.value); runGrid(); } })));
  box.append(h("div", { class: "line" }, h("label", { for: "c-sex", text: "Sex" }), h("select", { id: "c-sex", onchange: (e) => { B.sex = e.target.value; runGrid(); } }, sch.sexes.map((s) => h("option", { value: s, text: s, selected: s === B.sex })))));
  for (const [c, o] of Object.entries(sch.observations)) {
    if (axes.includes(c)) continue;
    const o2 = B.obs[c];
    const inp = h("input", { id: "c-" + c, type: "number", min: o.min, max: o.max, step: o.step, value: o2.value, disabled: !o2.present, "aria-label": `${o.label} (${o.unit})`, onchange: (e) => { o2.value = Number(e.target.value); runGrid(); } });
    const nr = h("label", { class: "nr" }, h("input", { type: "checkbox", checked: !o2.present, onchange: (e) => { o2.present = !e.target.checked; inp.disabled = !o2.present; runGrid(); } }), "not recorded");
    box.append(h("div", { class: "line" }, h("label", { for: "c-" + c, text: `${o.label} (${o.unit})` }), h("div", {}, inp)), h("div", { class: "line" }, h("span"), nr));
  }
  const group = (title, items, key) => Object.keys(items).length && h("div", {}, h("div", { class: "note", text: title }), h("div", { class: "checks" }, Object.entries(items).map(([k, label]) => h("label", {}, h("input", { type: "checkbox", checked: B[key].includes(k), onchange: (e) => { B[key] = e.target.checked ? [...B[key], k] : B[key].filter((x) => x !== k); runGrid(); } }), label))));
  box.append(group("Conditions", sch.conditions, "conditions") || "", group("Medications", sch.medications, "medications") || "");
}

function basePayload() {
  const B = S.base, obs = {};
  for (const [c, o] of Object.entries(B.obs)) if (o.present) obs[c] = { value: o.value };
  return { age: B.age, sex: B.sex, observations: obs, conditions: B.conditions, medications: B.medications };
}

/* ---------- zone grid + canvas ---------- */
const runGridSoon = debounce(() => runGrid(), 120);
async function runGrid() {
  if (!S.checkNew?.ok || !S.checkOld?.ok || !S.rule) return;
  const id = ++S.gridId;
  const axis = (v) => ({ var: v });
  try {
    const g = await api("/api/grid", { a: $("src-old").value, b: $("src-new").value, rule: S.rule, x: axis(S.ax), y: axis(S.ay), base: basePayload(), size: 64 });
    if (id !== S.gridId) return;
    S.grid = g;
  } catch (e) { return verdict("error", "Could not draw the plot.", e.message); }
  if (S.revealNext) { S.revealNext = false; revealPlot(); } else drawPlot();
  renderLegend();
  renderVerdict();
}

function cssVar(n) { return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); }
let hatch = null;
function hatchPattern(ctx, line, bg) {
  const c = document.createElement("canvas"); c.width = c.height = 8;
  const g = c.getContext("2d"); g.fillStyle = bg; g.fillRect(0, 0, 8, 8); g.strokeStyle = line; g.lineWidth = 1;
  g.beginPath(); g.moveTo(0, 8); g.lineTo(8, 0); g.stroke();
  return ctx.createPattern(c, "repeat");
}
function axisTitle(v) { return v === "age" ? "Age (years)" : `${S.schema.observations[v].label} (${S.schema.observations[v].unit})`; }
function niceTicks(lo, hi, n) {
  const raw = (hi - lo) / n, p = 10 ** Math.floor(Math.log10(raw)), f = raw / p;
  const step = (f >= 5 ? 5 : f >= 2 ? 2 : 1) * p, out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(6));
  return out;
}
function edges(vals, to) { const e = []; for (let i = 0; i <= vals.length; i++) { const a = vals[Math.max(i - 1, 0)], b = vals[Math.min(i, vals.length - 1)]; e.push(i === 0 ? to(vals[0]) - (to(vals[1]) - to(vals[0])) / 2 : i === vals.length ? to(vals[i - 1]) + (to(vals[i - 1]) - to(vals[i - 2])) / 2 : (to(a) + to(b)) / 2); } return e; }

function thresholdLines(res) {
  const out = [];
  for (const l of res?.literals || []) {
    const m = l.text.match(/Latest\("(\w+)"\)/) || (l.text.includes("AgeInYears") ? [null, "age"] : null);
    if (!m) continue;
    const spec = m[1] === "age" ? null : S.schema.observations[m[1]];
    if (spec && l.unit && l.unit !== spec.unit) continue;
    const op = (l.text.match(/(>=|<=|>|<|=|!=)/) || [, ""])[1];
    out.push({ v: m[1], value: l.value, op });
  }
  return out;
}

function drawPlot(reveal = 1) {
  const cv = $("plot"), ctx = cv.getContext("2d"), g = S.grid;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const cssW = cv.parentElement.clientWidth - 20, cssH = Math.round(cssW * 0.76);
  cv.style.width = cssW + "px"; cv.style.height = cssH + "px";
  cv.width = Math.round(cssW * dpr); cv.height = Math.round(cssH * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const cw = cssW, ch = cssH, m = { l: 64, r: 16, t: 22, b: 52 };
  const W = cw - m.l - m.r, H = ch - m.t - m.b;
  const xs = g.x.values, ys = g.y.values;
  const x0 = xs[0], x1 = xs[xs.length - 1], y0 = ys[0], y1 = ys[ys.length - 1];
  const px = (v) => m.l + ((v - x0) / (x1 - x0 || 1)) * W, py = (v) => m.t + H - ((v - y0) / (y1 - y0 || 1)) * H;
  S.geo = { m, W, H, px, py, x0, x1, y0, y1, cw, ch };
  const css = { ink: cssVar("--ink"), muted: cssVar("--muted"), hair: cssVar("--hair"), grid: cssVar("--grid"), major: cssVar("--grid-major"), sheet: cssVar("--sheet"), lost: cssVar("--lost"), gained: cssVar("--gained"), both: cssVar("--both") };
  hatch = hatchPattern(ctx, css.major, css.sheet);
  ctx.clearRect(0, 0, cw, ch);

  // chart-paper grid: minor every fifth of a major tick, drawn over the fills
  const xt = niceTicks(x0, x1, 7), yt = niceTicks(y0, y1, 6);
  const minor = (ticks, lo, hi, to, vertical) => {
    if (ticks.length < 2) return;
    const step = (ticks[1] - ticks[0]) / 5;
    for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) {
      const p = Math.round(to(v)) + 0.5;
      ctx.beginPath();
      if (vertical) { ctx.moveTo(p, m.t); ctx.lineTo(p, m.t + H); } else { ctx.moveTo(m.l, p); ctx.lineTo(m.l + W, p); }
      ctx.stroke();
    }
  };
  ctx.lineWidth = 1; ctx.strokeStyle = css.grid; ctx.globalAlpha = 0.9;
  minor(xt, x0, x1, px, true); minor(yt, y0, y1, py, false);
  ctx.strokeStyle = css.major; ctx.globalAlpha = 0.8;
  for (const t of xt) { const p = Math.round(px(t)) + 0.5; ctx.beginPath(); ctx.moveTo(p, m.t); ctx.lineTo(p, m.t + H); ctx.stroke(); }
  for (const t of yt) { const p = Math.round(py(t)) + 0.5; ctx.beginPath(); ctx.moveTo(m.l, p); ctx.lineTo(m.l + W, p); ctx.stroke(); }
  ctx.globalAlpha = 1;
  ctx.strokeStyle = css.muted; ctx.lineWidth = 1; ctx.strokeRect(m.l + .5, m.t + .5, W, H);

  // cells (the "neither fires" class is left blank so the chart paper shows through)
  ctx.save();
  ctx.beginPath(); ctx.rect(m.l, m.t, W * reveal, H); ctx.clip();
  const ex = edges(xs, px), ey = edges(ys, py);
  const fill = { 1: css.both, 2: css.lost, 3: css.gained };
  for (let j = 0; j < ys.length; j++) for (let i = 0; i < xs.length; i++) {
    const c = g.cells[j][i];
    if (c === 0) continue;
    ctx.fillStyle = c === -1 ? hatch : fill[c];
    ctx.globalAlpha = c === 1 ? 0.7 : c === -1 ? 1 : 0.92;
    const xa = Math.max(m.l, Math.min(ex[i], ex[i + 1])), xb = Math.min(m.l + W, Math.max(ex[i], ex[i + 1]));
    const ya = Math.max(m.t, Math.min(ey[j], ey[j + 1])), yb = Math.min(m.t + H, Math.max(ey[j], ey[j + 1]));
    ctx.fillRect(xa, ya, xb - xa + 0.6, yb - ya + 0.6);
  }
  ctx.restore();

  // axis labels
  ctx.fillStyle = css.muted; ctx.font = '12px "Instrument Sans", system-ui, sans-serif';
  ctx.textAlign = "center"; ctx.textBaseline = "top";
  for (const t of xt) ctx.fillText(num(t), px(t), m.t + H + 7);
  ctx.textAlign = "right"; ctx.textBaseline = "middle";
  for (const t of yt) ctx.fillText(num(t), m.l - 9, py(t));
  ctx.fillStyle = css.ink; ctx.font = '500 13.5px "Instrument Sans", system-ui, sans-serif'; ctx.textAlign = "center"; ctx.textBaseline = "alphabetic";
  ctx.fillText(axisTitle(g.x.var), m.l + W / 2, ch - 10);
  ctx.save(); ctx.translate(16, m.t + H / 2); ctx.rotate(-Math.PI / 2); ctx.fillText(axisTitle(g.y.var), 0, 0); ctx.restore();

  // threshold lines: where the rule text draws its lines (old = dotted, new = dashed with a label)
  const lines = (res, dash, label) => {
    ctx.save(); ctx.setLineDash(dash); ctx.lineWidth = 1.4; ctx.strokeStyle = css.ink; ctx.globalAlpha = label ? 0.8 : 0.4;
    ctx.font = '500 12px "IBM Plex Mono", ui-monospace, monospace'; ctx.fillStyle = css.ink;
    for (const t of thresholdLines(res)) {
      if (t.v === g.x.var && t.value >= x0 && t.value <= x1) {
        const x = Math.round(px(t.value)) + 0.5; ctx.beginPath(); ctx.moveTo(x, m.t); ctx.lineTo(x, m.t + H); ctx.stroke();
        if (label) { ctx.setLineDash([]); ctx.textAlign = "left"; ctx.textBaseline = "bottom"; ctx.globalAlpha = 1; ctx.fillText(`${t.op} ${num(t.value)}`, x + 4, m.t - 3); ctx.setLineDash(dash); ctx.globalAlpha = 0.8; }
      }
      if (t.v === g.y.var && t.value >= y0 && t.value <= y1) {
        const y = Math.round(py(t.value)) + 0.5; ctx.beginPath(); ctx.moveTo(m.l, y); ctx.lineTo(m.l + W, y); ctx.stroke();
        if (label) { ctx.setLineDash([]); ctx.textAlign = "right"; ctx.textBaseline = "bottom"; ctx.globalAlpha = 1; ctx.fillText(`${t.op} ${num(t.value)}`, m.l + W - 4, y - 3); ctx.setLineDash(dash); ctx.globalAlpha = 0.8; }
      }
    }
    ctx.restore();
  };
  lines(S.checkOld, [2, 4], false);
  lines(S.checkNew, [6, 4], true);

  // direct labels on the regions that changed
  if (reveal >= 1) {
    const total = ys.length * xs.length;
    for (const [cls, text, color] of [[3, "new version only", css.gained], [2, "old version only", css.lost]]) {
      let n = 0, sx = 0, sy = 0;
      for (let j = 0; j < ys.length; j++) for (let i = 0; i < xs.length; i++) if (g.cells[j][i] === cls) { n++; sx += i; sy += j; }
      if (n / total < 0.012) continue;
      const ci = Math.round(sx / n), cj = Math.round(sy / n);
      if (g.cells[cj][ci] !== cls) continue;
      ctx.font = '600 13px "Instrument Sans", system-ui, sans-serif'; ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.lineWidth = 4; ctx.strokeStyle = color; ctx.lineJoin = "round"; ctx.strokeText(text, px(xs[ci]), py(ys[cj]));
      ctx.fillStyle = matchMedia("(prefers-color-scheme: dark)").matches ? "#0b171b" : "#fff"; ctx.fillText(text, px(xs[ci]), py(ys[cj]));
    }
  }
  if (S.point) {
    const x = px(S.point.x), y = py(S.point.y);
    ctx.lineWidth = 3.5; ctx.strokeStyle = css.sheet; ctx.beginPath(); ctx.arc(x, y, 7, 0, 7); ctx.stroke();
    ctx.lineWidth = 1.8; ctx.strokeStyle = css.ink; ctx.beginPath(); ctx.arc(x, y, 7, 0, 7); ctx.stroke();
  }
}

function revealPlot() {
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) return drawPlot(1);
  const t0 = performance.now(), D = 800;
  const step = (now) => {
    const t = Math.min(1, (now - t0) / D);
    drawPlot(1 - Math.pow(1 - t, 3));
    if (t < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

function cellAt(ev) {
  const cv = $("plot"), r = cv.getBoundingClientRect(), geo = S.geo, g = S.grid;
  if (!geo || !g) return null;
  const x = ((ev.clientX - r.left) / r.width) * geo.cw, y = ((ev.clientY - r.top) / r.height) * geo.ch;
  if (x < geo.m.l || x > geo.m.l + geo.W || y < geo.m.t || y > geo.m.t + geo.H) return null;
  const vx = geo.x0 + ((x - geo.m.l) / geo.W) * (geo.x1 - geo.x0), vy = geo.y0 + (1 - (y - geo.m.t) / geo.H) * (geo.y1 - geo.y0);
  const near = (arr, v) => arr.reduce((bi, a, i) => (Math.abs(a - v) < Math.abs(arr[bi] - v) ? i : bi), 0);
  const i = near(g.x.values, vx), j = near(g.y.values, vy);
  return { i, j, x: g.x.values[i], y: g.y.values[j], cls: g.cells[j][i], clientX: ev.clientX - r.left, clientY: ev.clientY - r.top };
}
function initPlotEvents() {
  const cv = $("plot"), tip = $("tip");
  const label = (c) => `${S.grid.x.var} ${num(c.x)}, ${S.grid.y.var} ${num(c.y)}: ${CLASS_LABEL[c.cls]}`;
  cv.addEventListener("pointermove", (ev) => {
    const c = cellAt(ev);
    if (!c) { tip.hidden = true; return; }
    tip.hidden = false; tip.textContent = label(c);
    tip.style.left = Math.min(c.clientX, cv.clientWidth - 220) + 8 + "px"; tip.style.top = c.clientY + "px";
  });
  cv.addEventListener("pointerleave", () => { tip.hidden = true; });
  cv.addEventListener("click", (ev) => { const c = cellAt(ev); if (!c) return; S.point = { x: c.x, y: c.y }; drawPlot(); renderVerdict(c); });
  window.addEventListener("resize", debounce(() => S.grid && drawPlot(), 150));
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => S.grid && drawPlot());
}

function renderLegend() {
  const c = S.grid.counts, total = (c[0] + c[1] + c[2] + c[3]) || 1;
  const sw = (bg, extra) => h("span", { style: `background:${bg}${extra || ""}` });
  const items = [
    [cssVar("--lost"), "Only the old version fires", c[2]],
    [cssVar("--gained"), "Only the new version fires", c[3]],
    [cssVar("--both"), "Both fire", c[1]],
    [cssVar("--sheet"), "Neither fires", c[0]],
  ];
  const ul = $("legend");
  ul.replaceChildren(...items.map(([bg, t, n]) => h("li", {}, sw(bg), t + " ", h("b", { text: pct(n / total) }))));
  if (c[-1] || c["-1"]) ul.append(h("li", {}, h("span", { style: `background:repeating-linear-gradient(135deg, ${cssVar("--grid-major")} 0 2px, ${cssVar("--sheet")} 2px 6px)` }), "Not a plausible patient"));
}

function renderVerdict(cell) {
  const d = S.diff, c = S.grid.counts, total = (c[0] + c[1] + c[2] + c[3]) || 1, changed = c[2] + c[3];
  let title, sub, state = d.status;
  if (d.status === "equivalent") { title = `No difference for "${S.rule}".`; sub = "Both versions give every patient in the checked domain the same answer."; }
  else if (d.status === "different") {
    const parts = [];
    if (d.gained) parts.push("some patients newly get it");
    if (d.lost) parts.push("some patients no longer get it");
    title = `The change affects who gets "${S.rule}".`;
    sub = `Proved: ${parts.join(" and ")}. On this plot, ${pct(changed / total)} of plausible patients change.`;
  } else { title = "Lemma could not decide in time."; sub = "The solver gave up on part of the question; the witnesses below may be incomplete."; state = "unknown"; }
  if (cell) sub += `  At ${S.grid.x.var} ${num(cell.x)}, ${S.grid.y.var} ${num(cell.y)}: ${CLASS_LABEL[cell.cls]}.`;
  verdict(state, title, sub);
}

/* ---------- sliders (the what-if explorer) ---------- */
function syncSliders(res) {
  const lits = res.ok ? res.literals : [];
  const box = $("sliders");
  const same = lits.length === S.lits.length && box.children.length === lits.length && lits.every((l, i) => l.text === S.lits[i].text);
  S.lits = lits.map((l) => ({ ...l }));
  if (same) { [...box.children].forEach((row, i) => { row.dataset.i = i; }); return; }
  S.litsOrig = lits.map((l) => l.value);
  $("sliders-panel").hidden = !lits.length;
  box.replaceChildren(...lits.map((l, i) => {
    const dec = decimals(l.step), out = h("span", { class: "val", text: l.value.toFixed(dec) + (l.unit ? " " + l.unit : "") });
    const inp = h("input", { type: "range", min: l.min, max: l.max, step: l.step, value: l.value, "aria-label": `${l.subject} threshold in ${l.define}`, oninput: (e) => onSlide(i, Number(e.target.value), out) });
    return h("div", { class: "slider", "data-i": i }, h("div", { class: "row" }, h("span", { text: `${l.subject}, ${opOf(l.text)}` }), out), inp, h("span", { class: "was", text: `in "${l.define}"; started at ${l.value.toFixed(dec)}` }));
  }));
}
const opOf = (t) => (t.match(/(>=|<=|>|<|=|!=)/) || [, "compared with"])[1];
function onSlide(i, v, out) {
  const l = S.lits[i], dec = decimals(l.step), txt = v.toFixed(dec) + (l.unit ? ` '${l.unit}'` : "");
  const ta = $("src-new"), src = ta.value;
  ta.value = src.slice(0, l.start) + txt + src.slice(l.end);
  const delta = txt.length - (l.end - l.start);
  l.end = l.start + txt.length;
  for (const o of S.lits) if (o !== l && o.start > l.start) { o.start += delta; o.end += delta; }
  l.value = v;
  out.textContent = v.toFixed(dec) + (l.unit ? " " + l.unit : "");
  scheduleRun();
}

/* ---------- witnesses ---------- */
function patientDl(p) {
  const rows = [["Age", `${p.age} years`], ["Sex", p.sex]];
  for (const [c, o] of Object.entries(p.observations)) {
    const spec = S.schema.observations[c];
    let t = o.value != null ? `${o.value} ${spec.unit}` : "no current value";
    if (o.prior != null) t += `, previous ${o.prior} ${spec.unit} ${o.gap_hours} h earlier`;
    rows.push([spec.label, t]);
  }
  const miss = Object.keys(S.schema.observations).filter((c) => !(c in p.observations));
  if (miss.length) rows.push(["Not recorded", miss.map((c) => S.schema.observations[c].label.toLowerCase()).join(", ")]);
  if (p.conditions.length) rows.push(["Conditions", p.conditions.join(", ")]);
  if (p.medications.length) rows.push(["Medications", p.medications.join(", ")]);
  return h("dl", {}, rows.map(([k, v]) => [h("dt", { text: k }), h("dd", { text: v })]));
}
function renderWitnesses() {
  const box = $("witnesses"), d = S.diff;
  box.replaceChildren();
  for (const [dir, title] of [["gained", "Newly recommended: smallest example"], ["lost", "No longer recommended: smallest example"]]) {
    const w = d[dir];
    if (!w) continue;
    const card = h("article", { class: "wit", "data-dir": dir }, h("h3", { text: title }),
      h("p", { class: "res", text: `Old version: ${RESULT_LABEL[w.a]}. New version: ${RESULT_LABEL[w.b]}.` }));
    const z = zoneText(w.zone);
    if (z) card.append(h("p", { class: "zone", text: z }));
    card.append(patientDl(w.patient));
    const fh = d.fhir?.[dir];
    card.append(h("div", { class: "actions" },
      h("button", { type: "button", class: "quiet", onclick: () => { useAsBase(w.patient); } }, "Plot this patient"),
      fh && h("button", { type: "button", class: "quiet", onclick: () => download(`lemma-${dir}.fhir.json`, fh.bundle) }, "Download as FHIR bundle")));
    if (fh) card.append(h("p", { class: "note", text: fh.structural_problems.length ? "Bundle has structural problems: " + fh.structural_problems.join("; ") : "Bundle passes Lemma's structural check (not a full HL7 validation)." }));
    box.append(card);
  }
}
function zoneText(z) {
  if (!z) return "";
  const parts = [];
  if (z.age && (z.age.min > S.schema.age.min || z.age.max < S.schema.age.max)) parts.push(`age ${z.age.min} to ${z.age.max}`);
  for (const [c, r] of Object.entries(z.observations || {})) {
    const spec = S.schema.observations[c];
    if (r.min != null && (Number(r.min) > spec.min || Number(r.max) < spec.max)) parts.push(`${c} ${r.min} to ${r.max} ${spec.unit}`);
  }
  return parts.length ? "Bounding ranges of every changed patient: " + parts.join("; ") + "." : "";
}
function useAsBase(p) {
  const B = S.base;
  B.age = p.age; B.sex = p.sex; B.conditions = [...p.conditions]; B.medications = [...p.medications];
  for (const c of Object.keys(B.obs)) { const o = p.observations[c]; B.obs[c] = o && o.value != null ? { value: Number(o.value), present: true } : { value: B.obs[c].value, present: false }; }
  buildContext(); runGrid();
  $("plot-section").scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
}
function download(name, obj) {
  const a = h("a", { href: URL.createObjectURL(new Blob([JSON.stringify(obj, null, 2)], { type: "application/json" })), download: name });
  document.body.append(a); a.click(); a.remove();
}

/* ---------- secondary analyses ---------- */
function initTabs() {
  document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => {
    document.querySelectorAll(".tabs button").forEach((x) => x.setAttribute("aria-selected", x === b));
    document.querySelectorAll(".tabpanel").forEach((p) => (p.hidden = p.id !== "tab-" + b.dataset.tab));
  }));
}
function resetTabs() {
  const inv = Object.values(S.examples.invariants)[0] || "";
  $("tab-impact").replaceChildren(
    h("div", { class: "row-actions" },
      h("label", {}, "Synthetic patients", h("select", { id: "imp-n" }, [1000, 3000, 10000].map((n) => h("option", { value: n, text: n.toLocaleString(), selected: n === 3000 })))),
      h("button", { type: "button", id: "imp-go", onclick: runImpact }, "Estimate who is affected")),
    h("p", { class: "note", text: "Uses a generated population, so the numbers describe the generator and show how the analysis works. Load your own cohort with the command line." }),
    h("div", { id: "imp-out" }));
  $("tab-verify").replaceChildren(
    h("p", { class: "note", text: "Each rule says what the new version must never do. Lemma either proves it for every patient in range or shows a patient who breaks it." }),
    h("textarea", { id: "inv-src", spellcheck: "false", "aria-label": "Safety rules" }, inv),
    h("div", { class: "row-actions" }, h("button", { type: "button", onclick: runVerify }, "Check the new version")),
    h("div", { id: "ver-out" }));
  $("tab-missing").replaceChildren(
    h("p", { class: "note", text: "Which single missing measurement would silence the rule, or make it fire without evidence?" }),
    h("div", { class: "row-actions" }, h("button", { type: "button", onclick: runMissing }, "Analyse the new version")),
    h("div", { id: "mis-out" }));
  $("tab-robust").replaceChildren(
    h("p", { class: "note", text: "A decision is fragile if normal measurement noise (the tolerance set in the schema) could flip it." }),
    h("div", { class: "row-actions" }, h("button", { type: "button", onclick: runRobust }, "Check synthetic patients")),
    h("div", { id: "rob-out" }));
}
async function guarded(btn, out, fn) {
  btn.disabled = true; out.replaceChildren(h("p", { class: "empty", text: "Working…" }));
  try { out.replaceChildren(await fn()); } catch (e) { out.replaceChildren(h("p", { class: "note", text: "Could not run: " + e.message })); }
  finally { btn.disabled = false; }
}
const needOk = () => { if (!S.checkNew?.ok || !S.rule) throw new Error("Fix the errors in the new version first."); };

function rateRow(label, r) {
  return h("tr", {}, h("td", { text: label }), h("td", { class: "num", text: r.k.toLocaleString() }), h("td", { class: "num", text: pct(r.rate) }),
    h("td", {}, h("div", { class: "bar", title: `95% interval ${pct(r.ci_low)} to ${pct(r.ci_high)}` }, h("i", { style: `left:${r.ci_low * 100}%;width:${Math.max((r.ci_high - r.ci_low) * 100, 0.8)}%` }))),
    h("td", { text: `${pct(r.ci_low)} to ${pct(r.ci_high)}` }));
}
async function runImpact(ev) {
  await guarded(ev.currentTarget, $("imp-out"), async () => {
    needOk();
    const r = await api("/api/impact", { a: $("src-old").value, b: $("src-new").value, rule: S.rule, n: Number($("imp-n").value) });
    const wrap = h("div", {});
    wrap.append(h("table", {}, h("thead", {}, h("tr", {}, ["Outcome", "Patients", "Share", "", "95% interval"].map((t, i) => h("th", { class: i === 1 || i === 2 ? "num" : "", text: t })))),
      h("tbody", {}, [["Old version recommends", r.a_fires], ["New version recommends", r.b_fires], ["Newly recommended", r.gained], ["No longer recommended", r.lost], ["Changed either way", r.changed]].map(([l, x]) => rateRow(l, x)))));
    for (const [g, entries] of Object.entries(r.groups)) {
      wrap.append(h("h3", { text: "By " + g.replace(/_/g, " ") }), h("table", {}, h("thead", {}, h("tr", {}, ["Group", "Patients", "Changed", "", "95% interval"].map((t, i) => h("th", { class: i === 1 || i === 2 ? "num" : "", text: t })))),
        h("tbody", {}, entries.map((e) => { const row = rateRow(e.group, e.changed); row.children[1].textContent = e.n.toLocaleString() + (e.n < 30 ? " (small)" : ""); row.children[2].textContent = pct(e.changed.rate); return row; }))));
    }
    for (const n of r.notes || []) wrap.append(h("p", { class: "note", text: n }));
    return wrap;
  });
}
async function runVerify(ev) {
  await guarded(ev.currentTarget, $("ver-out"), async () => {
    needOk();
    const r = await api("/api/verify", { source: $("src-new").value, invariants: $("inv-src").value });
    const wrap = h("div", {});
    wrap.append(h("p", { text: `${r.holds} hold, ${r.violated} broken.` + (r.skipped.length ? ` ${r.skipped.length} skipped (their rule isn't in this library).` : "") }));
    wrap.append(h("table", {}, h("tbody", {}, r.results.map((x) => h("tr", {}, h("td", {}, h("span", { class: "pill", "data-s": x.status, text: { holds: "holds", violated: "broken", unknown: "unknown", error: "error" }[x.status] })),
      h("td", {}, h("div", { text: x.statement }), x.source && h("div", { class: "note", text: x.source }), x.vacuous && h("div", { class: "note", text: `Holds trivially: ${x.vacuous}.` }), x.message && h("div", { class: "note", text: x.message }), x.counterexample && h("div", {}, h("div", { class: "note", text: "A patient who breaks it:" }), patientDl(x.counterexample))))))));
    return wrap;
  });
}
async function runMissing(ev) {
  await guarded(ev.currentTarget, $("mis-out"), async () => {
    needOk();
    const r = await api("/api/missing", { source: $("src-new").value, rule: S.rule });
    if (!r.findings.length) return h("p", { text: "No single missing measurement silences this rule or makes it fire on its own." });
    return h("div", {}, r.findings.map((f) => h("div", {}, h("p", { text: f.effect === "silences" ? `Without ${S.schema.observations[f.observation]?.label ?? f.observation}, the rule goes quiet.` : `Without ${S.schema.observations[f.observation]?.label ?? f.observation}, the rule fires anyway.` }), patientDl(f.patient))));
  });
}
async function runRobust(ev) {
  await guarded(ev.currentTarget, $("rob-out"), async () => {
    needOk();
    const r = await api("/api/robustness", { source: $("src-new").value, rule: S.rule });
    const wrap = h("div", {}, h("p", { text: `${r.fragile} of ${r.patients_evaluated} synthetic patients (${pct(r.fragile_share)}) would get a different answer from a measurement within normal tolerance.` }));
    wrap.append(h("table", {}, h("thead", {}, h("tr", {}, [h("th", { text: "Distance to a flip, in tolerances" }), h("th", { class: "num", text: "Patients" })])), h("tbody", {}, Object.entries(r.distance_buckets_in_tolerances).map(([k, v]) => h("tr", {}, h("td", { text: k }), h("td", { class: "num", text: v }))))));
    const drivers = Object.entries(r.most_common_driver_of_fragile_flips || {});
    if (drivers.length) wrap.append(h("p", { class: "note", text: "Measurements that tip fragile decisions: " + drivers.map(([c, n]) => `${c} (${n})`).join(", ") + "." }));
    return wrap;
  });
}

initPlotEvents();
boot().catch((e) => verdict("error", "Could not start.", e.message));
