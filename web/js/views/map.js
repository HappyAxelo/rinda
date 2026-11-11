// National surveillance map: one map, six evidence layers, each loaded only when
// switched on. Predicted, satellite-observed, sensor-observed, field and
// operational records keep their own symbols and are never merged.
import { api, makeMap, icon, esc, fmt, css, pill, chart, disposeIn, toast, ramp, empty, opStatus, NODATA } from '../ui.js';
import { params, setIssue } from '../app.js';
import { loadSummary } from './dossier.js';

let map, groups = {}, redraws = {}, data = {}, boundsLayer = {}, ST, root;
const S = { date: null, lastTop: null, cell: '', inSector: false, earthMode: 'wet', compare: '', model: '',
  on: { forecast: true, earth: false, iot: false, ento: false, meteo: false, ops: false }, opacity: {} };

export function destroy() {
  if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
  map?.remove(); map = null; groups = {}; redraws = {}; data = {};
}

const LAYERS = [
  ['forecast', 'Forecast signal', 'Predicted · research-only'],
  ['earth', 'Earth observation', 'Satellite-observed · Sentinel-1 and Sentinel-2'],
  ['iot', 'IoT environmental nodes', 'Sensor-observed'],
  ['ento', 'Entomological surveillance', 'Field inspections and surveys'],
  ['meteo', 'Meteorology', 'Weather station record'],
  ['ops', 'Operations', 'Tasks, decisions, treatments, missions'],
];
const EARTH_MODES = { wet: 'Water signal on latest pass', new_signal: 'New water signal since previous pass', changed: 'Change against comparison date', vv_db: 'VV backscatter (dB)', vh_db: 'VH backscatter (dB)',
  ndwi: 'Sentinel-2 NDWI', mndwi: 'Sentinel-2 MNDWI', water_frequency: 'Historical water frequency' };
const OPS_COLOR = { inspection_required: '--info', evidence_insufficient: '--warn-fill', eligible_for_review: '--signal', authorised: '--s-4', rejected: '--ink-3', completed: '--good', follow_up_due: '--terra', closed: '--sage', not_assessed: '--cat-lower' };
const ENTO = { breeding_confirmed: ['Anopheles larvae confirmed', 'dm', '--terra'], larvae_observed: ['Larvae found, species not confirmed', 'sq', '--signal'], larvae_negative: ['Dipped, no larvae', 'ring', '--ink-3'],
  water_observed: ['Water seen, not dipped', '', '--water'], survey_positive: ['Survey sample, larvae found', 'sq', '--s-6'], survey_negative: ['Survey sample, none found', 'ring', '--s-7'] };

export async function render(el, st) {
  ST = st; root = el;
  if (S.lastTop !== st.date) { S.date = st.date; S.lastTop = st.date; }
  const areas = await api.get('/api/geo/areas');
  const cells = areas.filter((a) => a.level === 'cell');
  const issues = st.boot.horizons[st.h].dates;
  el.innerHTML = `
  <div class="mapws" id="mapws">
    <aside class="mapctl" id="mapctl" aria-label="Map controls">
      <div class="mapctl-head" style="display:flex;justify-content:space-between;align-items:center;gap:8px"><h2>Surveillance map</h2><button class="btn sm only-narrow" id="ctlToggle" aria-expanded="true">${icon('layers')}Layers</button></div>
      <div class="searchbox">${icon('search')}<input id="mSearch" type="search" placeholder="Search a cell, location or habitat code" aria-label="Search by administrative area or habitat ID" autocomplete="off"><div class="results" id="mResults" hidden></div></div>
      <div class="field"><label for="mArea">Area</label><select id="mArea" ${cells.length ? '' : 'disabled'}><option value="">Gikomero Sector and surroundings</option>${cells.map((c) => `<option value="${esc(c.name)}" ${S.cell === c.name ? 'selected' : ''}>${esc(c.name)} Cell · ${c.n_sites} locations</option>`).join('')}</select>
        <span class="hint">Rwanda › Kigali City › Gasabo › Gikomero. No other area is onboarded.${cells.length ? '' : ' Cell boundaries are not loaded yet (NISR files needed).'}</span></div>
      <label class="check"><input type="checkbox" id="mInSector" ${S.inSector ? 'checked' : ''}> Only locations inside Gikomero Sector</label>
      <div class="field"><label for="mDate">Date</label><input type="date" id="mDate" value="${S.date}" min="2023-03-01" max="${st.boot.server_date}">
        <div class="timeline-ctl" role="group" aria-label="Historical timeline"><button class="icon-btn" id="mPrev" aria-label="Earlier date">${icon('chevL')}</button><input type="range" id="mSlide" min="0" max="${issues.length - 1}" value="${Math.max(0, issues.indexOf(S.date))}" aria-label="Timeline of forecast issue dates"><button class="icon-btn" id="mNext" aria-label="Later date">${icon('chevR')}</button></div>
        <span class="hint" id="mDateHint"></span></div>
      <div class="sec">Layers</div>
      ${LAYERS.map(([k, name, sub]) => `<div class="layer ${S.on[k] ? 'on' : ''}" data-layer="${k}">
        <label><input type="checkbox" ${S.on[k] ? 'checked' : ''} data-toggle="${k}"><span>${name}<small>${sub}</small></span></label>
        <div class="lbody"><div data-body="${k}"></div><label style="display:grid;gap:2px">Transparency<input type="range" min="20" max="100" value="${S.opacity[k] ?? 100}" data-opacity="${k}" aria-label="${name} transparency"></label></div></div>`).join('')}
      <div class="layer on"><label><input type="checkbox" id="mBounds" checked><span>Boundaries<small>Sector and interpreted marshland</small></span></label>
        <div class="lbody"><div class="lg"><span><i class="ring" style="color:var(--brand)"></i>Gikomero Sector (geoBoundaries, NISR 2012)</span>${cells.length ? '<span><i class="ring" style="color:var(--sage)"></i>Cells</span>' : ''}<span><i class="sq" style="background:rgba(59,127,186,.25)"></i>Marshland, interpreted and preliminary</span></div></div></div>
      <p class="hint" style="margin:0">Background maps load from the internet. RINDA's own layers do not need them.</p>
    </aside>
    <div class="mapmain">
      <div class="map" id="svMap" role="region" aria-label="Surveillance map"></div>
      <div class="map-chip" id="mChips"></div>
      <div class="map-tools"><button class="icon-btn" id="mReset" aria-label="Reset map" title="Reset map">${icon('reset')}</button><button class="icon-btn" id="mFull" aria-label="Full screen" title="Full screen">${icon('maximize')}</button></div>
    </div>
    <aside class="mapside" id="mapside" hidden aria-label="Location details"><header><h3 id="sideTitle"></h3><button class="icon-btn" id="sideClose" aria-label="Close details">${icon('x')}</button></header><div class="body" id="sideBody"></div></aside>
  </div>`;

  map = makeMap('svMap', { zoom: 13 });
  LAYERS.forEach(([k], i) => {
    const p = map.createPane(`p-${k}`); p.style.zIndex = 410 + i * 5; p.style.opacity = (S.opacity[k] ?? 100) / 100;
    groups[k] = L.layerGroup().addTo(map);
  });
  map.createPane('p-bounds').style.zIndex = 405;
  const b = await api.get('/api/geo/boundaries');
  boundsLayer.g = L.layerGroup([
    L.geoJSON(b.marshland, { pane: 'p-bounds', style: { color: css('--water'), weight: 1, fillColor: css('--water'), fillOpacity: 0.16 }, interactive: false }),
    L.geoJSON({ type: 'FeatureCollection', features: b.cells }, { pane: 'p-bounds', style: { color: css('--sage'), weight: 1.5, dashArray: '4 4', fill: false }, interactive: false }),
    L.geoJSON(b.sector, { pane: 'p-bounds', style: { color: css('--brand'), weight: 2.5, fill: false }, interactive: false }),
  ]).addTo(map);
  boundsLayer.sector = L.geoJSON(b.sector).getBounds();
  boundsLayer.cells = Object.fromEntries(b.cells.map((c) => [c.properties.name, L.geoJSON(c).getBounds()]));
  fit();
  map.on('moveend', () => Object.keys(redraws).forEach((k) => S.on[k] && redraws[k]()));

  // ------------------------------------------------------------- controls
  const $ = (id) => document.getElementById(id);
  el.querySelectorAll('[data-toggle]').forEach((c) => c.addEventListener('change', () => {
    const k = c.dataset.toggle; S.on[k] = c.checked;
    c.closest('.layer').classList.toggle('on', c.checked);
    if (c.checked) load(k); else { groups[k].clearLayers(); delete redraws[k]; chips(); }
  }));
  el.querySelectorAll('[data-opacity]').forEach((r) => r.addEventListener('input', () => { S.opacity[r.dataset.opacity] = +r.value; map.getPane(`p-${r.dataset.opacity}`).style.opacity = r.value / 100; }));
  $('mBounds').addEventListener('change', (e) => { if (e.target.checked) boundsLayer.g.addTo(map); else boundsLayer.g.remove(); });
  $('mArea').addEventListener('change', (e) => { S.cell = e.target.value; fit(); reloadAll(); });
  $('mInSector').addEventListener('change', (e) => { S.inSector = e.target.checked; Object.keys(redraws).forEach((k) => redraws[k]()); });
  const setDate = (d) => {
    S.date = d; $('mDate').value = d;
    const i = issues.indexOf(d); if (i >= 0) { $('mSlide').value = i; S.lastTop = d; setIssue(d); }
    reloadAll();
  };
  $('mDate').addEventListener('change', (e) => e.target.value && setDate(e.target.value));
  $('mSlide').addEventListener('input', (e) => setDate(issues[+e.target.value]));
  const step = (n) => { const i = Math.max(0, Math.min(issues.length - 1, (issues.indexOf(S.date) < 0 ? issues.filter((d) => d <= S.date).length - 1 : issues.indexOf(S.date)) + n)); setDate(issues[i]); };
  $('mPrev').addEventListener('click', () => step(-1)); $('mNext').addEventListener('click', () => step(1));
  $('mReset').addEventListener('click', () => { S.cell = ''; S.inSector = false; $('mArea').value = ''; $('mInSector').checked = false; closeSide(); fit(); reloadAll(); });
  $('mFull').addEventListener('click', () => { const w = $('mapws'); if (document.fullscreenElement) document.exitFullscreen(); else w.requestFullscreen?.().catch(() => toast('Full screen is not available in this browser.', true)); });
  $('sideClose').addEventListener('click', closeSide);
  $('ctlToggle').addEventListener('click', (e) => { const c = $('mapctl').classList.toggle('closed'); e.currentTarget.setAttribute('aria-expanded', String(!c)); map.invalidateSize(); });
  if (window.matchMedia('(max-width: 900px)').matches) { $('mapctl').classList.add('closed'); $('ctlToggle').setAttribute('aria-expanded', 'false'); }
  search($('mSearch'), $('mResults'));

  await reloadAll();
  const focus = params().get('focus');
  if (focus) locate(focus);
}

function fit() {
  const bb = S.cell && boundsLayer.cells[S.cell] ? boundsLayer.cells[S.cell] : boundsLayer.sector;
  map.fitBounds(bb, { padding: [24, 24] });
}
const reloadAll = () => Promise.all(LAYERS.map(([k]) => (S.on[k] ? load(k) : null)));
const keep = (p) => !S.inSector || p.in_sector !== false;

async function load(k) {
  const body = root.querySelector(`[data-body="${k}"]`);
  body.innerHTML = '<div class="skeleton" style="height:44px"></div>';
  try { await LOAD[k](body); } catch (e) { if (!e.cancelled) body.innerHTML = `<p style="color:var(--crit);margin:0">Could not load this layer: ${esc(e.message)}</p>`; }
  chips();
}

function chips() {
  const out = [];
  const f = data.forecast;
  if (S.on.forecast && f) out.push(f.available ? `<span class="pill ${f.state === 'Forecast stale' ? 'Stale' : 'Historical'}">${esc(f.state)} · issued ${fmt.date(f.issue_date)} · ${f.horizon}-day</span>` : '<span class="pill Unavailable">Forecast unavailable</span>');
  if (S.on.earth && data.earth) out.push(`<span class="pill Historical">Satellite-observed · passes to ${fmt.date(data.earth.date)}</span>`);
  if (S.on.meteo && data.meteo?.available) out.push(`<span class="pill Historical">Weather record · ${fmt.date(data.meteo.observed_date)}</span>`);
  document.getElementById('mChips').innerHTML = out.join('');
}

// ------------------------------------------------------------- clustering
function clustered(k, pts, draw, tone) {
  const pane = `p-${k}`;
  redraws[k] = () => {
    const g = groups[k]; g.clearLayers();
    const z = map.getZoom(), bb = map.getBounds().pad(0.15);
    const vis = pts.filter((p) => keep(p) && bb.contains([p.lat, p.lon]));
    if (z >= 14 || vis.length <= 70) { vis.forEach((p) => draw(p, pane).addTo(g)); return; }
    const buckets = new Map();
    vis.forEach((p) => { const q = map.project([p.lat, p.lon], z); const key = `${Math.floor(q.x / 62)}_${Math.floor(q.y / 62)}`; if (!buckets.has(key)) buckets.set(key, []); buckets.get(key).push(p); });
    buckets.forEach((arr) => {
      if (arr.length === 1) { draw(arr[0], pane).addTo(g); return; }
      const lat = arr.reduce((a, p) => a + p.lat, 0) / arr.length, lon = arr.reduce((a, p) => a + p.lon, 0) / arr.length;
      const { color, note } = tone(arr), size = arr.length > 60 ? 44 : arr.length > 15 ? 38 : 32;
      L.marker([lat, lon], { pane, icon: L.divIcon({ className: 'pin', iconSize: [size, size], html: `<div class="cluster" style="width:${size}px;height:${size}px;background:${color}">${arr.length}</div>` }), title: `${arr.length} locations. ${note}` })
        .bindTooltip(`<b>${arr.length} locations</b><br>${note}<br>Select to zoom in`).on('click', () => map.fitBounds(L.latLngBounds(arr.map((p) => [p.lat, p.lon])), { padding: [40, 40], maxZoom: 15 })).addTo(g);
    });
  };
  redraws[k]();
}
const shape = (kind, color, size = 14) => {
  const s = kind === 'dm' ? `<div style="width:${size - 3}px;height:${size - 3}px;background:${color};transform:rotate(45deg);border:2px solid #fff;box-shadow:0 0 0 1px ${color};margin:1px"></div>`
    : kind === 'sq' ? `<div style="width:${size - 2}px;height:${size - 2}px;background:${color};border-radius:3px;border:2px solid #fff;box-shadow:0 0 0 1px ${color}"></div>`
      : kind === 'ring' ? `<div style="width:${size - 2}px;height:${size - 2}px;border-radius:50%;border:3px solid ${color};background:#fff"></div>`
        : `<div style="width:${size - 2}px;height:${size - 2}px;border-radius:50%;background:${color};border:2px solid #fff;box-shadow:0 0 0 1px ${color}"></div>`;
  return L.divIcon({ className: 'pin', iconSize: [size, size], iconAnchor: [size / 2, size / 2], html: s });
};
const TRI = (c) => L.divIcon({ className: 'pin', iconSize: [18, 18], iconAnchor: [9, 11], html: `<svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true"><path d="M9 2l7.500 13h-15z" fill="${c}" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg>` });

// ---------------------------------------------------------------- layers
const LOAD = {
  async forecast(body) {
    const f = await api.get(`/api/map/forecast?h=${ST.h}&date=${S.date}${S.cell ? `&cell=${encodeURIComponent(S.cell)}` : ''}${S.model ? `&model=${encodeURIComponent(S.model)}` : ''}`);
    data.forecast = f;
    document.getElementById('mDateHint').textContent = f.available ? `Forecast shown: issued ${f.issue_date}, the latest on or before this date. It used only data available then.` : (f.message || '');
    if (!f.available) { groups.forecast.clearLayers(); delete redraws.forecast; body.innerHTML = `<p class="nodata" style="margin:0">Forecast unavailable. ${esc(f.message || '')}</p>`; return; }
    const n = (c) => f.rows.filter((r) => r.category === c).length;
    body.innerHTML = `<div class="lg">
      <span><svg width="14" height="14" viewBox="0 0 18 18" aria-hidden="true"><path d="M9 2l7.500 13h-15z" fill="var(--cat-high)"/></svg>Potential surface-water formation signal · ${n('high')}</span>
      <span><i class="ring" style="color:var(--cat-insufficient);border-style:dashed"></i>Evidence insufficient · ${n('insufficient')}</span>
      <span><i style="background:var(--cat-lower);width:8px;height:8px;margin:0 2px"></i>Lower forecast priority · ${n('lower')}</span></div>
      <div><b>Issued</b> ${fmt.date(f.issue_date)} · <b>target</b> ${fmt.date(f.target_date)} · ${f.horizon}-day<br><b>Model</b> ${esc(f.model.version)} · ${esc(f.model.status || 'research only')}${f.age_days != null ? `<br><b>Age</b> ${f.age_days} days: ${esc(f.state.toLowerCase())}` : ''}</div>
      <details class="tech"><summary>Known limitations</summary><div><ul style="margin:0;padding-left:18px">${f.limitations.map((x) => `<li>${esc(x)}</li>`).join('')}</ul></div></details>
      <span class="hint">A forecast is a lead for inspection. It is not an observation and not a breeding site.</span>`;
    const pts = [...f.rows].sort((a, z) => (a.category === 'high') - (z.category === 'high'));
    clustered('forecast', pts, (r, pane) => {
      let m;
      if (r.category === 'high') m = L.marker([r.lat, r.lon], { pane, icon: TRI(css('--cat-high')), title: `${r.site_id}: potential surface-water formation signal` });
      else if (r.category === 'lower') m = L.circleMarker([r.lat, r.lon], { pane, radius: 3.4, weight: 0, fillColor: css('--cat-lower'), fillOpacity: 0.85 });
      else m = L.circleMarker([r.lat, r.lon], { pane, radius: 6, color: css('--cat-insufficient'), weight: 2, dashArray: '3 2', fillColor: '#fff', fillOpacity: 0.9 });
      return m.bindTooltip(`<b class="mono">${r.site_id}</b> · Predicted<br>${esc(r.label)}<br>Score ${r.probability.toFixed(2)} · research-only`, { direction: 'top' }).on('click', () => openSite(r.site_id));
    }, (arr) => { const h = arr.filter((r) => r.category === 'high').length; return { color: h ? css('--cat-high') : css('--ink-3'), note: h ? `${h} with a potential water signal (predicted)` : 'No potential water signal predicted' }; });
  },

  async earth(body) {
    const e = await api.get(`/api/map/earth?date=${S.date}${S.compare ? `&compare=${S.compare}` : ''}${S.cell ? `&cell=${encodeURIComponent(S.cell)}` : ''}`);
    data.earth = e;
    if (S.earthMode === 'changed' && !S.compare) S.earthMode = 'wet';
    const mode = S.earthMode, cont = ['vv_db', 'vh_db', 'ndwi', 'mndwi', 'water_frequency'].includes(mode);
    const vals = cont ? e.rows.map((r) => r[mode]).filter((v) => v != null) : [];
    const lo = cont ? Math.min(...vals) : 0, hi = cont ? Math.max(...vals) : 1;
    // low backscatter and high water index both point towards water: darker = more water-like
    const t = (v) => { const k = (v - lo) / ((hi - lo) || 1); return mode.endsWith('_db') ? 1 - k : k; };
    const noS2 = ['ndwi', 'mndwi'].includes(mode) && !vals.length;
    body.innerHTML = `<select id="eMode" aria-label="Earth observation product">${Object.entries(EARTH_MODES).map(([k, l]) => `<option value="${k}" ${k === mode ? 'selected' : ''} ${k === 'changed' && !S.compare ? 'disabled' : ''}>${l}</option>`).join('')}</select>
      <label style="display:grid;gap:2px">Compare with an earlier date<input type="date" id="eCmp" value="${S.compare}" min="2023-03-01" max="${S.date}"></label>
      ${cont ? (noS2 ? `<p class="nodata" style="margin:0">${NODATA} for Sentinel-2 on this date.</p>` : `<div><div class="ramp" style="${mode.endsWith('_db') ? 'transform:scaleX(-1)' : ''}"></div><div class="ramp-l"><span>${lo.toFixed(2)}</span><span>${hi.toFixed(2)}</span></div></div>`)
    : `<div class="lg"><span><i style="background:var(--water)"></i>${mode === 'changed' ? 'Signal differs from comparison date' : mode === 'new_signal' ? 'New water signal since previous pass' : 'Water signal on latest pass'} · ${e.rows.filter((r) => r[mode]).length}</span><span><i style="background:var(--cat-lower);width:8px;height:8px;margin:0 2px"></i>${mode === 'changed' ? 'Unchanged' : 'No signal'}</span></div>`}
      <div><b>Passes up to</b> ${fmt.date(e.date)}<br><b>Water rule</b> VV ≤ ${e.thresholds.vv_db_max} dB and VH ≤ ${e.thresholds.vh_db_max} dB${['ndwi', 'mndwi'].includes(mode) ? '<br><b>Index</b> latest clear Sentinel-2 value, 50 m mean' : ''}</div>
      <button class="btn sm" id="eProd">${icon('layers')}Imagery and metadata</button>
      <span class="hint">${esc(e.caution)}</span>`;
    body.querySelector('#eMode').addEventListener('change', (x) => { S.earthMode = x.target.value; load('earth'); });
    body.querySelector('#eCmp').addEventListener('change', (x) => { S.compare = x.target.value; if (S.compare) S.earthMode = 'changed'; load('earth'); });
    body.querySelector('#eProd').addEventListener('click', () => products(e));
    const pts = e.rows.filter((r) => !cont || r[mode] != null);
    clustered('earth', pts, (r, pane) => {
      const on = cont ? null : !!r[mode];
      const m = cont ? L.circleMarker([r.lat, r.lon], { pane, radius: 5.5, weight: 1, color: '#fff', fillColor: ramp(t(r[mode])), fillOpacity: 0.95 })
        : on ? L.circleMarker([r.lat, r.lon], { pane, radius: 6.5, weight: 2, color: '#fff', fillColor: css('--water'), fillOpacity: 0.95 })
          : L.circleMarker([r.lat, r.lon], { pane, radius: 3.2, weight: 0, fillColor: css('--cat-lower'), fillOpacity: 0.8 });
      return m.bindTooltip(`<b class="mono">${r.site_id}</b> · Satellite-observed<br>Pass ${r.pass_date}: ${r.wet ? 'water signal' : 'no water signal'}<br>VV ${r.vv_db} dB · VH ${r.vh_db} dB${r.mndwi != null ? `<br>MNDWI ${r.mndwi} (${r.s2_age_days} d old)` : ''}`, { direction: 'top' }).on('click', () => openSite(r.site_id));
    }, (arr) => { const w = arr.filter((r) => (cont ? false : r[mode])).length; return { color: w ? css('--water') : css('--ink-3'), note: cont ? EARTH_MODES[mode] : `${w} with ${mode === 'changed' ? 'a change' : 'a water signal'} (satellite-observed)` }; });
  },

  async iot(body) {
    const r = await api.get('/api/map/iot');
    data.iot = r; groups.iot.clearLayers();
    const TONE = { Online: 'Current', Delayed: 'Delayed', Offline: 'Stale', Faulty: 'Stale', 'Not yet commissioned': 'Unavailable' };
    body.innerHTML = `${r.nodes.map((n) => `<div style="display:flex;justify-content:space-between;gap:8px;align-items:center"><button class="btn sm ghost" data-node="${esc(n.node_id)}" style="padding:0 6px"><span class="mono">${esc(n.node_id)}</span></button><span class="pill ${TONE[n.status] || 'Unavailable'}">${esc(n.status)}</span></div>`).join('') || `<p class="nodata" style="margin:0">No node registered.</p>`}
      <span class="hint">${esc(r.note)} Nodes appear on the map once they are installed and their position is recorded.</span>`;
    body.querySelectorAll('[data-node]').forEach((x) => x.addEventListener('click', () => node(x.dataset.node)));
    r.nodes.filter((n) => n.lat != null).forEach((n) => L.marker([n.lat, n.lon], { pane: 'p-iot', icon: shape('sq', css(n.status === 'Online' ? '--good' : n.status === 'Delayed' ? '--warn-fill' : '--ink-3'), 16), title: `${n.node_id}: ${n.status}` })
      .bindTooltip(`<b class="mono">${esc(n.node_id)}</b> · ${esc(n.status)}`).on('click', () => node(n.node_id)).addTo(groups.iot));
  },

  async ento(body) {
    const r = await api.get('/api/map/entomology');
    data.ento = r;
    const cells = Object.entries(r.by_cell || {});
    body.innerHTML = `<div class="lg">${Object.entries(ENTO).map(([, [l, s, c]]) => `<span><i class="${s}" style="${s === 'ring' ? `color:var(${c})` : `background:var(${c})`}"></i>${l}</span>`).join('')}</div>
      ${r.message ? `<p class="${r.restricted ? 'hint' : 'nodata'}" style="margin:0">${esc(r.message)}</p>` : ''}
      ${cells.length ? `<div>${cells.map(([c, v]) => `<div style="display:flex;justify-content:space-between"><span>${esc(c)}</span><span>${v.inspected} inspected · ${v.confirmed} confirmed</span></div>`).join('')}</div>` : ''}
      <span class="hint">Only a reviewed dipping record with Anopheles larvae counts as confirmed breeding.</span>`;
    clustered('ento', r.points, (p, pane) => {
      const [label, s, c] = ENTO[p.kind];
      return L.marker([p.lat, p.lon], { pane, icon: shape(s, css(c), 16), title: `${p.site_id}: ${label}` })
        .bindTooltip(`<b class="mono">${esc(p.site_id)}</b>${p.demo ? ' · DEMO' : ''}<br>${label}<br>Last inspection ${p.last_inspection} · ${esc(String(p.review_status).replace('_', ' '))}${p.larvae_per_dip != null ? `<br>${p.larvae_per_dip} larvae per dip` : ''}${p.species.length ? `<br>${p.species.join(', ').replace(/_/g, ' ')}` : ''}${p.stages.length ? `<br>${p.stages.join(', ').replace(/_/g, ' ')}` : ''}${p.pupae === 'yes' ? '<br>Pupae found' : ''}${p.resistance ? `<br>Resistance: ${esc(p.resistance)}` : ''}`).on('click', () => openSite(p.site_id));
    }, (arr) => { const c = arr.filter((p) => p.kind === 'breeding_confirmed').length; return { color: c ? css('--terra') : css('--brand'), note: `${c} with confirmed Anopheles larvae` }; });
  },

  async meteo(body) {
    const m = await api.get(`/api/map/meteo?date=${S.date}`);
    data.meteo = m; groups.meteo.clearLayers();
    const st = m.station;
    body.innerHTML = m.available ? `<div><b>${esc(st.name)}</b><br>Reading used: ${fmt.date(m.observed_date)}${m.lag_days ? ` (${m.lag_days} days before the selected date)` : ''}<br>Rain that day ${fmt.n(m.rainfall_mm, 1)} mm · 7 days ${m.rain_7d_mm} mm · 30 days ${m.rain_30d_mm} mm<br>Mean temperature ${m.temp_mean_c ?? NODATA} °C · humidity ${m.humidity_pct == null ? NODATA : `${m.humidity_pct}%`}</div>
      <div>${pill('Historical')} Record ends ${fmt.date(m.record_ends)}.</div>
      <span class="hint">${esc(m.wind_note)} ${esc(m.forecasts_note)} One station serves the whole area, so there is no spatial weather pattern to draw.</span>
      <button class="btn sm" id="meteoMore">${icon('chart')}Charts</button>` : `<p class="nodata" style="margin:0">${NODATA}. ${esc(m.message || '')}</p>`;
    body.querySelector('#meteoMore')?.addEventListener('click', () => weather(m));
    L.marker([st.lat, st.lon], { pane: 'p-meteo', icon: L.divIcon({ className: 'pin', html: '<div class="station-pin"></div>', iconSize: [16, 16] }), title: st.name })
      .bindTooltip(`<b>${esc(st.name)}</b><br>${m.available ? `Record to ${m.record_ends} · historical` : NODATA}`).on('click', () => m.available && weather(m)).addTo(groups.meteo);
  },

  async ops(body) {
    const r = await api.get('/api/map/operations');
    data.ops = r; groups.ops.clearLayers();
    const counts = Object.entries(r.status_counts || {});
    body.innerHTML = `<div class="lg">${Object.entries(r.status_labels).filter(([k]) => k !== 'not_assessed').map(([k, l]) => `<span><i style="background:var(${OPS_COLOR[k]})"></i>${l}${r.status_counts[k] ? ` · ${r.status_counts[k]}` : ''}</span>`).join('')}<span><i class="dm" style="background:var(--ink)"></i>Approved mission target</span></div>
      ${r.restricted ? `<p class="hint" style="margin:0">${esc(r.message)}</p>` : counts.length ? '' : `<p class="nodata" style="margin:0">No operational records yet. Markers appear when inspections are assigned.</p>`}`;
    (r.points || []).forEach((p) => L.marker([p.lat, p.lon], { pane: 'p-ops', icon: shape('', css(OPS_COLOR[p.status]), 17), title: `${p.site_id}: ${p.label}` })
      .bindTooltip(`<b class="mono">${esc(p.site_id)}</b><br>${esc(p.label)}${p.officer ? `<br>${esc(p.officer)} · due ${p.due}` : ''}`).on('click', () => openSite(p.site_id)).addTo(groups.ops));
    (r.mission_targets || []).forEach((t) => L.marker([t.lat, t.lon], { pane: 'p-ops', icon: shape('dm', css('--ink'), 13), title: `${t.mission}: ${t.kind} target` })
      .bindTooltip(`<b class="mono">${esc(t.site_id)}</b><br>${t.kind === 'treatment' ? 'Treatment mission target' : 'Inspection flight target'} · ${esc(t.mission)}`).addTo(groups.ops));
  },
};

// ------------------------------------------------------------ side panel
function side(title) {
  const s = document.getElementById('mapside');
  disposeIn(s);
  s.hidden = false; document.getElementById('mapws').classList.add('has-side');
  document.getElementById('sideTitle').innerHTML = title;
  setTimeout(() => map?.invalidateSize(), 60);
  return document.getElementById('sideBody');
}
function closeSide() {
  const s = document.getElementById('mapside'); if (!s) return;
  disposeIn(s); s.hidden = true; document.getElementById('mapws').classList.remove('has-side');
  setTimeout(() => map?.invalidateSize(), 60);
}
function openSite(id) { loadSummary(side(`<span class="mono">${esc(id)}</span>`), id, ST); }

async function locate(id) {
  const s = ST.boot.sites.find((x) => x.site_id === id);
  if (s) map.setView([s.latitude, s.longitude], 16);
  openSite(id);
}

async function node(id) {
  const body = side(`Node <span class="mono">${esc(id)}</span>`);
  body.innerHTML = '<div class="skeleton" style="height:160px"></div>';
  const n = await api.get(`/api/iot/nodes/${encodeURIComponent(id)}`);
  const vars = Object.keys(n.series);
  body.innerHTML = `<div style="display:flex;gap:8px;flex-wrap:wrap">${pill(n.status === 'Online' ? 'Current' : n.status === 'Delayed' ? 'Delayed' : 'Unavailable').replace(/>[^<]+</, `>${esc(n.status)}<`)}</div>
    <div class="kvs"><div><span>Node ID</span><span class="mono" style="display:inline;color:var(--ink)">${esc(n.node_id)}</span></div><div><span>GPS location</span>${n.position ? fmt.ll(n.position.lat, n.position.lon) : 'Not installed yet'}</div>
      <div><span>Last successful communication</span>${n.last_received_utc ? n.last_received_utc.replace('T', ' ').slice(0, 16) + ' UTC' : 'Never'}</div><div><span>Packets received</span>${n.packets}</div>
      <div><span>Delivery ratio</span>${n.pdr == null ? NODATA : fmt.pct(n.pdr)}</div><div><span>Readings out of range</span>${n.packets ? n.bad_readings : NODATA}</div></div>
    <section><h4 class="sec-h">Sensor inventory (planned design)</h4><div class="list" style="margin:0 -16px">${n.inventory.sensors.map((s) => `<div><span>${esc(s.part)}</span><span class="sub">${esc(s.measures.replace(/_/g, ' '))} · ${esc(s.unit)}</span></div>`).join('')}<div><span>${esc(n.inventory.controller)} + ${esc(n.inventory.radio)}</span><span class="sub">controller and radio</span></div></div></section>
    <section><h4 class="sec-h">Latest readings</h4>${n.latest.length ? `<div class="list" style="margin:0 -16px">${n.latest.map((r) => `<div><span>${esc(n.labels[r.variable] || r.variable)}</span><span class="sub">${r.value} ${esc(r.unit)} · ${r.observed_utc.replace('T', ' ').slice(0, 16)}${r.quality_flag ? ` · ${esc(r.quality_flag.replace(/_/g, ' '))}` : ''}</span></div>`).join('')}</div>` : `<p class="nodata" style="margin:0">${NODATA}. This node has not sent a reading.</p>`}</section>
    ${vars.map((v, i) => `<section><h4 class="sec-h">${esc(n.labels[v] || v)} (${esc(n.units[v] || '')})</h4><div class="chart sm" id="nc${i}"></div></section>`).join('')}
    <section><h4 class="sec-h">Missing measurements</h4><p style="margin:0;color:var(--ink-2)">${n.missing_measurements.length ? esc(n.missing_measurements.join(', ')) : 'None'}</p></section>`;
  vars.forEach((v, i) => chart(`nc${i}`, { grid: { left: 44, right: 12, top: 12, bottom: 26 }, xAxis: { type: 'time' }, yAxis: { type: 'value', scale: true },
    series: [{ type: 'line', showSymbol: n.series[v].length < 40, symbolSize: 6, data: n.series[v].map((p) => [p[0], p[1]]), lineStyle: { width: 2, color: css('--s-1') }, itemStyle: { color: css('--s-1') } }] }));
}

function weather(m) {
  const body = side('Weather station');
  body.innerHTML = `<div>${pill('Historical')}</div>
    <div class="kvs"><div><span>Station</span>${esc(m.station.name)}</div><div><span>Station ID</span><span class="mono" style="display:inline;color:var(--ink)">${esc(m.station.id)}</span></div>
      <div><span>Reading date</span>${fmt.date(m.observed_date)}</div><div><span>Record ends</span>${fmt.date(m.record_ends)}</div>
      <div><span>14-day rainfall</span>${m.rain_14d_mm} mm</div><div><span>Elevation</span>${m.station.elevation_m} m</div></div>
    <section><h4 class="sec-h">Daily rainfall (mm), 60 days to the reading</h4><div class="chart sm" id="wRain"></div></section>
    <section><h4 class="sec-h">Mean air temperature (°C)</h4><div class="chart sm" id="wTemp"></div></section>
    <p class="hint" style="margin:0">${esc(m.wind_note)} ${esc(m.forecasts_note)} ${m.humidity_note ? esc(m.humidity_note) : ''}</p>
    <a class="btn" href="#/network">${icon('network')}Full station record</a>`;
  const x = m.series.map((r) => r.date);
  chart('wRain', { grid: { left: 40, right: 10, top: 10, bottom: 24 }, xAxis: { type: 'category', data: x }, yAxis: { type: 'value' }, series: [{ type: 'bar', barMaxWidth: 6, data: m.series.map((r) => r.rainfall_mm), itemStyle: { color: css('--s-2'), borderRadius: [3, 3, 0, 0] } }] });
  chart('wTemp', { grid: { left: 40, right: 10, top: 10, bottom: 24 }, xAxis: { type: 'category', data: x }, yAxis: { type: 'value', scale: true }, series: [{ type: 'line', showSymbol: false, data: m.series.map((r) => r.temp_mean_c), lineStyle: { width: 2, color: css('--s-3') }, itemStyle: { color: css('--s-3') } }] });
}

function products(e) {
  const body = side('Imagery and metadata');
  const row = (p) => `<details class="tech"><summary>${esc(p.platform)} · ${fmt.date(p.acquired_date)}${p.cloud_pct != null ? ` · ${p.cloud_pct.toFixed(0)}% cloud` : ''}</summary><div><dl class="dl" style="grid-template-columns:auto 1fr;font-size:12.5px">
    <dt>Platform</dt><dd>${esc(p.platform)}</dd><dt>Acquired</dt><dd>${esc((p.acquired_utc || '').replace('T', ' ').replace('Z', ' UTC'))}</dd><dt>Processed</dt><dd>${p.processed_utc ? esc(String(p.processed_utc).slice(0, 19).replace('T', ' ')) : 'Not stated in the catalogue'}</dd>
    <dt>Product ID</dt><dd class="mono" style="word-break:break-all;text-align:left">${esc(p.product_id)}</dd><dt>Resolution</dt><dd>${p.resolution_m} m</dd><dt>Processing</dt><dd style="text-align:left">${esc(p.processing_method)}</dd>
    <dt>Cloud cover</dt><dd>${p.cloud_pct == null ? 'Not applicable (radar)' : `${p.cloud_pct.toFixed(1)}%`}</dd>${p.relative_orbit != null ? `<dt>Orbit</dt><dd>${p.relative_orbit}${p.orbit_state ? ` · ${esc(p.orbit_state)}` : ''}</dd>` : ''}
    <dt>Source</dt><dd style="text-align:left">${esc(p.storage_ref)}</dd><dt>Validation</dt><dd>${esc(p.validation_status)}</dd></dl></div></details>`;
  body.innerHTML = `<p style="margin:0;color:var(--ink-2)">Scenes covering the pilot area acquired on or before ${fmt.date(e.date)}. RINDA stores site-level values and this metadata; the rasters stay in the satellite repository.</p>
    ${e.products.length ? e.products.map(row).join('') : empty('No scenes catalogued before this date')}
    <div class="note warn"><b>Sentinel-1C.</b> ${esc(e.sentinel1c_note)}</div>
    <p class="hint" style="margin:0">Copernicus Sentinel data. Catalogue: 2,061 scenes, 2020 to 2026.</p>`;
}

function search(input, box) {
  let tmr;
  const hide = () => { box.hidden = true; };
  input.addEventListener('input', () => {
    clearTimeout(tmr);
    const q = input.value.trim();
    if (!q) { hide(); return; }
    tmr = setTimeout(async () => {
      try {
        const res = await api.get(`/api/search?q=${encodeURIComponent(q)}`);
        box.hidden = false;
        box.innerHTML = res.length ? res.slice(0, 20).map((r, i) => `<button data-i="${i}">${esc(r.label)}${r.type === 'area' && !r.onboarded ? ' <span class="hint">· not onboarded</span>' : ''}</button>`).join('') : '<button disabled>No match</button>';
        box.querySelectorAll('[data-i]').forEach((b) => b.addEventListener('click', () => {
          const r = res[+b.dataset.i]; hide(); input.value = '';
          if (r.type === 'area') {
            const name = r.label.split(' (')[0];
            if (boundsLayer.cells[name]) { S.cell = name; document.getElementById('mArea').value = name; fit(); reloadAll(); }
            else if (r.onboarded) { S.cell = ''; document.getElementById('mArea').value = ''; fit(); reloadAll(); }
            else toast(`${name} is not onboarded. RINDA holds data for the Gikomero pilot only.`, true);
          } else { map.setView([r.lat, r.lon], 16); openSite(r.id); }
        }));
      } catch (e) { /* cancelled or offline */ }
    }, 220);
  });
  input.addEventListener('keydown', (e) => { if (e.key === 'Escape') hide(); });
  document.addEventListener('click', (e) => { if (!e.target.closest('.searchbox')) hide(); });
}

export const setModel = (id) => { S.model = id; };
export { opStatus };
