import { api, card, kpi, fmt, icon, table, chart, css, series, esc, makeMap, legendControl, siteMarker } from '../ui.js';

let map;
export function destroy() { map?.remove(); map = null; }

const pct = (v) => (v == null ? '–' : fmt.pct(v, 1));

export async function render(el, st) {
  const h = st.h;
  const [r, fc] = await Promise.all([api.get(`/api/replay?h=${h}&date=${st.date}`), api.get(`/api/forecast?h=${h}&date=${st.date}`)]);
  const m = r.meta, i = r.issue, o7 = r.overall[7] || r.overall['7'], o14 = r.overall[14] || r.overall['14'];
  const D = r.definitions;
  el.innerHTML = `
  <div class="page-head">
    <div><h1>Replay & validation</h1><p>What the ${h}-day forecast said on ${fmt.date(m.issue_date)}, which inputs it had, and what the next radar pass actually found. Outcomes are radar-detected surface water, not larvae.</p></div>
    <div class="actions">${st.can('export:validation') ? `<a class="btn" href="/api/v1/replay/export.csv?h=${h}">${icon('download')}Export validation summary</a>` : ''}</div>
  </div>
  <div class="note" style="margin-bottom:14px"><b>No future information.</b> ${esc(r.leakage_note)}</div>

  ${card('As issued', `<div class="meta-grid">
    <div><span>Issue date</span>${m.issue_date}</div>
    <div><span>Valid window</span>${m.valid_window[0]} to ${m.valid_window[1]}</div>
    <div><span>Last radar pass used</span>${m.inputs.radar_last_pass} (${m.inputs.radar_lag_days} d before issue)</div>
    <div><span>Last weather day used</span>${m.inputs.weather_last_day} (${m.inputs.weather_lag_days} d before issue)</div>
    <div><span>Model</span><span class="mono" style="display:inline;text-transform:none;letter-spacing:0;color:var(--ink)">${m.model.fingerprint}</span> · engine ${m.model.engine}</div>
    <div><span>Trained / calibrated on</span>${m.model.train_window[0]} – ${m.model.train_window[1]} / to ${m.model.calibration_window[1]}</div>
    <div><span>Generated</span>${m.generated_utc || '–'} (hindcast)</div>
    <div><span>Leakage guard</span>${m.leakage_guard}</div></div>`)}

  <div class="grid g-4" style="margin-top:14px">
    ${i ? kpi('High-priority calls', fmt.n(i.high_calls), `${i.hits} confirmed by radar · ${i.false_alarms} false alarms`, { accent: true }) : kpi('High-priority calls', '–', 'Outcome not yet observed')}
    ${i ? kpi('Formations observed', fmt.n(i.observed_formation), `${i.hits} called · ${i.missed_while_insufficient} flagged as insufficient · ${i.missed_while_lower} missed`) : ''}
    ${i ? kpi('Precision of calls', pct(i.precision_of_calls), 'Share of High calls where water formed') : ''}
    ${i ? kpi('Detection rate', pct(i.detection_rate), `Called or flagged: ${pct(i.detected_or_flagged_for_verification)}`) : ''}
  </div>

  <div class="grid g-main" style="margin-top:14px">
    ${card('Calls against outcome on this issue', '<div class="map mid" id="rpMap" role="region" aria-label="Map comparing forecast calls with observed formation"></div>', { flush: true, foot: '<span>Ring = water formed per radar. Triangle inside ring = hit; triangle alone = false alarm; ring around a dot = missed.</span>' })}
    ${card('Both horizons, whole test window', table([
      { h: 'Measure', f: (x) => x[0] }, { h: '7-day', r: 1, f: (x) => x[1] }, { h: '14-day', r: 1, f: (x) => x[2] },
    ], [
      ['Site-issues evaluated', fmt.n(o7.sites), fmt.n(o14.sites)], ['Formations observed', fmt.n(o7.observed_formation), fmt.n(o14.observed_formation)],
      ['High-priority calls', fmt.n(o7.high_calls), fmt.n(o14.high_calls)], ['Precision of calls', pct(o7.precision_of_calls), pct(o14.precision_of_calls)],
      ['Detection rate', pct(o7.detection_rate), pct(o14.detection_rate)], ['Missed-detection rate', pct(o7.missed_detection_rate), pct(o14.missed_detection_rate)],
      ['Called or flagged for verification', pct(o7.detected_or_flagged_for_verification), pct(o14.detected_or_flagged_for_verification)],
      ['Missed while "lower priority"', fmt.n(o7.missed_while_lower), fmt.n(o14.missed_while_lower)], ['Lead time', '7 days', '14 days'],
    ]).html, { flush: true, sub: 'Radar-confirmed outcomes', foot: '<span>Precision is low because new water is rare (base rate about 3%): most calls are false alarms, which is why every call is an inspection, never a treatment.</span>' })}
  </div>

  <div class="grid g-2" style="margin-top:14px">
    ${card('Per issue', '<div class="chart" id="rpChart"></div>', { sub: 'Hits, false alarms and misses for each issue date' })}
    ${card('Definitions', `<dl class="glossary" style="margin:0">${Object.entries(D).map(([k, v]) => `<dt>${k.replaceAll('_', ' ')}</dt><dd>${esc(v)}</dd>`).join('')}</dl>`)}
  </div>
  <div style="margin-top:14px">${card('Validation by issue', table([
    { h: 'Issue', f: (x) => `<a href="#" data-date="${x.date}" class="mono">${x.date}</a>` }, { h: 'Sites', r: 1, k: 'sites' },
    { h: 'Observed', r: 1, k: 'observed_formation' }, { h: 'Calls', r: 1, k: 'high_calls' }, { h: 'Hits', r: 1, k: 'hits' },
    { h: 'False alarms', r: 1, k: 'false_alarms' }, { h: 'Flagged insufficient', r: 1, k: 'missed_while_insufficient' }, { h: 'Missed', r: 1, k: 'missed_while_lower' },
    { h: 'Precision', r: 1, f: (x) => pct(x.precision_of_calls) }, { h: 'Detection', r: 1, f: (x) => pct(x.detection_rate) },
  ], [...r.per_issue].reverse(), { max: 420 }).html, { flush: true })}</div>`;

  chart('rpChart', {
    grid: { left: 40, right: 12, top: 36, bottom: 28 }, legend: { data: ['Hits', 'False alarms', 'Missed'] },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    xAxis: { type: 'category', data: r.per_issue.map((x) => fmt.dshort(x.date)) }, yAxis: { type: 'value', name: 'points' },
    series: [
      { name: 'Hits', type: 'bar', stack: 'a', barMaxWidth: 12, data: r.per_issue.map((x) => x.hits), itemStyle: { color: series(3) } },
      { name: 'False alarms', type: 'bar', stack: 'a', barMaxWidth: 12, data: r.per_issue.map((x) => x.false_alarms), itemStyle: { color: css('--ink-3') } },
      { name: 'Missed', type: 'bar', stack: 'a', barMaxWidth: 12, data: r.per_issue.map((x) => x.missed_while_lower), itemStyle: { color: css('--crit') } },
    ],
  });
  el.querySelectorAll('[data-date]').forEach((a) => a.addEventListener('click', (e) => {
    e.preventDefault(); const sel = document.getElementById('dateSel'); sel.value = a.dataset.date; sel.dispatchEvent(new Event('change'));
  }));

  map = makeMap('rpMap', { zoom: 12.5 });
  const rows = fc.rows;
  rows.filter((x) => x.category !== 'lower' || x.y === 1).forEach((x) => siteMarker(x, { onClick: (z) => window.openSite(z.site_id) }).addTo(map));
  rows.filter((x) => x.y === 1).forEach((x) => L.circleMarker([x.latitude, x.longitude], { radius: 11, color: css('--brand'), weight: 2, fill: false, interactive: false }).addTo(map));
  map.fitBounds(rows.map((x) => [x.latitude, x.longitude]), { padding: [20, 20] });
  legendControl(map, `<b>Calls and outcomes</b><div class="row"><svg width="14" height="14" viewBox="0 0 14 14"><path d="M7 1.5l5.5 10h-11z" fill="var(--cat-high)"/></svg>High-priority call</div><div class="row"><span class="dot" style="background:none;border:2px dashed var(--cat-insufficient)"></span>Insufficient evidence</div><div class="row"><span class="dot" style="background:none;border:2px solid var(--brand);width:14px;height:14px"></span>Water formed (radar)</div>`);
}
