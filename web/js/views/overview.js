import { api, card, kpi, chart, fmt, icon, table, makeMap, legendControl, riskLegend, siteMarker, riskTag, decisionTag, css, series, statusTag, RISK, RISK_LABEL } from '../ui.js';

let map;
export function destroy() { map?.remove(); map = null; }

export async function render(el, st) {
  const h = st.h, date = st.date, b = st.boot;
  const start = new Date(new Date(date) - 45 * 864e5).toISOString().slice(0, 10);
  const [fc, ver, met, ev] = await Promise.all([
    api.get(`/api/forecast?h=${h}&date=${date}`), api.get(`/api/verification?h=${h}`),
    api.get(`/api/meteo?start=${start}&end=${date}`), api.get('/api/evidence'),
  ]);
  const s = fc.summary, m = b.model.horizons[h], sc = m.test_scores;
  const prevIdx = ver.findIndex((v) => v.date === s.issue_date) - 1;
  const prev = prevIdx >= 0 ? ver[prevIdx] : null;
  const rain14 = met.slice(-14).reduce((a, r) => a + (r.rainfall_mm || 0), 0);
  const queue = fc.rows.filter((r) => r.conformal_decision !== 'no_formation').slice(0, 12);

  el.innerHTML = `
  <div class="page-head">
    <div><h1>Operating picture</h1>
      <p>Forecast issued ${fmt.date(s.issue_date)} for new surface water by ${fmt.date(s.target_date)} across ${b.tenant.name}, ${b.tenant.district} District. ${b.claim}</p></div>
    <div class="actions">
      <a class="btn" href="/api/bulletin?h=${h}&date=${s.issue_date}" target="_blank" rel="noopener">${icon('print')}Issue bulletin</a>
      <a class="btn primary" href="#/missions">${icon('mission')}Plan sorties</a>
    </div>
  </div>
  <div class="grid g-4">
    ${kpi('Formation calls', fmt.n(s.decisions.formation || 0), `${s.alerts} above operating threshold · ${fmt.n(s.n_sites)} sites assessed`, { accent: true, id: 'spkCalls' })}
    ${kpi('Verify before action', fmt.n(s.decisions.uncertain || 0), 'Model cannot decide at 90% confidence', { id: 'spkUnc' })}
    ${kpi('Forecast skill', fmt.n(sc.pr_auc / sc.base_rate, 1), `PR-AUC ${sc.pr_auc.toFixed(3)} vs base rate ${sc.base_rate.toFixed(3)} on untouched test window`, { unit: '× chance' })}
    ${kpi('Rain, last 14 days', fmt.n(rain14, 1), `Gikomero AWS · ${met.length ? fmt.dshort(met.at(-1).date) : '–'} latest reading`, { unit: 'mm', id: 'spkRain' })}
  </div>
  <div class="grid g-main" style="margin-top:14px">
    ${card('Formation outlook', '<div class="map-host"><div class="map mid" id="ovMap"></div></div>', { flush: true, sub: `${h}-day horizon · click a site for its dossier` })}
    <div class="grid" style="align-content:start">
      ${card('Priority queue', '<div id="ovQueue"></div>', { flush: true, sub: 'Ranked by larviciding priority', foot: `<span>Priority = probability, persistence, certainty</span><a href="#/forecast">Full list ${icon('arrow')}</a>` })}
      ${prev ? card('Last issue, checked against radar', `
        <div class="grid g-3" style="gap:8px;text-align:center">
          <div><div class="kpi" style="padding:0"><div class="val" style="font-size:22px">${prev.hits}</div><div class="meta">alerts confirmed</div></div></div>
          <div><div class="kpi" style="padding:0"><div class="val" style="font-size:22px">${prev.false_alarms}</div><div class="meta">false alarms</div></div></div>
          <div><div class="kpi" style="padding:0"><div class="val" style="font-size:22px">${prev.misses}</div><div class="meta">missed formations</div></div></div>
        </div>`, { sub: fmt.date(prev.date) }) : ''}
    </div>
  </div>
  <div class="grid g-3" style="margin-top:14px">
    ${card('Formation calls by issue', '<div class="chart" id="ovTimeline"></div>', { cls: 'span-2', sub: 'Click a bar to open that issue', foot: '<span>A call means the 90% conformal set contains only “formation”. Observed counts come from the next radar pass.</span>' })}
    ${card('Pipeline health', pipeline(b, m))}
  </div>
  <div class="grid g-3" style="margin-top:14px">
    ${ev.objectives.map((o) => card(`${o.id} · ${o.paper}`, `<p style="margin:0 0 10px;font-weight:500">${o.title}</p>${statusTag(o.status)}<p style="color:var(--ink-2);font-size:12.5px;margin:10px 0 0">${o.evidence}</p>`, { foot: `<span>Open item: ${o.open}</span>` })).join('')}
  </div>`;

  // map
  map = makeMap('ovMap', { zoom: 12.5 });
  const pts = [];
  [...fc.rows].sort((a, z) => a.probability - z.probability).forEach((r) => { siteMarker(r, { onClick: (x) => window.openSite(x.site_id) }).addTo(map); pts.push([r.latitude, r.longitude]); });
  L.marker([b.station.lat, b.station.lon], { icon: L.divIcon({ className: '', html: '<div class="station-pin"></div>', iconSize: [16, 16] }) }).bindTooltip('Gikomero AWS').addTo(map);
  if (pts.length) map.fitBounds(pts, { padding: [20, 20] });
  legendControl(map, riskLegend());

  const t = table([
    { h: 'Site', f: (r) => `<span class="mono">${r.site_id}</span>` },
    { h: 'p', r: 1, f: (r) => r.probability.toFixed(3) },
    { h: 'Class', f: (r) => riskTag(r.risk_class) },
    { h: 'Call', f: (r) => decisionTag(r.conformal_decision) },
  ], queue, { onRow: (r) => window.openSite(r.site_id), max: 330, empty: 'No actionable sites on this issue' });
  document.getElementById('ovQueue').innerHTML = t.html; t.bind();

  const calls = ver.map((v) => v.date);
  const c = chart('ovTimeline', {
    grid: { left: 40, right: 12, top: 40, bottom: 28 },
    legend: { data: ['Formation calls', 'Verify first', 'Observed formations'] },
    xAxis: { type: 'category', data: calls.map(fmt.dshort) },
    yAxis: { type: 'value', name: 'sites' },
    series: [
      { name: 'Formation calls', type: 'bar', stack: 'c', barMaxWidth: 14, data: ver.map((v) => v.formation_calls), itemStyle: { color: css('--signal') } },
      { name: 'Verify first', type: 'bar', stack: 'c', barMaxWidth: 14, data: ver.map((v) => v.uncertain), itemStyle: { color: css('--ink-3'), borderRadius: [3, 3, 0, 0] } },
      { name: 'Observed formations', type: 'line', symbol: 'circle', symbolSize: 5, data: ver.map((v) => v.observed_formation), lineStyle: { width: 2, color: series(1) }, itemStyle: { color: series(1) } },
    ],
  });
  c.on('click', (p) => { st.date = ver[p.dataIndex].date; document.getElementById('dateSel').value = st.date; location.hash = '#/overview'; dispatchEvent(new HashChangeEvent('hashchange')); });

  const spark = (id, data, color, type = 'line') => chart(id, {
    grid: { left: 0, right: 0, top: 2, bottom: 0 }, tooltip: { show: false },
    xAxis: { type: 'category', show: false, data: data.map((_, i) => i) }, yAxis: { type: 'value', show: false },
    series: [{ type, data, showSymbol: false, barWidth: 3, lineStyle: { width: 1.5, color }, itemStyle: { color }, areaStyle: type === 'line' ? { color, opacity: .1 } : undefined }],
  });
  spark('spkCalls', ver.map((v) => v.formation_calls), css('--signal'));
  spark('spkUnc', ver.map((v) => v.uncertain), css('--warn'));
  spark('spkRain', met.slice(-30).map((r) => r.rainfall_mm || 0), series(1), 'bar');
}

function pipeline(b, m) {
  const src = b.sources;
  const row = (name, ok, meta) => `<div class="kv-row"><span>${name}</span><span>${ok ? `<span class="status st-good">${icon('check')}${meta}</span>` : `<span class="status st-warn">${icon('lock')}${meta}</span>`}</span></div>`;
  return [
    row('Sentinel-1 radar', true, `to ${fmt.dshort(b.s1_last)}`),
    row('Weather station', true, `to ${fmt.dshort(b.meteo_last)}`),
    ...src.filter((s) => !s.present).map((s) => row(s.name, false, 'not connected')),
    `<div class="kv-row"><span>Model</span><span class="mono">${m.fingerprint}</span></div>`,
    `<div class="kv-row"><span>Engine reproduction</span><span>ΔPR-AUC ${m.reproduction.abs_diff_pr_auc.toFixed(4)}</span></div>`,
  ].join('');
}
