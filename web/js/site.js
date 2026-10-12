// Site dossier: everything known about one monitoring point, in one panel.
import { api, chart, drawer, fmt, esc, icon, catTag, css, series, CAT_LABEL } from './ui.js';

const FAMILY_COLOR = { 'Radar surface state': 1, Rainfall: 3, Temperature: 2, Humidity: 7, 'Acquisition quality': 4, Other: 5 };
const ACTION = {
  high: 'Assign a field inspection (dipping) before the target date. Do not treat on the forecast alone.',
  moderate: 'Inspect when capacity allows, after High-priority points.',
  insufficient: 'The model cannot classify this point. Inspect if it lies in an area of programme concern.',
  lower: 'No forecast-driven action. Routine surveillance continues; this is not evidence of absence of larvae.',
};

export async function openSite(id, date, h, st) {
  drawer.open(`Monitoring point <span class="mono">${esc(id)}</span>`, '<div class="skeleton" style="height:120px"></div><div class="skeleton" style="height:240px"></div>');
  let s, ex, meta;
  try {
    [s, ex, meta] = await Promise.all([api.get(`/api/site/${id}`), api.get(`/api/explain?site=${id}&date=${date}&h=${h}`), api.get(`/api/forecast/meta?h=${h}&date=${date}`)]);
  } catch (e) {
    drawer.body().innerHTML = `<div class="note crit">${esc(e.message)}</div>`;
    return;
  }
  const fc = (s.forecasts[h] || []).find((f) => f.date === date);
  const cat = fc?.category;
  const body = drawer.body();
  const outcome = fc && fc.y != null ? (fc.y ? '<span class="status st-crit">Water formed</span>' : '<span class="status st-mute">Stayed dry</span>') : '<span class="status st-mute">Not yet observed</span>';
  const stale = meta.warnings.filter((w) => ['stale_forecast', 'radar_lag', 'weather_gap'].includes(w.code));
  const fld = s.field || {};
  body.innerHTML = `
    ${meta.status === 'stale' ? `<div class="note crit" role="alert"><b>Historical forecast.</b> Issued ${meta.age_days} days ago; not guidance for today.</div>` : ''}
    <div class="grid g-3">
      <div><div class="kpi" style="padding:0"><div class="lbl">Verification priority</div><div style="margin-top:8px">${cat ? catTag(cat) : '–'}</div><div class="meta">${cat ? esc(CAT_LABEL[cat]) : 'No forecast for this point on this issue'}</div></div></div>
      <div><div class="kpi" style="padding:0"><div class="lbl">Probability of new water</div><div class="val">${fc ? fc.probability.toFixed(3) : '–'}</div><div class="meta">Calibrated · uncertainty ${fc ? fc.uncertainty_band : '–'}</div></div></div>
      <div><div class="kpi" style="padding:0"><div class="lbl">Radar outcome</div><div style="margin-top:8px">${outcome}</div><div class="meta">Checked ${meta.valid_window[0]} – ${meta.valid_window[1]}</div></div></div>
    </div>
    <section class="card"><div class="card-h"><h3>Recommended action</h3><span class="sub">per operational rules ${esc(st?.boot?.rules_version || '')}</span></div>
      <div class="card-b"><p style="margin:0">${cat ? esc(ACTION[cat]) : 'None.'}</p>
      ${st?.can?.('assign:task') && cat && cat !== 'lower' ? `<div class="actions" style="margin-top:10px"><button class="btn sm primary" id="dsTask">${icon('clipboard')}Assign inspection</button></div>` : ''}</div></section>
    <section class="card"><div class="card-h"><h3>Forecast</h3></div><div class="card-b"><div class="meta-grid">
      <div><span>Issue date</span>${meta.issue_date} (${h}-day)</div>
      <div><span>Valid window</span>${meta.valid_window[0]} – ${meta.valid_window[1]}</div>
      <div><span>Predicted target</span>New surface water at this 50 m circle</div>
      <div><span>Units</span>Probability 0–1 and a 90% call</div>
      <div><span>Position</span><span class="mono" style="display:inline;text-transform:none;letter-spacing:0;color:var(--ink)">${fmt.ll(s.lat, s.lon)}</span> (centre of circle)</div>
      <div><span>Radar input</span>${meta.inputs.radar_last_pass} (${meta.inputs.radar_lag_days} d before issue)</div>
      <div><span>Weather input</span>${meta.inputs.weather_last_day} (${meta.inputs.weather_lag_days} d before issue)</div>
      <div><span>Model</span><span class="mono" style="display:inline;text-transform:none;letter-spacing:0;color:var(--ink)">${meta.model.fingerprint}</span> · research stage</div>
    </div>${stale.length ? `<ul style="margin:10px 0 0;padding-left:18px;color:var(--warn);font-size:12px">${stale.map((w) => `<li>${esc(w.message)}</li>`).join('')}</ul>` : ''}
    <p style="margin:10px 0 0;font-size:11.5px;color:var(--ink-3)">${esc(st?.boot?.spatial_resolution || '')}</p></div></section>
    <section class="card"><div class="card-h"><h3>Field evidence</h3><span class="sub">observations and interventions here</span></div><div class="card-b">${fieldHTML(fld)}</div></section>
    <section class="card"><div class="card-h"><h3>Radar backscatter</h3><span class="sub">Sentinel-1 VV / VH, 50 m buffer</span></div>
      <div class="card-b"><div class="chart" id="sRadar"></div>
      <div class="legend"><span><i class="ln" style="background:${series(1)}"></i>VV</span><span><i class="ln" style="background:${series(3)}"></i>VH</span><span><i class="ln" style="background:var(--ink-3)"></i>Water threshold (VV ${s.water_thresholds.vv_db_max} dB, prototype)</span><span><i style="background:var(--brand-soft);border:1px solid var(--brand)"></i>Classified wet</span></div>
      <dl class="dl" style="margin-top:8px"><dt>Wet on</dt><dd>${fmt.pct(s.wet_fraction)} of ${s.n_acquisitions} passes</dd><dt>Distance to weather station</dt><dd>${fmt.n(s.distance_to_station_km, 1)} km</dd></dl></div></section>
    <section class="card"><div class="card-h"><h3>Forecast history</h3><span class="sub">Issued probability vs what the radar saw</span></div>
      <div class="card-b"><div class="chart sm" id="sHist"></div></div></section>
    ${ex.available ? `<section class="card"><div class="card-h"><h3>Environmental factors behind this forecast</h3><span class="sub">Additive log-odds contributions</span></div>
      <div class="card-b"><div class="chart sm" id="sFam"></div><div class="chart" id="sDrv" style="height:300px"></div>
      <p style="font-size:11.5px;color:var(--ink-3);margin:6px 0 0">Bars right of zero push towards formation. Weather comes from one station, so rainfall terms are identical for every point on a date; point-to-point differences come from radar.</p></div></section>` : ''}
    <div class="actions"><a class="btn" href="#/scenario?site=${id}&date=${date}">${icon('scenario')}Open in Scenario Lab</a><a class="btn" href="#/replay">${icon('replay')}Validation for this issue</a></div>`;

  body.querySelector('#dsTask')?.addEventListener('click', async () => {
    const { taskDialog } = await import('./views/field.js');
    drawer.close();
    taskDialog({ site_id: id, priority: cat, issue_date: date, horizon: h, due: meta.target_date, alert_id: cat === 'high' ? `A-${date}-H${String(h).padStart(2, '0')}-${id}` : null, reason: `Forecast ${date} (${h}-day): ${CAT_LABEL[cat]}. Verify surface water and dip for larvae.` });
  });

  const r = s.radar;
  const wetBands = [];
  r.forEach((p, i) => { if (p.wet) wetBands.push([{ xAxis: p.date }, { xAxis: r[Math.min(i + 1, r.length - 1)].date }]); });
  chart('sRadar', {
    grid: { left: 40, right: 12, top: 14, bottom: 46 }, legend: { show: false },
    xAxis: { type: 'time' }, yAxis: { type: 'value', name: 'dB', scale: true },
    dataZoom: [{ type: 'inside' }, { type: 'slider', height: 16, bottom: 6, borderColor: 'transparent', backgroundColor: css('--s2'), fillerColor: css('--brand-soft'), textStyle: { color: css('--ink-3') } }],
    series: [
      { name: 'VV', type: 'line', showSymbol: false, data: r.map((p) => [p.date, p.vv_db]), lineStyle: { width: 1.6, color: series(1) }, itemStyle: { color: series(1) },
        markArea: { silent: true, itemStyle: { color: css('--brand-soft') }, data: wetBands },
        markLine: { silent: true, symbol: 'none', lineStyle: { color: css('--ink-3'), type: 'dashed' }, label: { show: false }, data: [{ yAxis: s.water_thresholds.vv_db_max }, { xAxis: date }] } },
      { name: 'VH', type: 'line', showSymbol: false, data: r.map((p) => [p.date, p.vh_db]), lineStyle: { width: 1.6, color: series(3) }, itemStyle: { color: series(3) } },
    ],
  });
  const hist = s.forecasts[h] || [];
  chart('sHist', {
    grid: { left: 40, right: 12, top: 30, bottom: 24 }, legend: { data: ['Probability', 'Water formed'] },
    xAxis: { type: 'time' }, yAxis: { type: 'value', max: 1, min: 0 },
    series: [
      { name: 'Probability', type: 'line', step: 'middle', showSymbol: false, data: hist.map((f) => [f.date, f.probability]), lineStyle: { width: 2, color: css('--signal') }, itemStyle: { color: css('--signal') }, areaStyle: { color: css('--signal'), opacity: 0.08 },
        markLine: { silent: true, symbol: 'none', label: { show: false }, lineStyle: { color: css('--ink-3'), type: 'dashed' }, data: [{ xAxis: date }] } },
      { name: 'Water formed', type: 'scatter', symbolSize: 9, data: hist.filter((f) => f.y === 1).map((f) => [f.date, 1]), itemStyle: { color: css('--crit'), borderColor: css('--s1'), borderWidth: 2 } },
    ],
  });
  if (ex.available) {
    const fam = ex.families.filter((f) => Math.abs(f.contribution) > 1e-4);
    chart('sFam', {
      grid: { left: 130, right: 20, top: 6, bottom: 20 }, tooltip: { trigger: 'item', valueFormatter: (v) => v.toFixed(3) },
      xAxis: { type: 'value' }, yAxis: { type: 'category', data: fam.map((f) => f.family).reverse() },
      series: [{ type: 'bar', barWidth: 12, data: fam.map((f) => ({ value: f.contribution, itemStyle: { color: series(FAMILY_COLOR[f.family] || 5), borderRadius: f.contribution >= 0 ? [0, 4, 4, 0] : [4, 0, 0, 4] } })).reverse() }],
    });
    const d = ex.drivers.slice(0, 10).reverse();
    chart('sDrv', {
      grid: { left: 170, right: 20, top: 8, bottom: 20 },
      tooltip: { trigger: 'item', formatter: (p) => `<b class="mono">${d[p.dataIndex].feature}</b><br>value ${d[p.dataIndex].value == null ? 'missing' : d[p.dataIndex].value.toFixed(2)}<br>contribution ${p.value.toFixed(3)}` },
      xAxis: { type: 'value' }, yAxis: { type: 'category', data: d.map((x) => x.feature), axisLabel: { fontFamily: 'IBM Plex Mono', fontSize: 10.5 } },
      series: [{ type: 'bar', barWidth: 10, data: d.map((x) => ({ value: x.contribution, itemStyle: { color: series(FAMILY_COLOR[x.family] || 5), borderRadius: x.contribution >= 0 ? [0, 4, 4, 0] : [4, 0, 0, 4] } })) }],
    });
  }
}

function fieldHTML(f) {
  if (f.restricted) return `<p style="margin:0;color:var(--ink-2)">${icon('lock')} ${f.n_observations} field observation(s) recorded here. Sign in with a field, analyst or supervisor role to see them.</p>`;
  const o = f.observations || [], iv = f.interventions || [];
  if (!o.length && !iv.length) return '<p style="margin:0;color:var(--ink-2)">No field observations at this point. The forecast is the only evidence; nothing here confirms or rules out larvae.</p>';
  return `<div class="list" style="margin:-4px -15px">${o.map((x) => `<div><span><span class="mono">O${x.id}</span> · ${x.observed_at.slice(0, 10)} · water ${x.water_present} · larvae ${x.larvae_present.replace('_', ' ')}${x.dips ? ` (${x.larvae_count ?? 0} in ${x.dips} dips)` : ''}${x.demo ? ' <span class="badge">DEMO</span>' : ''}</span><span class="sub">${x.status.replace('_', ' ')}</span></div>`).join('')}
    ${iv.map((x) => `<div><span><span class="mono">I${x.id}</span> · ${x.method.replace('_', ' ')}${x.demo ? ' <span class="badge">DEMO</span>' : ''}</span><span class="sub">${x.status}${x.outcome ? ` · ${x.outcome.replace('_', ' ')}` : ''}</span></div>`).join('')}</div>`;
}
