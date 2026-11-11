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
  // Lucide outlines (ISC licence), inlined so the console needs no external script
  dashboard: '<rect width="7" height="9" x="3" y="3" rx="1"/><rect width="7" height="5" x="14" y="3" rx="1"/><rect width="7" height="9" x="14" y="12" rx="1"/><rect width="7" height="5" x="3" y="16" rx="1"/>',
  map: '<path d="M14.106 5.553a2 2 0 0 0 1.788 0l3.659-1.83A1 1 0 0 1 21 4.619v12.764a1 1 0 0 1-.553.894l-4.553 2.277a2 2 0 0 1-1.788 0l-4.212-2.106a2 2 0 0 0-1.788 0l-3.659 1.83A1 1 0 0 1 3 19.381V6.618a1 1 0 0 1 .553-.894l4.553-2.277a2 2 0 0 1 1.788 0z"/><path d="M15 5.764v15"/><path d="M9 3.236v15"/>',
  cliplist: '<rect width="8" height="4" x="8" y="2" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><path d="M12 11h4"/><path d="M12 16h4"/><path d="M8 11h.01"/><path d="M8 16h.01"/>',
  send: '<path d="M14.536 21.686a.5.5 0 0 0 .937-.024l6.5-19a.496.496 0 0 0-.635-.635l-19 6.5a.5.5 0 0 0-.024.937l7.93 3.18a2 2 0 0 1 1.112 1.11z"/><path d="m21.854 2.147-10.94 10.939"/>',
  chart: '<path d="M3 3v16a2 2 0 0 0 2 2h16"/><path d="M18 17V9"/><path d="M13 17V5"/><path d="M8 17v-3"/>',
  sliders: '<path d="M21 4h-7"/><path d="M10 4H3"/><path d="M21 12h-9"/><path d="M8 12H3"/><path d="M21 20h-5"/><path d="M12 20H3"/><path d="M14 2v4"/><path d="M8 10v4"/><path d="M16 18v4"/>',
  maximize: '<path d="M8 3H5a2 2 0 0 0-2 2v3"/><path d="M21 8V5a2 2 0 0 0-2-2h-3"/><path d="M3 16v3a2 2 0 0 0 2 2h3"/><path d="M16 21h3a2 2 0 0 0 2-2v-3"/>',
  reset: '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/>',
  chevR: '<path d="m9 18 6-6-6-6"/>', chevL: '<path d="m15 18-6-6 6-6"/>',
  droplet: '<path d="M12 22a7 7 0 0 0 7-7c0-2-1-3.9-3-5.5s-3.500-4-4-6.500c-.5 2.500-2 4.900-4 6.500C6 11.100 5 13 5 15a7 7 0 0 0 7 7z"/>',
  calendar: '<path d="M8 2v4"/><path d="M16 2v4"/><rect width="18" height="18" x="3" y="4" rx="2"/><path d="M3 10h18"/>',
  camera: '<path d="M14.500 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.500-3z"/><circle cx="12" cy="13" r="3"/>',
  navigate: '<polygon points="3 11 22 2 13 21 11 13 3 11"/>',
  eye: '<path d="M2.062 12.348a1 1 0 0 1 0-.696 10.750 10.750 0 0 1 19.876 0 1 1 0 0 1 0 .696 10.750 10.750 0 0 1-19.876 0"/><circle cx="12" cy="12" r="3"/>',
  sync: '<path d="M3 12a9 9 0 0 1 9-9 9.750 9.750 0 0 1 6.740 2.740L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.750 9.750 0 0 1-6.740-2.740L3 16"/><path d="M8 16H3v5"/>',
  database: '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5V19A9 3 0 0 0 21 19V5"/><path d="M3 12A9 3 0 0 0 21 12"/>',
};
export const icon = (name, cls = '') => `<svg viewBox="0 0 24 24" class="${cls}" aria-hidden="true">${P[name] || ''}</svg>`;

// --------------------------------------------------------------- semantics
export const RISK = ['very_high', 'high', 'moderate', 'low', 'very_low'];
export const RISK_LABEL = { very_high: 'Very high', high: 'High', moderate: 'Moderate', low: 'Low', very_low: 'Very low' };
export const CAT = ['high', 'moderate', 'insufficient', 'lower'];
export const CAT_LABEL = { high: 'Potential surface-water formation signal', moderate: 'Moderate forecast priority', insufficient: 'Evidence insufficient', lower: 'Lower forecast priority' };
export const CAT_SHORT = { high: 'Potential signal', moderate: 'Moderate', insufficient: 'Evidence insufficient', lower: 'Lower priority' };
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
    axisLabel: { color: ink3, fontFamily: 'Inter, system-ui, sans-serif', fontSize: 11.5, hideOverlap: true },
    splitLine: { lineStyle: { color: grid } }, nameTextStyle: { color: ink3, fontSize: 11 }, ...a,
  });
  const fix = (a) => (Array.isArray(a) ? a.map(axis) : a ? axis(a) : a);
  return {
    animationDuration: 500, textStyle: { fontFamily: 'Inter, system-ui, sans-serif', color: ink2 },
    grid: { left: 48, right: 16, top: 26, bottom: 32, containLabel: false },
    tooltip: {
      trigger: 'axis', backgroundColor: s1, borderColor: line, borderWidth: 1, padding: [7, 10],
      textStyle: { color: css('--ink'), fontSize: 12 }, extraCssText: 'box-shadow:0 6px 20px rgba(38,51,46,.14);border-radius:8px',
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
  const layers = {
    Satellite: L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', { maxZoom: 19, attribution: 'Imagery © Esri, Maxar, Earthstar' }),
    Streets: L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '© OpenStreetMap contributors' }),
    Terrain: L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}', { maxZoom: 19, attribution: '© Esri' }),
  };
  let pref = 'Streets';
  try { pref = localStorage.getItem('rinda.basemap') || 'Streets'; } catch (e) {}
  (layers[pref] || layers.Satellite).addTo(m);
  // Basemap tiles come from the internet. Data layers are served by RINDA and still draw without them.
  let warned = false;
  Object.values(layers).forEach((l) => l.on('tileerror', () => {
    if (warned) return; warned = true;
    const n = document.createElement('div'); n.className = 'map-note'; n.setAttribute('role', 'status');
    n.textContent = 'Background map tiles need an internet connection. RINDA data layers are still shown.';
    m.getContainer().parentElement.append(n);
  }));
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
export const riskLegend = () => `<b>Research-only forecast</b>
  <div class="row"><svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><path d="M7 1.5l5.5 10h-11z" fill="var(--cat-high)" stroke="#fff"/></svg>Potential water signal</div>
  <div class="row"><span class="dot" style="background:var(--s1);border:2px dashed var(--cat-insufficient)"></span>Evidence insufficient</div>
  <div class="row"><span class="dot" style="background:var(--cat-lower);width:6px;height:6px"></span>Lower priority</div>
  <div class="row" style="margin-top:4px;color:var(--ink-3)">Points are 50 m monitoring circles</div>`;

const TRI = (fill) => L.divIcon({ className: 'tri-pin', iconSize: [16, 16], iconAnchor: [8, 10],
  html: `<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1.5l6.5 12h-13z" fill="${fill}" stroke="#fff" stroke-width="1.4"/></svg>` });

export const catOf = (r) => r.category || (r.conformal_decision === 'formation' ? 'high' : r.conformal_decision === 'uncertain' ? 'insufficient' : 'lower');

export function siteMarker(r, { onClick } = {}) {
  const cat = catOf(r);
  const tip = `<b class="mono">${r.site_id}</b> · ${CAT_LABEL[cat]}<br>p = ${r.probability.toFixed(3)} (calibrated) · 50 m monitoring circle`;
  let mk;
  if (cat === 'high') mk = L.marker([r.latitude, r.longitude], { icon: TRI(css('--cat-high')), keyboard: true, title: `${r.site_id}: potential surface-water formation signal` });
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

// ------------------------------------------------------------ 2026-11 helpers
export const NODATA = 'No data available';
export const val = (v, d = 0) => (v == null ? `<span class="nodata">${NODATA}</span>` : fmt.n(v, d));
export const stat = (label, value, meta = '', ic = 'info', tone = '') => `
  <section class="stat"><div class="ic ${tone}">${icon(ic)}</div><div class="l">${label}</div>
  ${value == null ? `<div class="v none">${NODATA}</div>` : `<div class="v">${typeof value === 'number' ? fmt.n(value) : value}</div>`}
  ${meta ? `<div class="m">${meta}</div>` : ''}</section>`;
export const pill = (label) => `<span class="pill ${esc(String(label).split(' ')[0])}">${esc(label)}</span>`;
const EVI = {
  predicted: ['Predicted', 'forecast'], satellite_observed: ['Satellite-observed', 'layers'], sensor_observed: ['Sensor-observed', 'network'],
  field_recorded: ['Field record, not verified', 'cliplist'], field_verified: ['Field-verified', 'check'], survey_record: ['Survey record', 'cliplist'],
  entomologically_confirmed: ['Entomologically confirmed', 'alert'], treatment_authorised: ['Treatment authorised', 'shield'],
  treatment_rejected: ['Treatment not authorised', 'x'], treatment_completed: ['Treatment completed', 'check'], follow_up: ['Follow-up', 'replay'],
  predicted_or_satellite_only: ['Predicted or satellite-observed only', 'layers'],
};
export const evi = (k) => { const e = EVI[k] || [k, 'info']; return `<span class="evi ${k === 'predicted_or_satellite_only' ? 'satellite_observed' : k}">${icon(e[1])}${e[0]}</span>`; };
export const EVI_LABEL = Object.fromEntries(Object.entries(EVI).map(([k, v]) => [k, v[0]]));
const ST_TONE = { not_assessed: 'st-mute', inspection_required: 'st-info', evidence_insufficient: 'st-warn', eligible_for_review: 'st-warn', authorised: 'st-info',
  rejected: 'st-mute', completed: 'st-good', follow_up_due: 'st-crit', closed: 'st-good' };
export const opStatus = (k, label) => `<span class="status ${ST_TONE[k] || 'st-mute'}">${esc(label || k)}</span>`;
export function auditNote(a, { open = false } = {}) {
  if (!a) return '';
  return `<div class="audit" role="note">${icon('alert')}<div><b>${esc(a.title)}.</b> ${esc(a.summary)}
    <details ${open ? 'open' : ''}><summary>What the audit found</summary><ul>${a.findings.map((f) => `<li>${esc(f)}</li>`).join('')}</ul>
    <p style="margin:8px 0 0;color:var(--ink-2)"><b>Before its status can change:</b> ${esc(a.required_before_upgrade)}</p></details></div></div>`;
}
export const empty = (title, text = '', ic = 'info') => `<div class="empty">${icon(ic)}<b>${title}</b>${text ? `<span>${text}</span>` : ''}</div>`;
export const tip = (text, help) => `<span class="tip" title="${esc(help)}">${text}</span>`;
export const ramp = (t) => { // light to dark, one hue
  const a = [227, 237, 245], b = [31, 78, 121], k = Math.max(0, Math.min(1, t));
  return `rgb(${a.map((x, i) => Math.round(x + (b[i] - x) * k)).join(',')})`;
};
