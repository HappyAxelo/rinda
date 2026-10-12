import { api, card, kpi, fmt, icon, table, chart, css, series, toast, statusTag, makeMap, download, esc } from '../ui.js';

let map;
export function destroy() { map?.remove(); map = null; }

const LOOP = [
  ['work_speed_ms', 'Ground speed', 'm/s', 0.5], ['altitude_m', 'Height AGL', 'm', 0.5], ['camera_vfov_deg', 'Camera vertical FOV', '°', 1],
  ['nozzle_offset_m', 'Nozzle aft of camera axis', 'm', 0.1], ['t_cap_ms', 'Capture', 'ms', 1], ['t_pre_ms', 'Pre-process', 'ms', 1],
  ['t_inf_ms', 'Inference', 'ms', 1], ['t_dec_ms', 'Decision', 'ms', 1], ['t_act_ms', 'Valve actuation', 'ms', 1],
  ['p_flight_w', 'Flight power', 'W', 10], ['p_compute_w', 'Compute power', 'W', 1], ['p_spray_w', 'Pump power', 'W', 1], ['swath_m', 'Swath', 'm', 0.1],
];

export async function render(el, st) {
  const a = { ...st.boot.planner_defaults.aircraft };
  el.innerHTML = `
  <div class="page-head">
    <div><h1>Flight operations</h1><p>The onboard perceive–decide–actuate loop. Design the timing budget against <span class="mono">L = t<sub>cap</sub>+t<sub>pre</sub>+t<sub>inf</sub>+t<sub>dec</sub>+t<sub>act</sub> ≤ d/v</span>, then load flight logs to replace every design value with a measurement.</p></div>
    <div class="actions"><a class="btn" href="/api/v1/flightlogs/template">${icon('download')}Log template</a><select id="flProv" class="role" aria-label="Provenance of the uploaded log"><option value="FIELD">Field flight</option><option value="BENCH">Bench / HIL test</option><option value="SYNTHETIC">Synthetic</option></select><label class="btn primary" for="flFile">${icon('upload')}Upload flight log</label><input type="file" id="flFile" accept=".csv" hidden></div>
  </div>
  <div class="tabs" role="tablist"><button role="tab" aria-selected="true" data-tab="design">Loop design</button><button role="tab" aria-selected="false" data-tab="logs">Measured performance</button><button role="tab" aria-selected="false" data-tab="frontier">Model compression frontier</button></div>
  <div id="tab-design">
    <div class="grid g-4" id="flKpis"></div>
    <div class="grid g-main" style="margin-top:14px">
      <div class="grid" style="align-content:start">
        ${card('Where the latency budget goes', '<div class="chart sm" id="flStack"></div>', { sub: 'Design values · ms' })}
        ${card('Deadline vs ground speed', '<div class="chart" id="flCurve"></div>', { sub: 'Faster flight shrinks d/v; the loop latency does not', foot: '<span>Deadline d/v assumes a patch is first seen at the leading edge of the frame and must reach the nozzle line.</span>' })}
        ${card('Energy per treated hectare', '<div id="flEnergy"></div>', { sub: 'E<sub>ha</sub> = (P<sub>flight</sub>+P<sub>compute</sub>+P<sub>spray</sub>) × 10 000 / (v·w)' })}
      </div>
      ${card('Loop parameters', `<div class="assume">${icon('info')}Design values until measured. Stage times typical of a Jetson-class board running a compressed segmentation model.</div>
        <div class="form cols-2" style="margin-top:10px">${LOOP.map(([k, l, u, s]) => `<div class="field"><label for="l_${k}">${l}<span class="u">${u}</span></label><input id="l_${k}" type="number" step="${s}" value="${a[k]}"></div>`).join('')}</div>`)}
    </div>
  </div>
  <div id="tab-logs" hidden></div>
  <div id="tab-frontier" hidden></div>`;

  el.querySelectorAll('[data-tab]').forEach((b) => b.addEventListener('click', () => {
    el.querySelectorAll('[data-tab]').forEach((x) => x.setAttribute('aria-selected', String(x === b)));
    ['design', 'logs', 'frontier'].forEach((t) => { document.getElementById('tab-' + t).hidden = t !== b.dataset.tab; });
    if (b.dataset.tab === 'logs') logs(st);
    if (b.dataset.tab === 'frontier') frontier();
    window.dispatchEvent(new Event('resize'));
  }));

  const recompute = async () => {
    LOOP.forEach(([k]) => { a[k] = +document.getElementById('l_' + k).value; });
    const r = await api.post('/api/timing', a);
    const bgt = r.budget, e = r.energy;
    document.getElementById('flKpis').innerHTML = [
      kpi('Loop latency L', fmt.n(bgt.latency_ms), 'Sum of five stages', { unit: 'ms', accent: true }),
      kpi('Deadline d/v', fmt.n(bgt.deadline_ms), `d = ${fmt.n(bgt.actionable_distance_m, 2)} m at ${a.work_speed_ms} m/s · footprint ${fmt.n(bgt.footprint_m, 2)} m`, { unit: 'ms' }),
      kpi('Slack', `<span style="color:${bgt.meets_deadline ? 'var(--good)' : 'var(--crit)'}">${bgt.slack_ms >= 0 ? '+' : ''}${fmt.n(bgt.slack_ms)}</span>`, bgt.meets_deadline ? 'Deadline met with margin' : 'Deadline missed: slow down or cut inference time', { unit: 'ms' }),
      kpi('Highest safe speed', fmt.n(bgt.max_safe_speed_ms, 1), 'Speed at which L = d/v exactly', { unit: 'm/s' }),
    ].join('');
    chart('flStack', {
      grid: { left: 70, right: 24, top: 30, bottom: 24 }, tooltip: { trigger: 'item', valueFormatter: (v) => `${fmt.n(v, 1)} ms` },
      legend: { data: bgt.stages.map((s) => s.stage) },
      xAxis: { type: 'value', max: Math.max(bgt.deadline_ms, bgt.latency_ms) * 1.05 }, yAxis: { type: 'category', data: ['Loop'] },
      series: [...bgt.stages.map((s, i) => ({ name: s.stage, type: 'bar', stack: 'L', barWidth: 22, data: [s.ms], itemStyle: { color: series(i + 1), borderColor: css('--s1'), borderWidth: 1 } })),
        { name: 'Deadline', type: 'line', data: [], markLine: { symbol: 'none', label: { formatter: `d/v ${fmt.n(bgt.deadline_ms)} ms`, color: css('--ink-2') }, lineStyle: { color: css('--crit'), type: 'dashed', width: 1.5 }, data: [{ xAxis: bgt.deadline_ms }] } }],
    });
    chart('flCurve', {
      grid: { left: 52, right: 16, top: 30, bottom: 36 }, legend: { data: ['Deadline d/v', 'Loop latency L'] },
      xAxis: { type: 'value', name: 'ground speed m/s', nameLocation: 'middle', nameGap: 24, min: 1, max: 10 },
      yAxis: { type: 'log', name: 'ms', min: 50 },
      series: [
        { name: 'Deadline d/v', type: 'line', showSymbol: false, data: r.curve.map((c) => [c.speed, c.deadline_ms]), lineStyle: { width: 2, color: series(1) }, itemStyle: { color: series(1) }, areaStyle: { color: series(1), opacity: .06 } },
        { name: 'Loop latency L', type: 'line', showSymbol: false, data: r.curve.map((c) => [c.speed, c.latency_ms]), lineStyle: { width: 2, color: series(2) }, itemStyle: { color: series(2) },
          markPoint: { symbol: 'circle', symbolSize: 9, itemStyle: { color: css('--crit') }, label: { show: true, position: 'top', color: css('--ink-2'), formatter: `${fmt.n(bgt.max_safe_speed_ms, 1)} m/s` }, data: [{ coord: [Math.min(10, bgt.max_safe_speed_ms), bgt.latency_ms] }] } },
      ],
    });
    document.getElementById('flEnergy').innerHTML = `
      <div style="display:flex;align-items:baseline;gap:10px"><span style="font-size:26px;font-weight:600" class="num">${fmt.n(e.wh_per_ha)}</span><span style="color:var(--ink-3)">Wh per treated hectare · ${fmt.n(e.seconds_per_ha / 60, 1)} min/ha</span></div>
      <div class="stack" style="margin:10px 0 8px;height:12px"><span style="flex:${e.share_flight};background:${series(1)}"></span><span style="flex:${e.share_compute};background:${series(2)}"></span><span style="flex:${e.share_spray};background:${series(3)}"></span></div>
      <div class="legend"><span><i style="background:${series(1)}"></i>Flight ${fmt.pct(e.share_flight)}</span><span><i style="background:${series(2)}"></i>Compute ${fmt.pct(e.share_compute)}</span><span><i style="background:${series(3)}"></i>Spray ${fmt.pct(e.share_spray)}</span></div>
      <p style="font-size:12px;color:var(--ink-3);margin:8px 0 0">Onboard compute is ${fmt.pct(e.share_compute)} of the power budget, so compression pays off mainly through latency (allowing faster flight) rather than through the compute watts themselves.</p>`;
  };
  el.querySelectorAll('input[id^="l_"]').forEach((x) => x.addEventListener('input', recompute));
  await recompute();

  document.getElementById('flFile').addEventListener('change', async (e) => {
    const f = e.target.files[0]; if (!f) return;
    const prov = document.getElementById('flProv').value;
    if (!st.can('upload:flightlog')) { toast('Sign in as an analyst or supervisor to upload flight logs.', true); return; }
    const fd = new FormData(); fd.append('file', f); fd.append('provenance', prov.toUpperCase());
    try { await api.upload('/api/flightlogs', fd); toast(`${f.name} ingested`); el.querySelector('[data-tab="logs"]').click(); }
    catch (er) { toast('Rejected: ' + er.message, true); }
    e.target.value = '';
  });
}

async function logs(st) {
  const box = document.getElementById('tab-logs');
  const [list, ms] = await Promise.all([api.get('/api/flightlogs'), api.get('/api/missions')]);
  const an = await api.get('/api/flightlogs/analysis');
  const synthOpts = ms.map((m) => `<option value="${m.id}">${m.id}</option>`).join('');
  const header = card(`Loaded logs · ${list.length}`, table([
    { h: 'File', f: (l) => `<span class="mono">${esc(l.filename)}</span>` }, { h: 'Mission', f: (l) => `<span class="mono">${l.mission_id}</span>` },
    { h: 'Provenance', f: (l) => statusTag(l.provenance) }, { h: 'Events', r: 1, f: (l) => fmt.n(l.n_rows) },
    { h: 'Uploaded (UTC)', f: (l) => `<span class="mono">${l.uploaded_utc}</span>` }, { h: '', f: (l) => (st.can('delete:flightlog') ? `<button class="btn sm ghost" data-del="${l.id}">${icon('trash')}Remove</button>` : '') },
  ], list, { max: 220, empty: 'No flight logs yet.' }).html, { flush: true, tools: ms.length && st.can('upload:flightlog') ? `<select id="synMid" class="role" style="height:26px">${synthOpts}</select><button class="btn sm" id="synBtn">Generate bench test file</button>` : '' });

  if (!an.available) {
    box.innerHTML = header + `<div class="card" style="margin-top:14px"><div class="empty">${icon('flight')}<b>No measurements yet</b><span>Upload a flight log exported by the companion computer (one row per decision) to measure latency, deadline misses, verification accuracy and spray volume.<br>${ms.length ? 'To exercise the analytics before flights, generate a bench test file from a committed mission. It is stamped synthetic everywhere it appears.' : 'Commit a mission in Mission Planning to enable bench test files.'}</span></div></div>` + schema();
    bindLogs(st); return;
  }
  const synthetic = an.provenance.includes('SYNTHETIC');
  const v = an.verification;
  box.innerHTML = header + `
    ${synthetic ? '<div class="synthetic-banner" style="margin-top:14px">Synthetic bench data in view · these numbers exercise the analytics and are not measurements</div>' : ''}
    <div class="grid g-4" style="margin-top:14px">
      ${kpi('Decisions logged', fmt.n(an.n_events), `${an.missions.length} mission(s) · ${Object.entries(an.decisions).map(([k, n]) => `${n} ${k.toLowerCase().replace('_', ' ')}`).join(' · ')}`, { accent: true })}
      ${kpi('Latency p50 / p99', `${fmt.n(an.latency.p50)} <small>/</small> ${fmt.n(an.latency.p99)}`, `max ${fmt.n(an.latency.max)} ms · mean ${fmt.n(an.latency.mean)} ms`, { unit: 'ms' })}
      ${kpi('Deadline-miss ratio', fmt.pct(an.deadline_miss_ratio, 2), 'Share of decisions with L > d/v')}
      ${v ? kpi('Verification precision / recall', `${fmt.n(v.precision, 2)} <small>/</small> ${fmt.n(v.recall, 2)}`, `false-spray ${fmt.pct(v.false_spray_rate)} · missed habitat ${fmt.pct(v.missed_habitat_rate)}`) : kpi('Verification accuracy', '–', 'Add a ground_truth column from entomology to score it')}
    </div>
    <div class="grid g-2" style="margin-top:14px">
      ${card('Latency distribution', '<div class="chart" id="lgCdf"></div>', { sub: 'Empirical CDF; tails matter more than means' })}
      ${card('Stage means and 99th percentiles', '<div class="chart" id="lgStage"></div>')}
      ${card('By ground speed', table([{ h: 'Speed m/s', f: (r) => r.speed }, { h: 'Events', r: 1, f: (r) => r.n }, { h: 'p50 ms', r: 1, f: (r) => fmt.n(r.p50) }, { h: 'p99 ms', r: 1, f: (r) => fmt.n(r.p99) }, { h: 'Miss ratio', r: 1, f: (r) => `<span style="color:${r.dmr > .01 ? 'var(--crit)' : 'var(--ink)'}">${fmt.pct(r.dmr, 2)}</span>` }], an.by_speed).html, { flush: true })}
      ${v ? card('Verification against entomology', `<div class="grid g-2" style="gap:6px;text-align:center;max-width:360px">
          <div class="card" style="padding:10px;box-shadow:none"><div class="val num" style="font-size:22px;font-weight:600">${v.tp}</div><div style="font-size:11.5px;color:var(--ink-3)">sprayed, habitat confirmed</div></div>
          <div class="card" style="padding:10px;box-shadow:none"><div class="num" style="font-size:22px;font-weight:600;color:var(--crit)">${v.fp}</div><div style="font-size:11.5px;color:var(--ink-3)">sprayed, no habitat</div></div>
          <div class="card" style="padding:10px;box-shadow:none"><div class="num" style="font-size:22px;font-weight:600;color:var(--warn)">${v.fn}</div><div style="font-size:11.5px;color:var(--ink-3)">withheld, habitat present</div></div>
          <div class="card" style="padding:10px;box-shadow:none"><div class="num" style="font-size:22px;font-weight:600">${v.tn}</div><div style="font-size:11.5px;color:var(--ink-3)">withheld, no habitat</div></div></div>`, { sub: 'Confidence gate outcome' }) : ''}
      ${card('Decision map', '<div class="map mid" id="lgMap"></div>', { flush: true, cls: 'span-all' })}
    </div>` + schema();

  chart('lgCdf', {
    grid: { left: 48, right: 16, top: 16, bottom: 36 }, tooltip: { trigger: 'axis', valueFormatter: (v) => fmt.pct(v, 0) },
    xAxis: { type: 'log', name: 'end-to-end latency ms', nameLocation: 'middle', nameGap: 24 }, yAxis: { type: 'value', max: 1, axisLabel: { formatter: (v) => `${v * 100}%` } },
    series: [{ type: 'line', showSymbol: false, step: 'end', data: an.cdf.map((c) => [c.ms, c.q]), lineStyle: { width: 2, color: series(1) }, areaStyle: { color: series(1), opacity: .07 },
      markLine: { symbol: 'none', lineStyle: { color: css('--ink-3'), type: 'dashed' }, label: { color: css('--ink-2'), formatter: '{b}' }, data: [{ name: 'p99', xAxis: an.latency.p99 }] } }],
  });
  chart('lgStage', {
    grid: { left: 70, right: 16, top: 30, bottom: 24 }, legend: { data: ['Mean', 'p99'] }, tooltip: { trigger: 'axis', valueFormatter: (v) => `${fmt.n(v, 1)} ms` },
    xAxis: { type: 'value', name: 'ms' }, yAxis: { type: 'category', data: an.stage_means.map((s) => s.stage) },
    series: [{ name: 'Mean', type: 'bar', barWidth: 8, data: an.stage_means.map((s) => s.ms), itemStyle: { color: series(1), borderRadius: [0, 4, 4, 0] } },
      { name: 'p99', type: 'bar', barWidth: 8, data: an.stage_means.map((s) => s.p99), itemStyle: { color: series(2), borderRadius: [0, 4, 4, 0] } }],
  });
  map?.remove();
  map = makeMap('lgMap');
  const col = { SPRAY: css('--signal'), ABSTAIN: css('--warn'), NO_HABITAT: css('--ink-3') };
  const pts = an.events.map((e) => { L.circleMarker([e.lat, e.lon], { radius: 6, color: css('--s1'), weight: 1, fillColor: col[e.decision], fillOpacity: .95 }).bindTooltip(`${e.target_id || 'opportunistic'} · ${e.decision}<br>conf ${e.confidence} · L ${fmt.n(e.t_cap_ms + e.t_pre_ms + e.t_inf_ms + e.t_dec_ms + e.t_act_ms)} ms`).addTo(map); return [e.lat, e.lon]; });
  if (pts.length) map.fitBounds(pts, { padding: [20, 20] });
  bindLogs(st);
}

function bindLogs(st) {
  document.querySelectorAll('[data-del]').forEach((b) => b.addEventListener('click', async () => {
    try { await api.del(`/api/flightlogs/${b.dataset.del}`); toast('Log removed'); logs(st); } catch (e) { toast(e.message, true); }
  }));
  document.getElementById('synBtn')?.addEventListener('click', async () => {
    const mid = document.getElementById('synMid').value;
    try { const r = await api.post(`/api/missions/${mid}/synthetic-log`, {}); toast(`Bench test file generated: ${r.rows} events, stamped SYNTHETIC`); logs(st); } catch (e) { toast(e.message, true); }
  });
}

function schema() {
  return `<div style="margin-top:14px" id="schemaBox"></div>` + (setTimeout(async () => {
    const s = await api.get('/api/flightlogs/schema');
    const b = document.getElementById('schemaBox');
    if (b) b.innerHTML = card('Flight log contract', table([{ h: 'Column', f: (r) => `<span class="mono">${r.column}</span>` }, { h: 'Meaning', k: 'description' }, { h: 'Example', f: (r) => `<span class="mono">${esc(r.example)}</span>` }], s, { max: 320 }).html, { flush: true, sub: 'One row per onboard decision, written by the companion computer' });
  }), '');
}

function frontier() {
  const box = document.getElementById('tab-frontier');
  if (box.dataset.ready) return;
  box.dataset.ready = 1;
  const example = 'variant,precision,latency_ms,energy_mj,size_mb,iou\nfp32_baseline,FP32,0,0,0,0\n';
  box.innerHTML = `<div class="grid g-main">
    ${card('Accuracy against latency', '<div class="chart xl" id="frChart"><div class="empty">' + icon('impact') + '<b>No benchmark loaded</b><span>Load the on-board benchmark CSV produced by the compression study. Each row is one model variant timed on the companion computer.</span></div></div>', { sub: 'Pareto frontier highlighted' })}
    ${card('Benchmark file', `<p style="margin:0 0 10px;color:var(--ink-2);font-size:12.5px">Columns: <span class="mono">variant, precision, latency_ms, energy_mj, size_mb, iou</span>. Time each variant 1 000 times on the target board and report the median; measure energy at the board rail, not from TDP.</p>
      <div class="actions"><label class="btn primary" for="frFile">${icon('upload')}Load benchmark CSV</label><input type="file" id="frFile" accept=".csv" hidden><button class="btn" id="frTpl">${icon('download')}Template</button></div>
      <div id="frTable" style="margin-top:12px"></div>`)}
  </div>`;
  document.getElementById('frTpl').addEventListener('click', () => download('rinda_compression_benchmark_template.csv', example, 'text/csv'));
  document.getElementById('frFile').addEventListener('change', async (e) => {
    const txt = await e.target.files[0].text();
    const [head, ...lines] = txt.trim().split(/\r?\n/);
    const cols = head.split(',').map((c) => c.trim());
    const rows = lines.map((l) => Object.fromEntries(l.split(',').map((v, i) => [cols[i], isNaN(+v) ? v : +v])));
    const pareto = rows.filter((r) => !rows.some((o) => o !== r && o.latency_ms <= r.latency_ms && o.iou >= r.iou && (o.latency_ms < r.latency_ms || o.iou > r.iou))).sort((a, b) => a.latency_ms - b.latency_ms);
    document.getElementById('frChart').innerHTML = '';
    chart('frChart', {
      grid: { left: 52, right: 20, top: 30, bottom: 40 }, legend: { data: ['Variants', 'Pareto frontier'] },
      tooltip: { trigger: 'item', formatter: (p) => { const r = p.data.r || {}; return `<b>${esc(r.variant)}</b><br>${r.precision} · ${r.size_mb} MB<br>IoU ${r.iou} · ${r.latency_ms} ms · ${r.energy_mj} mJ`; } },
      xAxis: { type: 'value', name: 'median latency ms', nameLocation: 'middle', nameGap: 26 }, yAxis: { type: 'value', name: 'IoU', scale: true },
      series: [{ name: 'Variants', type: 'scatter', symbolSize: (d) => 8 + Math.sqrt(d[2] || 1) * 2, data: rows.map((r) => ({ value: [r.latency_ms, r.iou, r.size_mb], r })), itemStyle: { color: series(1), borderColor: css('--s1'), borderWidth: 2 } },
        { name: 'Pareto frontier', type: 'line', step: 'end', symbol: 'none', data: pareto.map((r) => [r.latency_ms, r.iou]), lineStyle: { color: series(2), width: 2 } }],
    });
    document.getElementById('frTable').innerHTML = table([{ h: 'Variant', f: (r) => `<span class="mono">${esc(r.variant)}</span>` }, { h: 'IoU', r: 1, k: 'iou' }, { h: 'ms', r: 1, k: 'latency_ms' }, { h: 'mJ', r: 1, k: 'energy_mj' }, { h: 'MB', r: 1, k: 'size_mb' }, { h: 'Frontier', f: (r) => (pareto.includes(r) ? statusTag('APPROVED').replace('Approved', 'Pareto') : '') }], rows, { max: 300 }).html;
  });
}
