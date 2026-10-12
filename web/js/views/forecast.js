import { api, card, fmt, icon, table, makeMap, legendControl, riskLegend, siteMarker, footprintLayer, catTag, css, CAT, CAT_LABEL, download, esc } from '../ui.js';

let map, layer, outcomeLayer;
export function destroy() { map?.remove(); map = null; }

export async function render(el, st) {
  const h = st.h;
  const fc = await api.get(`/api/forecast?h=${h}&date=${st.date}`);
  const s = fc.summary, m = fc.meta;
  const f = { cat: new Set(CAT), q: '', outcome: false };

  el.innerHTML = `
  <div class="page-head">
    <div><h1>Habitat formation forecast</h1>
      <p>${fmt.n(s.n_sites)} monitoring points that were dry on ${fmt.date(s.issue_date)}, each scored for new surface water by ${fmt.date(s.target_date)} (± 2 days). Priorities follow the documented rules; probabilities are calibrated.</p></div>
    <div class="actions"><button class="btn" id="fcCsv">${icon('download')}CSV</button><button class="btn" id="fcGeo">${icon('layers')}GeoJSON</button></div>
  </div>
  ${card('Forecast record', `<div class="meta-grid">
    <div><span>Issued</span>${m.issue_date} · ${h}-day</div>
    <div><span>Valid window</span>${m.valid_window[0]} – ${m.valid_window[1]}</div>
    <div><span>Status</span><b style="color:${m.status === 'stale' ? 'var(--crit)' : 'var(--good)'}">${m.status === 'stale' ? `Stale (${m.age_days} days)` : 'Current'}</b></div>
    <div><span>Target</span>New surface water (radar)</div>
    <div><span>Units</span>${esc(m.units)}</div>
    <div><span>Spatial resolution</span>50 m monitoring circles</div>
    <div><span>Inputs</span>Radar ${m.inputs.radar_last_pass} · weather ${m.inputs.weather_last_day}</div>
    <div><span>Model</span><span class="mono" style="display:inline;text-transform:none;letter-spacing:0;color:var(--ink)">${m.model.fingerprint}</span> · research stage</div>
  </div>`, { sub: 'Forecast, not observation: radar outcomes appear only after the target date' })}
  <div class="grid g-main-l" style="margin-top:14px">
    <div class="grid" style="align-content:start">
      ${card('Verification priority', `
        <div class="stack" style="height:12px">${CAT.map((k) => `<span title="${CAT_LABEL[k]}: ${s.categories[k] || 0}" style="flex:${s.categories[k] || 0};background:var(--cat-${k === 'moderate' ? 'insufficient' : k})"></span>`).join('')}</div>
        <div style="margin-top:12px">${CAT.map((k) => `<label class="check" style="justify-content:space-between;padding:3px 0"><span><input type="checkbox" data-cat="${k}" checked> ${catTag(k)}</span><span class="num">${k === 'moderate' && !s.categories.moderate ? 'not configured' : s.categories[k] || 0}</span></label>`).join('')}</div>
        <p style="font-size:11.5px;color:var(--ink-3);margin:8px 0 0">Rules: High = 90% set {formation}; Insufficient = set {formation, no formation}; Lower = set {no formation}. Moderate needs a programme-agreed threshold.</p>`)}
      ${card('Find and overlay', `
        <div class="field"><label for="fcQ">Monitoring point</label><input id="fcQ" placeholder="e.g. G0221" autocomplete="off"></div>
        <label class="check" style="margin-top:10px"><input type="checkbox" id="fcOut"> Overlay radar outcome at t+${h}</label>
        ${s.outcome ? `<p style="font-size:12px;color:var(--ink-3);margin:8px 0 0">The later radar pass found ${s.outcome.observed} formations; ${s.outcome.hits} had been called High priority.</p>` : '<p style="font-size:12px;color:var(--ink-3);margin:8px 0 0">Outcome not yet observable.</p>'}
        <button class="btn sm" id="fcReset" style="margin-top:10px">${icon('layers')}Reset view</button>`)}
    </div>
    ${card('', '<div class="map-host"><div class="map tall" id="fcMap" role="region" aria-label="Map of monitoring points"></div></div>', { flush: true })}
  </div>
  <div style="margin-top:14px" id="fcTable"></div>`;

  map = makeMap('fcMap', { zoom: 13 });
  legendControl(map, riskLegend());
  const b = st.boot;
  L.marker([b.station.lat, b.station.lon], { icon: L.divIcon({ className: '', html: '<div class="station-pin"></div>', iconSize: [16, 16] }), title: 'Weather station' }).bindTooltip('Gikomero automatic weather station').addTo(map);
  const bounds = fc.rows.map((r) => [r.latitude, r.longitude]);
  map.fitBounds(bounds, { padding: [20, 20] });
  footprintLayer(map, fc.rows);

  const rows = () => fc.rows.filter((r) => f.cat.has(r.category) && (!f.q || r.site_id.toLowerCase().includes(f.q)));
  const draw = () => {
    const R = rows();
    layer?.remove(); outcomeLayer?.remove();
    layer = L.layerGroup([...R].sort((a, z) => (a.category === 'high') - (z.category === 'high') || a.probability - z.probability).map((r) => siteMarker(r, { onClick: (x) => window.openSite(x.site_id) }))).addTo(map);
    if (f.outcome) {
      outcomeLayer = L.layerGroup(R.filter((r) => r.y === 1).map((r) => L.circleMarker([r.latitude, r.longitude], { radius: 11, color: css('--brand'), weight: 2, fill: false }).bindTooltip(`${r.site_id}: water formed (radar)`))).addTo(map);
    }
    const t = table([
      { h: '#', r: 1, f: (r) => r.priority_rank },
      { h: 'Point', f: (r) => `<span class="mono">${r.site_id}</span>` },
      { h: 'Centre (lat, lon)', f: (r) => `<span class="mono">${fmt.ll(r.latitude, r.longitude)}</span>` },
      { h: 'Priority', f: (r) => catTag(r.category) },
      { h: 'Probability', r: 1, f: (r) => `<div class="bar-cell" style="justify-content:flex-end"><span class="num">${r.probability.toFixed(3)}</span><span class="b" style="width:${Math.max(2, r.probability * 70)}px;background:var(--cat-${r.category === 'moderate' ? 'insufficient' : r.category})"></span></div>` },
      { h: 'Uncertainty', f: (r) => r.uncertainty_band },
      { h: 'Wet passes', r: 1, f: (r) => fmt.n(r.WATER_PERSISTENCE_PASSES) },
      { h: `Radar t+${h}`, f: (r) => (r.y == null ? '<span style="color:var(--ink-3)">pending</span>' : r.y ? '<span class="status st-crit">Formed</span>' : '<span style="color:var(--ink-3)">Dry</span>') },
    ], R, { onRow: (r) => { map.setView([r.latitude, r.longitude], 16); window.openSite(r.site_id); }, max: 460 });
    document.getElementById('fcTable').innerHTML = card(`Monitoring points · ${R.length}`, t.html, { flush: true, sub: 'Table alternative to the map · ranked by larviciding priority score' });
    t.bind();
  };
  draw();

  el.querySelectorAll('[data-cat]').forEach((c) => c.addEventListener('change', () => { c.checked ? f.cat.add(c.dataset.cat) : f.cat.delete(c.dataset.cat); draw(); }));
  document.getElementById('fcQ').addEventListener('input', (e) => { f.q = e.target.value.trim().toLowerCase(); draw(); });
  document.getElementById('fcOut').addEventListener('change', (e) => { f.outcome = e.target.checked; draw(); });
  document.getElementById('fcReset').addEventListener('click', () => map.fitBounds(bounds, { padding: [20, 20] }));
  const stamp = `issue ${s.issue_date}, ${h}-day, model ${m.model.fingerprint}, status ${m.status}, research stage`;
  document.getElementById('fcCsv').addEventListener('click', () => {
    const cols = ['site_id', 'latitude', 'longitude', 'category', 'probability', 'conformal_decision', 'uncertainty_band', 'priority_score', 'priority_rank'];
    download(`rinda_forecast_h${h}_${s.issue_date}.csv`, [`# RINDA forecast: ${stamp}. Target: new surface water at 50 m monitoring circles. Not larvae.`, cols.join(','), ...rows().map((r) => cols.map((c) => r[c]).join(','))].join('\n'), 'text/csv');
  });
  document.getElementById('fcGeo').addEventListener('click', () => {
    const gj = { type: 'FeatureCollection', metadata: { generator: 'RINDA', stamp, target: m.target, resolution: m.spatial_resolution },
      features: rows().map((r) => ({ type: 'Feature', geometry: { type: 'Point', coordinates: [r.longitude, r.latitude] }, properties: { site_id: r.site_id, radius_m: 50, probability: r.probability, category: r.category, issue_date: s.issue_date, valid_to: m.valid_window[1] } })) };
    download(`rinda_forecast_h${h}_${s.issue_date}.geojson`, JSON.stringify(gj), 'application/geo+json');
  });
}
