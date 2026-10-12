import { api, card, fmt, icon, table, makeMap, legendControl, riskLegend, siteMarker, riskTag, decisionTag, css, RISK, RISK_LABEL, download } from '../ui.js';

let map, layer, outcomeLayer;
export function destroy() { map?.remove(); map = null; }

export async function render(el, st) {
  const h = st.h;
  const fc = await api.get(`/api/forecast?h=${h}&date=${st.date}`);
  const s = fc.summary;
  const f = { risk: new Set(RISK), decision: 'all', q: '', outcome: false };

  el.innerHTML = `
  <div class="page-head">
    <div><h1>Habitat formation forecast</h1>
      <p>${fmt.n(s.n_sites)} dry sites assessed on ${fmt.date(s.issue_date)}. Each is scored for new surface water by ${fmt.date(s.target_date)} and given a 90%-confidence call: formation, no formation, or verify first.</p></div>
    <div class="actions"><button class="btn" id="fcCsv">${icon('download')}Export CSV</button><button class="btn" id="fcGeo">${icon('layers')}Export GeoJSON</button></div>
  </div>
  <div class="grid g-main-l">
    <div class="grid" style="align-content:start">
      ${card('Composition', `
        <div class="stack" style="height:12px">${RISK.map((k) => `<span title="${RISK_LABEL[k]}: ${s.risk[k] || 0}" style="flex:${s.risk[k] || 0};background:var(--risk-${k})"></span>`).join('')}</div>
        <div style="margin-top:12px">${RISK.map((k) => `<label class="check" style="justify-content:space-between;padding:3px 0"><span><input type="checkbox" data-risk="${k}" checked> ${riskTag(k)}</span><span class="num">${s.risk[k] || 0}</span></label>`).join('')}</div>`)}
      ${card('Confidence call', `
        <div class="seg" id="decSeg" style="width:100%;display:flex">
          ${[['all', 'All'], ['formation', 'Formation'], ['uncertain', 'Verify'], ['no_formation', 'None']].map(([k, l]) => `<button data-d="${k}" aria-pressed="${k === 'all'}" style="flex:1">${l}</button>`).join('')}
        </div>
        <dl class="dl" style="margin-top:12px">
          <dt>Formation</dt><dd>${s.decisions.formation || 0}</dd><dt>Verify first</dt><dd>${s.decisions.uncertain || 0}</dd>
          <dt>No formation</dt><dd>${s.decisions.no_formation || 0}</dd><dt>Operating threshold</dt><dd class="mono">${s.threshold.toFixed(3)}</dd>
        </dl>`)}
      ${card('Find and overlay', `
        <div class="field"><label for="fcQ">Site identifier</label><input id="fcQ" placeholder="e.g. G0221" autocomplete="off"></div>
        <label class="check" style="margin-top:10px"><input type="checkbox" id="fcOut"> Overlay radar outcome at t+${h}</label>
        ${s.outcome ? `<p style="font-size:12px;color:var(--ink-3);margin:8px 0 0">Later radar pass found ${s.outcome.observed} formations; ${s.outcome.hits} were above threshold.</p>` : ''}`)}
    </div>
    ${card('', '<div class="map-host"><div class="map tall" id="fcMap"></div></div>', { flush: true })}
  </div>
  <div style="margin-top:14px" id="fcTable"></div>`;

  map = makeMap('fcMap', { zoom: 13 });
  legendControl(map, riskLegend());
  const b = st.boot;
  L.marker([b.station.lat, b.station.lon], { icon: L.divIcon({ className: '', html: '<div class="station-pin"></div>', iconSize: [16, 16] }) }).bindTooltip('Gikomero automatic weather station').addTo(map);
  map.fitBounds(fc.rows.map((r) => [r.latitude, r.longitude]), { padding: [20, 20] });

  const rows = () => fc.rows.filter((r) => f.risk.has(r.risk_class) && (f.decision === 'all' || r.conformal_decision === f.decision) && (!f.q || r.site_id.toLowerCase().includes(f.q)));
  const draw = () => {
    const R = rows();
    layer?.remove(); outcomeLayer?.remove();
    layer = L.layerGroup([...R].sort((a, z) => a.probability - z.probability).map((r) => siteMarker(r, { onClick: (x) => window.openSite(x.site_id) }))).addTo(map);
    if (f.outcome) {
      outcomeLayer = L.layerGroup(R.filter((r) => r.y === 1).map((r) => L.circleMarker([r.latitude, r.longitude], { radius: 11, color: css('--brand'), weight: 2, fill: false, dashArray: '3 2' }).bindTooltip(`${r.site_id}: water formed`))).addTo(map);
    }
    const t = table([
      { h: '#', r: 1, f: (r) => r.priority_rank ?? '' },
      { h: 'Site', f: (r) => `<span class="mono">${r.site_id}</span>` },
      { h: 'Position', f: (r) => `<span class="mono">${fmt.ll(r.latitude, r.longitude)}</span>` },
      { h: 'Probability', r: 1, f: (r) => `<div class="bar-cell" style="justify-content:flex-end"><span class="num">${r.probability.toFixed(3)}</span><span class="b" style="width:${Math.max(2, r.probability * 70)}px;background:var(--risk-${r.risk_class})"></span></div>` },
      { h: 'Class', f: (r) => riskTag(r.risk_class) },
      { h: 'Call', f: (r) => decisionTag(r.conformal_decision) },
      { h: 'Uncertainty', f: (r) => r.uncertainty_band },
      { h: 'Wet passes', r: 1, f: (r) => fmt.n(r.WATER_PERSISTENCE_PASSES) },
      { h: 'Priority', r: 1, f: (r) => (r.priority_score ?? 0).toFixed(3) },
      { h: `Radar t+${h}`, f: (r) => (r.y == null ? '<span style="color:var(--ink-3)">pending</span>' : r.y ? '<span class="status st-crit">Formed</span>' : '<span style="color:var(--ink-3)">Dry</span>') },
    ], R, { onRow: (r) => { map.setView([r.latitude, r.longitude], 16); window.openSite(r.site_id); }, max: 460 });
    document.getElementById('fcTable').innerHTML = card(`Sites · ${R.length}`, t.html, { flush: true, sub: 'Sorted by larviciding priority' });
    t.bind();
  };
  draw();

  el.querySelectorAll('[data-risk]').forEach((c) => c.addEventListener('change', () => { c.checked ? f.risk.add(c.dataset.risk) : f.risk.delete(c.dataset.risk); draw(); }));
  el.querySelectorAll('#decSeg button').forEach((x) => x.addEventListener('click', () => {
    f.decision = x.dataset.d; el.querySelectorAll('#decSeg button').forEach((y) => y.setAttribute('aria-pressed', String(y === x))); draw();
  }));
  document.getElementById('fcQ').addEventListener('input', (e) => { f.q = e.target.value.trim().toLowerCase(); draw(); });
  document.getElementById('fcOut').addEventListener('change', (e) => { f.outcome = e.target.checked; draw(); });
  document.getElementById('fcCsv').addEventListener('click', () => {
    const cols = ['site_id', 'latitude', 'longitude', 'probability', 'risk_class', 'conformal_decision', 'uncertainty_band', 'priority_score', 'priority_rank'];
    download(`rinda_forecast_h${h}_${s.issue_date}.csv`, [cols.join(','), ...rows().map((r) => cols.map((c) => r[c]).join(','))].join('\n'), 'text/csv');
  });
  document.getElementById('fcGeo').addEventListener('click', () => {
    const gj = { type: 'FeatureCollection', features: rows().map((r) => ({ type: 'Feature', geometry: { type: 'Point', coordinates: [r.longitude, r.latitude] }, properties: { site_id: r.site_id, probability: r.probability, risk_class: r.risk_class, call: r.conformal_decision, priority: r.priority_score, issue_date: s.issue_date, valid_to: s.target_date, claim: fc.claim } })) };
    download(`rinda_forecast_h${h}_${s.issue_date}.geojson`, JSON.stringify(gj), 'application/geo+json');
  });
}
