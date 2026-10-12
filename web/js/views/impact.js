import { api, card, kpi, fmt, icon, chart, css, series, table, statusTag } from '../ui.js';

const INPUTS = [
  ['period_weeks', 'Evaluation period', 'weeks'], ['ground_teams', 'Ground teams', 'teams'], ['ground_team_size', 'People per team', 'people'],
  ['ground_daily_wage', 'Daily wage', 'RWF'], ['ground_ha_per_team_day', 'Area searched per team-day', 'ha'], ['ground_detection_rate', 'Habitats found on foot', 'share'],
  ['ground_bti_kg_ha_of_water', 'Bti rate on water', 'kg/ha'], ['bti_price_per_kg', 'Bti price', 'RWF/kg'], ['drone_cost_per_flight_hour', 'Drone operating cost', 'RWF/h'],
  ['pilot_crew_cost_per_day', 'Drone crew cost', 'RWF/day'], ['flight_hours_per_day', 'Flight hours per crew-day', 'h'], ['drones_available', 'Drones available', 'units'],
  ['two_pass_processing_days', 'Two-pass image processing delay', 'days'], ['processing_cost_per_day', 'Processing cost', 'RWF/day'], ['forecast_service_cost_per_week', 'Forecast service', 'RWF/week'],
  ['verification_detection_rate', 'Habitats confirmed onboard', 'share'], ['water_fraction_of_area', 'Water share of wet-prone zone', 'share'], ['habitat_lifetime_days', 'Window before adults emerge', 'days'],
];
const COMP = [['labour', 'Labour'], ['larvicide', 'Larvicide'], ['aviation', 'Aviation'], ['processing', 'Image processing'], ['forecast', 'Forecast service']];

export async function render(el, st) {
  const inputs = { ...st.boot.econ_defaults };
  el.innerHTML = `
  <div class="page-head">
    <div><h1>Impact &amp; economics</h1><p>Compares manual ground larval source management, two-pass drone mapping-then-spraying, and forecast-guided single-pass treatment over the same wet-prone zone. Workload comes from the real forecast archive; prices and rates are programme inputs you set.</p></div>
  </div>
  <div class="note warn" style="margin-bottom:14px"><b>Planning estimate.</b> Every cost below is computed from the inputs on the right. Replace them with RBC programme figures before quoting any number. Entomological effectiveness is reported only from the matched-plot trial (bottom of page).</div>
  <div class="grid g-4" id="imKpis"></div>
  <div class="grid g-main" style="margin-top:14px">
    <div class="grid" style="align-content:start">
      ${card('Programme cost by strategy', '<div class="chart lg" id="imCost"></div>', { sub: 'Total over the evaluation period' })}
      <div class="grid g-2">
        ${card('Response time', '<div class="chart sm" id="imResp"></div>', { sub: 'Days to work through one week of targets' })}
        ${card('Larvicide used', '<div class="chart sm" id="imBti"></div>', { sub: 'Bti product, kg' })}
      </div>
      ${card('Weekly workload from the forecast archive', '<div id="imWork"></div>')}
    </div>
    ${card('Programme inputs', `<div class="form cols-2">${INPUTS.map(([k, l, u]) => `<div class="field"><label for="e_${k}">${l}<span class="u">${u}</span></label><input id="e_${k}" type="number" step="any" value="${inputs[k]}"></div>`).join('')}</div>`, { sub: 'all editable' })}
  </div>
  <div class="grid g-2" style="margin-top:14px">
    ${card('National scale-up planner', `<div class="form cols-2">
        <div class="field"><label for="suS">Sectors under forecast-guided LSM<span class="u">of 416</span></label><input id="suS" type="number" value="30"></div>
        <div class="field"><label for="suW">Operating weeks per year<span class="u">weeks</span></label><input id="suW" type="number" value="36"></div>
      </div><div id="suOut" style="margin-top:12px"></div>`, { sub: 'Scales the Gikomero per-sector workload' })}
    ${card('Entomological and epidemiological outcomes', table([
      { h: 'Outcome', k: 0, f: (r) => r[0] }, { h: 'Design', f: (r) => r[1] }, { h: 'Source', f: (r) => r[2] }, { h: 'Status', f: () => statusTag('awaiting_field') },
    ], [
      ['Anopheles larval density', 'Matched plots, dipping before/after', 'RBC entomology'], ['Pupal productivity', 'Emergence traps on treated habitats', 'RBC entomology'],
      ['Adult Anopheles density', 'CDC light traps, HLC subset', 'RBC entomology'], ['Malaria incidence', 'HMIS, cluster comparison', 'MAPLE / RBC'],
      ['Bti deposition on target', 'Water-sensitive papers, bioassay pans', 'Semi-field trial (E4)'],
    ]).html, { flush: true, sub: 'Reported only from field data (Objective 3)' })}
  </div>`;

  const run = async () => {
    INPUTS.forEach(([k]) => { inputs[k] = +document.getElementById('e_' + k).value; });
    const r = await api.post('/api/economics', { h: st.h, inputs });
    draw(r);
  };
  el.querySelectorAll('input[id^="e_"], #suS, #suW').forEach((x) => x.addEventListener('change', run));
  await run();
}

function draw(r) {
  const [g, t, f] = r.strategies;
  const w = r.workload;
  document.getElementById('imKpis').innerHTML = [
    kpi('Cost per hectare', fmt.rwf(f.cost_per_ha), `Forecast-guided · manual ${fmt.rwf(g.cost_per_ha)} · two-pass ${fmt.rwf(t.cost_per_ha)}`, { accent: true }),
    kpi('Response time', `${fmt.n(f.response_days, 1)}`, `days vs ${fmt.n(g.response_days, 1)} on foot and ${fmt.n(t.response_days, 1)} two-pass · window ${r.inputs.habitat_lifetime_days} d`, { unit: 'days' }),
    kpi('Larvicide avoided', fmt.pct(1 - f.bti_kg / t.bti_kg), `${fmt.n(f.bti_kg, 1)} kg vs ${fmt.n(t.bti_kg, 1)} kg blanket over mapped water`),
    kpi('Wet-prone zone', fmt.n(r.area_ha, 0), `${fmt.n(r.water_ha, 1)} ha estimated open water`, { unit: 'ha' }),
  ].join('');
  const labels = r.strategies.map((s) => s.label);
  chart('imCost', {
    grid: { left: 210, right: 30, top: 30, bottom: 26 }, legend: { data: COMP.map((c) => c[1]) }, tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, valueFormatter: (v) => fmt.rwf(v) },
    xAxis: { type: 'value', axisLabel: { formatter: (v) => fmt.rwf(v).replace('RWF ', '') } }, yAxis: { type: 'category', data: labels },
    series: COMP.map(([k, l], i) => ({ name: l, type: 'bar', stack: 'c', barWidth: 20, data: r.strategies.map((s) => s[k]), itemStyle: { color: series(i + 1), borderColor: css('--s1'), borderWidth: 1 } })),
  });
  const bar = (id, vals, fmtv, mark) => chart(id, {
    grid: { left: 110, right: 50, top: 6, bottom: 22 }, tooltip: { trigger: 'item', valueFormatter: fmtv },
    xAxis: { type: 'value' }, yAxis: { type: 'category', data: ['Manual', 'Two-pass', 'Guided'] },
    series: [{ type: 'bar', barWidth: 12, label: { show: true, position: 'right', color: css('--ink-2'), fontSize: 11, formatter: (p) => fmtv(p.value) },
      data: vals.map((v, i) => ({ value: v, itemStyle: { color: i === 2 ? css('--brand') : css('--ink-3'), borderRadius: [0, 4, 4, 0] } })),
      markLine: mark ? { symbol: 'none', label: { formatter: 'window', color: css('--ink-2') }, lineStyle: { color: css('--crit'), type: 'dashed' }, data: [{ xAxis: mark }] } : undefined }],
  });
  bar('imResp', r.strategies.map((s) => s.response_days), (v) => `${fmt.n(v, 1)} d`, r.inputs.habitat_lifetime_days);
  bar('imBti', r.strategies.map((s) => s.bti_kg), (v) => `${fmt.n(v, 1)} kg`);
  document.getElementById('imWork').innerHTML = `<div class="grid g-4" style="gap:10px">
    <div><div style="font-size:11.5px;color:var(--ink-3)">Issues per week</div><div class="num" style="font-size:20px;font-weight:600">${fmt.n(w.issues_per_week, 1)}</div></div>
    <div><div style="font-size:11.5px;color:var(--ink-3)">Alerts per issue</div><div class="num" style="font-size:20px;font-weight:600">${fmt.n(w.alerts_per_issue, 1)}</div></div>
    <div><div style="font-size:11.5px;color:var(--ink-3)">Targets per week</div><div class="num" style="font-size:20px;font-weight:600">${fmt.n(w.targets_per_week, 0)}</div></div>
    <div><div style="font-size:11.5px;color:var(--ink-3)">Field hours per week</div><div class="num" style="font-size:20px;font-weight:600">${fmt.n(w.guided_flight_h_per_week, 1)} <small style="font-size:12px;color:var(--ink-3)">vs ${fmt.n(w.blanket_flight_h_per_week, 0)} blanket</small></div></div></div>`;

  const S = +document.getElementById('suS').value, W = +document.getElementById('suW').value;
  const perWeekH = w.guided_flight_h_per_week, crewH = r.inputs.flight_hours_per_day * 5;
  const drones = Math.ceil(S * perWeekH / crewH);
  const weekly = f.total / r.inputs.period_weeks;
  document.getElementById('suOut').innerHTML = `<dl class="dl">
    <dt>Drone crews needed (5-day weeks)</dt><dd>${fmt.n(drones)}</dd>
    <dt>Field hours per year</dt><dd>${fmt.n(S * perWeekH * W)}</dd>
    <dt>Bti per year</dt><dd>${fmt.n(S * f.bti_kg / r.inputs.period_weeks * W, 0)} kg</dd>
    <dt>Operating cost per year</dt><dd>${fmt.rwf(S * weekly * W)}</dd>
    <dt>Manual LSM, same sectors</dt><dd>${fmt.rwf(S * g.total / r.inputs.period_weeks * W)}</dd></dl>
    <p style="font-size:11.5px;color:var(--ink-3);margin:10px 0 0">Assumes other sectors resemble Gikomero in wet-prone area and formation rate. Onboarding a sector requires its own radar site grid and at least one weather station.</p>`;
}
