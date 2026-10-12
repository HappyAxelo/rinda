// Site dossier: everything known about one monitored site, in one panel.
import { api, chart, drawer, fmt, esc, icon, riskTag, decisionTag, css, series } from './ui.js';

const FAMILY_COLOR = { 'Radar surface state': 1, Rainfall: 3, Temperature: 2, Humidity: 7, 'Acquisition quality': 4, Other: 5 };

export async function openSite(id, date, h) {
  drawer.open(`Site <span class="mono">${esc(id)}</span>`, '<div class="skeleton" style="height:120px"></div><div class="skeleton" style="height:240px"></div>');
  const [s, ex] = await Promise.all([api.get(`/api/site/${id}`), api.get(`/api/explain?site=${id}&date=${date}&h=${h}`)]);
  const fc = (s.forecasts[h] || []).find((f) => f.date === date);
  const body = drawer.body();
  const label = fc && fc.y != null ? (fc.y ? '<span class="status st-crit">Water formed</span>' : '<span class="status st-mute">Stayed dry</span>') : '<span class="status st-mute">Not yet observed</span>';
  body.innerHTML = `
    <div class="grid g-3">
      <div><div class="kpi" style="padding:0"><div class="lbl">Probability · ${h}-day</div><div class="val">${fc ? fc.probability.toFixed(3) : '–'}</div><div class="meta">${fc ? riskTag(fc.risk_class) : ''}</div></div></div>
      <div><div class="kpi" style="padding:0"><div class="lbl">Call at 90% confidence</div><div style="margin-top:8px">${fc ? decisionTag(fc.conformal_decision) : '–'}</div><div class="meta">Uncertainty ${fc ? fc.uncertainty_band : '–'}</div></div></div>
      <div><div class="kpi" style="padding:0"><div class="lbl">Radar outcome at t+${h}</div><div style="margin-top:8px">${label}</div><div class="meta">Issue ${fmt.date(date)}</div></div></div>
    </div>
    <dl class="dl" style="grid-template-columns:auto 1fr auto 1fr">
      <dt>Position</dt><dd class="mono">${fmt.ll(s.lat, s.lon)}</dd>
      <dt>Wet passes</dt><dd>${fmt.pct(s.wet_fraction)}</dd>
      <dt>Radar acquisitions</dt><dd>${s.n_acquisitions}</dd>
      <dt>To weather station</dt><dd>${fmt.n(s.distance_to_station_km, 1)} km</dd>
    </dl>
    <section class="card"><div class="card-h"><h3>Radar backscatter</h3><span class="sub">Sentinel-1 VV / VH, 50 m buffer</span></div>
      <div class="card-b"><div class="chart" id="sRadar"></div>
      <div class="legend"><span><i class="ln" style="background:${series(1)}"></i>VV</span><span><i class="ln" style="background:${series(3)}"></i>VH</span><span><i class="ln" style="background:var(--ink-3)"></i>Water threshold (VV ${s.water_thresholds.vv_db_max} dB)</span><span><i style="background:var(--brand-soft);border:1px solid var(--brand)"></i>Classified wet</span></div></div></section>
    <section class="card"><div class="card-h"><h3>Forecast history</h3><span class="sub">Issued probability vs what the radar saw</span></div>
      <div class="card-b"><div class="chart sm" id="sHist"></div></div></section>
    ${ex.available ? `<section class="card"><div class="card-h"><h3>Why the model says this</h3><span class="sub">Additive log-odds contributions</span></div>
      <div class="card-b"><div class="chart sm" id="sFam"></div><div class="chart" id="sDrv" style="height:300px"></div>
      <p style="font-size:11.5px;color:var(--ink-3);margin:6px 0 0">Bars right of zero push towards formation. Weather comes from one station, so rainfall terms are identical for every site on a date; site-to-site differences come from radar.</p></div></section>` : ''}
    <div class="actions"><a class="btn primary" href="#/scenario?site=${id}&date=${date}">${icon('scenario')}Open in Scenario Lab</a>
    <a class="btn" href="#/missions?add=${id}">${icon('mission')}Plan mission from this date</a></div>`;

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
        markLine: { silent: true, symbol: 'none', lineStyle: { color: css('--ink-3'), type: 'dashed' }, label: { show: false }, data: [{ yAxis: s.water_thresholds.vv_db_max }] } },
      { name: 'VH', type: 'line', showSymbol: false, data: r.map((p) => [p.date, p.vh_db]), lineStyle: { width: 1.6, color: series(3) }, itemStyle: { color: series(3) } },
    ],
  });
  const hist = s.forecasts[h] || [];
  chart('sHist', {
    grid: { left: 40, right: 12, top: 30, bottom: 24 }, legend: { data: ['Probability', 'Water formed'] },
    xAxis: { type: 'time' }, yAxis: { type: 'value', max: 1, min: 0 },
    series: [
      { name: 'Probability', type: 'line', step: 'middle', showSymbol: false, data: hist.map((f) => [f.date, f.probability]), lineStyle: { width: 2, color: css('--signal') }, itemStyle: { color: css('--signal') }, areaStyle: { color: css('--signal'), opacity: .08 },
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
