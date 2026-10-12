// Shared console primitives: data access, formatting, icons, charts, maps.

// Requests made while a page is open share that page's AbortController, so
// switching page, area or horizon cancels work that would arrive too late.
let scope = new AbortController();
export function newScope() { scope.abort(); scope = new AbortController(); return scope.signal; }

async function errorOf(r) {
  let msg = `${r.status} ${r.statusText}`;
  try { const j = await r.json(); msg = j.error?.message || j.detail || msg; } catch (e) { /* not JSON */ }
  const err = new Error(msg); err.status = r.status; return err;
}

async function request(path, opt = {}, { retries = 0, timeout = 20000 } = {}) {
  const url = path.startsWith('/api/') && !path.startsWith('/api/v1/') && !path.startsWith('/api/docs') ? '/api/v1/' + path.slice(5) : path;
  for (let attempt = 0; ; attempt++) {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort('timeout'), timeout);
    const onAbort = () => ctl.abort('navigation');
    const sig = scope.signal;
    sig.addEventListener('abort', onAbort, { once: true });
    try {
      const r = await fetch(url, { credentials: 'same-origin', ...opt, signal: ctl.signal });
      if (r.ok) return r;
      if (r.status >= 500 && attempt < retries) { await new Promise((z) => setTimeout(z, 600 * 2 ** attempt)); continue; }
      throw await errorOf(r);
    } catch (e) {
      if (ctl.signal.reason === 'navigation') { const a = new Error('cancelled'); a.cancelled = true; throw a; }
      if (ctl.signal.reason === 'timeout') {
        if (attempt < retries) continue;
        const a = new Error('The server took too long to answer. Check the connection and try again.'); a.timeout = true; throw a;
      }
      if (e instanceof TypeError && attempt < retries) { await new Promise((z) => setTimeout(z, 600 * 2 ** attempt)); continue; }
      if (e instanceof TypeError) { const a = new Error('Network unavailable. Nothing was sent.'); a.offline = true; throw a; }
      throw e;
    } finally { clearTimeout(t); sig.removeEventListener('abort', onAbort); }
  }
}

const inflight = new Map();
const W = { 'X-RINDA': '1' };
export const api = {
  async get(path) {
    // identical concurrent GETs share one request
    if (inflight.has(path)) return inflight.get(path);
    const p = request(path, {}, { retries: 2 }).then((r) => r.json()).finally(() => inflight.delete(path));
    inflight.set(path, p);
    return p;
  },
  async post(path, body) { return (await request(path, { method: 'POST', headers: { ...W, 'content-type': 'application/json' }, body: JSON.stringify(body ?? {}) })).json(); },
  async patch(path, body) { return (await request(path, { method: 'PATCH', headers: { ...W, 'content-type': 'application/json' }, body: JSON.stringify(body ?? {}) })).json(); },
  async del(path) { return (await request(path, { method: 'DELETE', headers: W })).json(); },
  async upload(path, form) { return request(path, { method: 'POST', headers: W, body: form }, { timeout: 120000 }); },
};

// ------------------------------------------------------------------ format
const nf = (d) => new Intl.NumberFormat('en-GB', { maximumFractionDigits: d, minimumFractionDigits: d });
export const fmt = {
  n: (v, d = 0) => (v == null || Number.isNaN(v) ? '–' : nf(d).format(v)),
  pct: (v, d = 1) => (v == null ? '–' : `${nf(d).format(v * 100)}%`),
  p: (v) => (v == null ? '–' : v.toFixed(3)),
  rwf: (v) => (v == null ? '–' : `RWF ${new Intl.NumberFormat('en-GB', { notation: v >= 1e6 ? 'compact' : 'standard', maximumFractionDigits: 1 }).format(v)}`),
  date: (s) => (s ? new Date(s + 'T00:00:00').toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' }) : '–'),
  dshort: (s) => (s ? new Date(s + 'T00:00:00').toLocaleDateString('en-GB', { day: '2-digit', month: 'short' }) : '–'),
  min: (m) => (m == null ? '–' : m >= 90 ? `${Math.floor(m / 60)} h ${String(Math.round(m % 60)).padStart(2, '0')} min` : `${nf(0).format(m)} min`),
  ll: (lat, lon) => `${lat.toFixed(5)}, ${lon.toFixed(5)}`,
};
export const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

// ------------------------------------------------------------------- icons
const P = {
  overview: '<rect x="3.5" y="3.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="3.5" width="7" height="4.5" rx="1.5"/><rect x="13.5" y="11" width="7" height="9.5" rx="1.5"/><rect x="3.5" y="13.5" width="7" height="7" rx="1.5"/>',
  forecast: '<path d="M12 3.5c3.6 4.6 5.4 7.7 5.4 10a5.4 5.4 0 0 1-10.8 0c0-2.3 1.8-5.4 5.4-10z"/><path d="M9.6 14.2a2.6 2.6 0 0 0 2.4 2.4"/>',
  mission: '<path d="M4 19l5-14 3 8 3-4 5 10"/><circle cx="4" cy="19" r="1.5"/><circle cx="20" cy="19" r="1.5"/>',
  flight: '<circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="6" r="2.5"/><circle cx="6" cy="18" r="2.5"/><circle cx="18" cy="18" r="2.5"/><path d="M8 8l3 3M16 8l-3 3M8 16l3-3M16 16l-3-3"/><rect x="10.5" y="10.5" width="3" height="3" rx=".6"/>',
  scenario: '<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>',
  assurance: '<path d="M12 3l7.5 3v5.5c0 4.6-3.2 8.2-7.5 9.5-4.3-1.3-7.5-4.9-7.5-9.5V6z"/><path d="M8.8 12.2l2.2 2.2 4.4-4.6"/>',
  network: '<path d="M12 20v-6"/><circle cx="12" cy="12" r="2"/><path d="M8.5 8.5a5 5 0 0 1 7 0M6 6a8.5 8.5 0 0 1 12 0"/><path d="M8 20h8"/>',
  impact: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
  bulletin: '<path d="M6 3.5h9l3.5 3.5v13.5H6z"/><path d="M14.5 3.5V7.5H18.5M9 12h6M9 15.5h6M9 8.5h3"/>',
  alignment: '<circle cx="5" cy="12" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="19" cy="12" r="2"/><path d="M7 12h3M14 12h3"/><path d="M5 5v3M12 5v3M19 16v3"/>',
  governance: '<ellipse cx="12" cy="6" rx="7.5" ry="2.8"/><path d="M4.5 6v6c0 1.5 3.4 2.8 7.5 2.8s7.5-1.3 7.5-2.8V6M4.5 12v6c0 1.5 3.4 2.8 7.5 2.8s7.5-1.3 7.5-2.8v-6"/>',
  download: '<path d="M12 4v11M7.5 10.5L12 15l4.5-4.5M5 19.5h14"/>',
  upload: '<path d="M12 15V4M7.5 8.5L12 4l4.5 4.5M5 19.5h14"/>',
  print: '<path d="M7 8V3.5h10V8M7 16.5H4.5v-7h15v7H17"/><rect x="7" y="13.5" width="10" height="7"/>',
  play: '<path d="M7 5l11 7-11 7z"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  alert: '<path d="M12 4l9 16H3z"/><path d="M12 10v4.5M12 17.5v.01"/>',
  x: '<path d="M6 6l12 12M18 6L6 18"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  info: '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5.5M12 7.8v.01"/>',
  lock: '<rect x="5" y="10.5" width="14" height="10" rx="1.5"/><path d="M8 10.5V7.5a4 4 0 0 1 8 0v3"/>',
  route: '<circle cx="6" cy="18" r="2.2"/><circle cx="18" cy="6" r="2.2"/><path d="M8 18h7a3 3 0 0 0 0-6H9a3 3 0 0 1 0-6h7"/>',
  layers: '<path d="M12 4l8.5 4.5L12 13 3.5 8.5z"/><path d="M3.5 12.5L12 17l8.5-4.5M3.5 16.5L12 21l8.5-4.5"/>',
  arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  file: '<path d="M6 3.5h8l4 4v13H6z"/><path d="M14 3.5v4h4"/>',
  trash: '<path d="M4.5 7h15M9.5 7V4.5h5V7M6.5 7l1 13h9l1-13"/>',
  user: '<circle cx="12" cy="8" r="3.8"/><path d="M4.5 20c.8-4 3.8-6 7.5-6s6.7 2 7.5 6"/>',
  bell: '<path d="M6 16.5V11a6 6 0 0 1 12 0v5.5l1.5 2h-15z"/><path d="M10 20.5a2 2 0 0 0 4 0"/>',
  clipboard: '<rect x="5.5" y="4.5" width="13" height="16" rx="1.5"/><path d="M9 4.5V3h6v1.5M9 10h6M9 13.5h6M9 17h4"/>',
  replay: '<path d="M4 12a8 8 0 1 0 2.4-5.7"/><path d="M4 4v4.5h4.5"/><path d="M12 8v4l2.5 1.5"/>',
  help: '<circle cx="12" cy="12" r="8.5"/><path d="M9.6 9.5a2.5 2.5 0 0 1 4.8 1c0 1.7-2.4 2-2.4 3.5M12 17v.01"/>',
  shield: '<path d="M12 3l7.5 3v5.5c0 4.6-3.2 8.2-7.5 9.5-4.3-1.3-7.5-4.9-7.5-9.5V6z"/>',
  search: '<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/>',
  pin: '<path d="M12 21s-6.5-6.2-6.5-11a6.5 6.5 0 0 1 13 0c0 4.8-6.5 11-6.5 11z"/><circle cx="12" cy="10" r="2.3"/>',
};
export const icon = (name, cls = '') => `<svg viewBox="0 0 24 24" class="${cls}" aria-hidden="true">${P[name] || ''}</svg>`;

// --------------------------------------------------------------- semantics
export const RISK = ['very_high', 'high', 'moderate', 'low', 'very_low'];
export const RISK_LABEL = { very_high: 'Very high', high: 'High', moderate: 'Moderate', low: 'Low', very_low: 'Very low' };
export const CAT = ['high', 'moderate', 'insufficient', 'lower'];
export const CAT_LABEL = { high: 'High priority for verification', moderate: 'Moderate priority for verification', insufficient: 'Insufficient evidence to classify', lower: 'Lower current priority' };
export const CAT_SHORT = { high: 'High', moderate: 'Moderate', insufficient: 'Insufficient evidence', lower: 'Lower' };
export const DECISION_LABEL = { formation: 'Formation', uncertain: 'Verify first', no_formation: 'No formation' };
export const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
export const riskColor = (k) => css(`--risk-${k}`);

export function decisionTag(d) {
  if (d === 'formation') return `<span class="status st-crit">${icon('alert')}Formation</span>`;
  if (d === 'uncertain') return `<span class="status st-warn">${icon('info')}Verify first</span>`;
  return `<span class="status st-mute">No formation</span>`;
}
export function catTag(k, short = true) {
  const m = { high: ['st-crit', 'alert'], moderate: ['st-warn', 'info'], insufficient: ['st-info', 'info'], lower: ['st-mute', 'check'] }[k] || ['st-mute', 'info'];
  return `<span class="status ${m[0]}" title="${CAT_LABEL[k] || k}">${icon(m[1])}${short ? CAT_SHORT[k] : CAT_LABEL[k]}</span>`;
}
export function riskTag(k) {
  return `<span class="num"><span class="dot" style="background:var(--risk-${k})"></span>${RISK_LABEL[k] || k}</span>`;
}
export function statusTag(s) {
  const m = {
    operational: ['st-good', 'check', 'Operational'], awaiting_field: ['st-info', 'clock', 'Built · awaiting field data'],
    blocked: ['st-warn', 'lock', 'Waiting on data source'], out_of_scope: ['st-mute', 'x', 'Partner scope'],
    partial: ['st-info', 'clock', 'Partly evidenced'], PLANNED: ['st-info', 'clock', 'Planned'], APPROVED: ['st-good', 'check', 'Approved'],
    IN_FLIGHT: ['st-warn', 'play', 'In flight'], COMPLETED: ['st-good', 'check', 'Completed'], CANCELLED: ['st-mute', 'x', 'Cancelled'],
    FIELD: ['st-good', 'check', 'Field'], BENCH: ['st-info', 'info', 'Bench'], SYNTHETIC: ['st-warn', 'alert', 'Synthetic'],
  }[s] || ['st-mute', 'info', s];
  return `<span class="status ${m[0]}">${icon(m[1])}${m[2]}</span>`;
}

// ------------------------------------------------------------- components
export const card = (title, body, { sub = '', foot = '', cls = '', flush = false, tools = '' } = {}) => `
  <section class="card ${cls}">
    ${title ? `<div class="card-h"><h3>${title}</h3><div class="actions">${sub ? `<span class="sub">${sub}</span>` : ''}${tools}</div></div>` : ''}
    <div class="card-b ${flush ? 'flush' : ''}">${body}</div>
    ${foot ? `<div class="card-f">${foot}</div>` : ''}
  </section>`;

export const kpi = (label, value, meta = '', { unit = '', accent = false, id = '', tip = '' } = {}) => `
  <section class="card kpi ${accent ? 'accent' : ''}" ${tip ? `title="${esc(tip)}"` : ''}>
    <div class="lbl">${label}</div>
    <div class="val">${value}${unit ? `<small>${unit}</small>` : ''}</div>
    <div class="meta">${meta}</div>
    ${id ? `<div class="spark" id="${id}"></div>` : ''}
  </section>`;

export function table(cols, rows, { onRow, rowKey, max = 520, empty = 'No rows' } = {}) {
  const id = 't' + Math.random().toString(36).slice(2, 8);
  const html = `<div class="tbl-wrap" style="max-height:${max}px"><table class="tbl" id="${id}">
    <thead><tr>${cols.map((c) => `<th class="${c.r ? 'r' : ''}">${c.h}</th>`).join('')}</tr></thead>
    <tbody>${rows.length ? rows.map((r, i) => `<tr class="${onRow ? 'click' : ''}" data-i="${i}" ${rowKey ? `data-k="${esc(rowKey(r))}"` : ''}>${cols.map((c) => `<td class="${c.r ? 'r' : ''}">${c.f ? c.f(r) : esc(r[c.k])}</td>`).join('')}</tr>`).join('') : `<tr><td colspan="${cols.length}" style="text-align:center;color:var(--ink-3);padding:24px">${empty}</td></tr>`}</tbody></table></div>`;
  return { html, bind: () => { if (onRow) document.getElementById(id)?.addEventListener('click', (e) => { const tr = e.target.closest('tr[data-i]'); if (tr) onRow(rows[+tr.dataset.i], tr); }); } };
}

export function toast(msg, err = false) {
  const t = document.createElement('div');
  t.className = 'toast' + (err ? ' err' : '');
  t.textContent = msg;
  document.getElementById('toasts').append(t);
  setTimeout(() => t.remove(), 4200);
}

export const drawer = {
  open(title, html) {
    document.getElementById('drawerTitle').innerHTML = title;
    document.getElementById('drawerBody').innerHTML = html;
    document.getElementById('drawer').setAttribute('aria-hidden', 'false');
  },
  close() { document.getElementById('drawer').setAttribute('aria-hidden', 'true'); disposeIn(document.getElementById('drawerBody')); },
  body: () => document.getElementById('drawerBody'),
};

export function download(name, text, type = 'text/plain') {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([text], { type }));
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}

// ------------------------------------------------------------------ charts
const charts = new Set();
export function chart(el, option) {
  if (typeof el === 'string') el = document.getElementById(el);
  if (!el) return null;
  const c = echarts.init(el, null, { renderer: 'canvas' });
  c.setOption(theme(option));
  charts.add(c);
  return c;
}
export function disposeIn(root) {
  for (const c of [...charts]) {
    const d = c.getDom();
    if (!d.isConnected || (root && root.contains(d))) { c.dispose(); charts.delete(c); }
  }
}
export function disposeAll() { for (const c of charts) c.dispose(); charts.clear(); }
window.addEventListener('resize', () => charts.forEach((c) => c.resize()));

function theme(o) {
  const ink2 = css('--ink-2'), ink3 = css('--ink-3'), grid = css('--grid'), line = css('--line-2'), s1 = css('--s1');
  const axis = (a) => ({
    axisLine: { lineStyle: { color: line } }, axisTick: { show: false },
    axisLabel: { color: ink3, fontFamily: 'IBM Plex Sans', fontSize: 11, hideOverlap: true },
    splitLine: { lineStyle: { color: grid } }, nameTextStyle: { color: ink3, fontSize: 11 }, ...a,
  });
  const fix = (a) => (Array.isArray(a) ? a.map(axis) : a ? axis(a) : a);
  return {
    animationDuration: 500, textStyle: { fontFamily: 'IBM Plex Sans', color: ink2 },
    grid: { left: 48, right: 16, top: 26, bottom: 32, containLabel: false },
    tooltip: {
      trigger: 'axis', backgroundColor: s1, borderColor: line, borderWidth: 1, padding: [7, 10],
      textStyle: { color: css('--ink'), fontSize: 12 }, extraCssText: 'box-shadow:' + css('--shadow') + ';border-radius:4px',
      axisPointer: { lineStyle: { color: line }, crossStyle: { color: line } },
    },
    legend: { textStyle: { color: ink2, fontSize: 11.5 }, itemWidth: 12, itemHeight: 8, top: 0, right: 0, icon: 'roundRect' },
    ...o,
    xAxis: fix(o.xAxis), yAxis: fix(o.yAxis),
  };
}
export const series = (i) => css(`--s-${i}`);

// --------------------------------------------------------------------- maps
export function makeMap(el, { center = [-1.875, 30.21], zoom = 13 } = {}) {
  const m = L.map(el, { zoomControl: true, attributionControl: true, preferCanvas: true }).setView(center, zoom);
  const dark = document.documentElement.dataset.theme === 'dark';
  const layers = {
    Satellite: L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', { maxZoom: 19, attribution: 'Imagery © Esri, Maxar, Earthstar' }),
    Streets: L.tileLayer(`https://{s}.basemaps.cartocdn.com/${dark ? 'dark_all' : 'light_all'}/{z}/{x}/{y}{r}.png`, { maxZoom: 19, subdomains: 'abcd', attribution: '© OpenStreetMap © CARTO' }),
    Terrain: L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}', { maxZoom: 19, attribution: '© Esri' }),
  };
  let pref = 'Satellite';
  try { pref = localStorage.getItem('rinda.basemap') || 'Satellite'; } catch (e) {}
  (layers[pref] || layers.Satellite).addTo(m);
  L.control.layers(layers, null, { position: 'topright' }).addTo(m);
  m.on('baselayerchange', (e) => { try { localStorage.setItem('rinda.basemap', e.name); } catch (er) {} });
  L.control.scale({ imperial: false, position: 'bottomleft' }).addTo(m);
  setTimeout(() => m.invalidateSize(), 50);
  const host = typeof el === 'string' ? document.getElementById(el) : el;
  const ro = new ResizeObserver(() => { if (host.isConnected && m._mapPane) m.invalidateSize(); });
  ro.observe(host);
  m.on('unload', () => ro.disconnect());
  return m;
}
export function legendControl(map, html, pos = 'bottomright') {
  const c = L.control({ position: pos });
  c.onAdd = () => { const d = L.DomUtil.create('div', 'map-ctl'); d.innerHTML = html; L.DomEvent.disableClickPropagation(d); return d; };
  c.addTo(map);
  return c;
}
export const riskLegend = () => `<b>Verification priority</b>
  <div class="row"><svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><path d="M7 1.5l5.5 10h-11z" fill="var(--cat-high)" stroke="#0b1015"/></svg>High</div>
  <div class="row"><span class="dot" style="background:var(--s1);border:2px dashed var(--cat-insufficient)"></span>Insufficient evidence</div>
  <div class="row"><span class="dot" style="background:var(--cat-lower);width:6px;height:6px"></span>Lower</div>
  <div class="row" style="margin-top:4px;color:var(--ink-3)">Points are 50 m monitoring circles</div>`;

const TRI = (fill) => L.divIcon({ className: 'tri-pin', iconSize: [16, 16], iconAnchor: [8, 10],
  html: `<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1.5l6.5 12h-13z" fill="${fill}" stroke="#0b1015" stroke-width="1.2"/></svg>` });

export const catOf = (r) => r.category || (r.conformal_decision === 'formation' ? 'high' : r.conformal_decision === 'uncertain' ? 'insufficient' : 'lower');

export function siteMarker(r, { onClick } = {}) {
  const cat = catOf(r);
  const tip = `<b class="mono">${r.site_id}</b> · ${CAT_LABEL[cat]}<br>p = ${r.probability.toFixed(3)} (calibrated) · 50 m monitoring circle`;
  let mk;
  if (cat === 'high') mk = L.marker([r.latitude, r.longitude], { icon: TRI(css('--cat-high')), keyboard: true, title: `${r.site_id}: high priority for verification` });
  else if (cat === 'insufficient' || cat === 'moderate') mk = L.circleMarker([r.latitude, r.longitude], { radius: 5.5, color: css(cat === 'moderate' ? '--warn' : '--cat-insufficient'), weight: 2, dashArray: '3 2', fillColor: css('--s1'), fillOpacity: 0.9 });
  else mk = L.circleMarker([r.latitude, r.longitude], { radius: 3.2, color: 'transparent', weight: 0, fillColor: css('--cat-lower'), fillOpacity: 0.8 });
  mk.bindTooltip(tip, { direction: 'top', offset: [0, -6] });
  if (onClick) mk.on('click', () => onClick(r));
  return mk;
}

// 50 m monitoring circles appear once the map is zoomed in far enough to read them
export function footprintLayer(map, rows) {
  const g = L.layerGroup(rows.map((r) => L.circle([r.latitude, r.longitude], { radius: 50, color: css('--ink-3'), weight: 1, fill: false, interactive: false })));
  const sync = () => { if (map.getZoom() >= 15) g.addTo(map); else g.remove(); };
  map.on('zoomend', sync); sync();
  return g;
}

export function banner(level, html) {
  const cls = { critical: 'crit', warn: 'warn', info: '', good: 'good' }[level] ?? '';
  return `<div class="note ${cls}" role="${level === 'critical' ? 'alert' : 'status'}">${html}</div>`;
}

export function modal(title, html, { wide = false } = {}) {
  const d = document.createElement('dialog');
  d.className = 'modal' + (wide ? ' wide' : '');
  d.setAttribute('aria-label', title.replace(/<[^>]+>/g, ''));
  d.innerHTML = `<form method="dialog" class="modal-h"><h2>${title}</h2><button class="icon-btn" aria-label="Close">${icon('x')}</button></form><div class="modal-b">${html}</div>`;
  document.body.append(d);
  d.addEventListener('close', () => d.remove());
  d.showModal();
  return d;
}

export const uuid = () => (crypto.randomUUID ? crypto.randomUUID() : 'u' + Date.now().toString(36) + Math.random().toString(36).slice(2));
