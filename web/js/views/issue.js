import { api, card, chart, fmt, icon, table, makeMap, legendControl, riskLegend, siteMarker, footprintLayer, catTag, css, series, esc } from '../ui.js';

let map;
export function destroy() { map?.remove(); map = null; }

const ICON = { critical: 'alert', warn: 'info', info: 'info' };

export async function render(el, st) {
  const h = st.h, b = st.boot;
  const [fc, ver, alerts, fs] = await Promise.all([
    api.get(`/api/forecast?h=${h}&date=${st.date}`), api.get(`/api/verification?h=${h}`),
    api.get(`/api/alerts?h=${h}&date=${st.date}`), api.get('/api/field/summary'),
  ]);
  const canField = st.can('read:field');
  const [tasks, obs, ivs] = canField ? await Promise.all([api.get('/api/tasks'), api.get('/api/observations'), api.get('/api/interventions')]) : [[], [], []];
  const m = fc.meta, s = fc.summary, c = s.categories;
  const cats = b.categories;
  const openAlerts = alerts.filter((a) => a.state === 'open');
  const tasked = new Set(tasks.filter((t) => t.issue_date === s.issue_date && t.status !== 'cancelled').map((t) => t.site_id));
  const awaiting = fc.rows.filter((r) => (r.category === 'high' || r.category === 'insufficient') && !tasked.has(r.site_id));
  const outstanding = tasks.filter((t) => ['assigned', 'in_progress'].includes(t.status));
  const moderateOn = cats.moderate && c.moderate > 0;

  el.innerHTML = `
  <div class="page-head">
    <div><h1>Situation overview</h1>
      <p>${esc(b.tenant.name)}, ${esc(b.tenant.district)} District. ${esc(b.target)}</p></div>
    <div class="actions">
      <a class="btn" href="/api/v1/bulletin?h=${h}&date=${s.issue_date}" target="_blank" rel="noopener">${icon('print')}Bulletin</a>
      ${st.can('assign:task') ? `<a class="btn primary" href="#/field">${icon('clipboard')}Assign inspections</a>` : `<a class="btn primary" href="#/forecast">${icon('forecast')}Open forecast</a>`}
    </div>
  </div>

  <div class="status-strip" role="group" aria-label="Forecast status">
    <div><div class="k">Forecast issued</div><div class="v">${fmt.date(m.issue_date)}</div><div class="m">${h}-day horizon${m.is_latest ? ' · latest issue' : ` · latest is ${fmt.dshort(m.latest_issue)}`}</div></div>
    <div><div class="k">Valid for</div><div class="v">${fmt.dshort(m.valid_window[0])} – ${fmt.date(m.valid_window[1])}</div><div class="m">Target date ${fmt.date(m.target_date)} ± 2 days</div></div>
    <div><div class="k">Freshness</div><div class="v" style="color:${m.status === 'stale' ? 'var(--crit)' : 'var(--good)'}">${m.status === 'stale' ? `Stale · ${m.age_days} days old` : 'Current'}</div><div class="m">Generated ${m.generated_utc ? fmt.date(m.generated_utc.slice(0, 10)) : '–'} (hindcast run)</div></div>
    <div><div class="k">Inputs at issue</div><div class="v">Radar ${fmt.dshort(m.inputs.radar_last_pass)} · Weather ${fmt.dshort(m.inputs.weather_last_day)}</div><div class="m">${m.inputs.weather_lag_days > 2 ? `<span style="color:var(--warn)">Weather ${m.inputs.weather_lag_days} days behind issue</span>` : 'Inputs on time'}</div></div>
    <div><div class="k">Model</div><div class="v" style="color:var(--warn)">Research stage</div><div class="m"><span class="mono">${m.model.fingerprint}</span> · <a href="#/replay">validation</a></div></div>
  </div>

  <div class="cat-row" style="margin-top:14px">
    ${['high', 'moderate', 'insufficient', 'lower'].map((k) => `
      <section class="cat-tile ${k === 'moderate' && !moderateOn ? 'disabled' : ''}" title="Rule: ${esc(cats[k].rule)}">
        <div>${catTag(k, false)}</div>
        <div class="n">${k === 'moderate' && !moderateOn ? '–' : fmt.n(c[k] || 0)}</div>
        <div class="d">${k === 'moderate' && !moderateOn ? 'Not configured: needs a threshold agreed with the programme.' : esc(cats[k].meaning)}</div>
      </section>`).join('')}
  </div>

  <div class="grid g-main" style="margin-top:14px">
    ${card('Monitoring points', '<div class="map-host"><div class="map mid" id="ovMap" role="region" aria-label="Map of monitoring points by verification priority"></div></div>', { flush: true, sub: `${fmt.n(s.n_sites)} points assessed · click for evidence`, foot: '<span>Each point is a 50 m radar sample, not a confirmed breeding site.</span><a href="#/forecast">Table view</a>' })}
    <div class="grid" style="align-content:start">
      ${card('Alerts and warnings', alertsHTML(m, alerts, openAlerts), { flush: true, sub: `${openAlerts.length} open` })}
      ${card('Awaiting field verification', `<div class="list">${awaiting.slice(0, 6).map((r) => `<div><span><span class="mono">${r.site_id}</span> ${catTag(r.category)}</span><span class="sub">p ${r.probability.toFixed(2)}</span></div>`).join('') || '<div class="empty sm">Every flagged point on this issue has an inspection task.</div>'}</div>`,
        { flush: true, sub: `${awaiting.length} without a task`, foot: canField ? '<a href="#/field">Field surveillance</a>' : '<span>Sign in as a supervisor to assign inspections.</span>' })}
    </div>
  </div>

  <div class="grid g-3" style="margin-top:14px">
    ${card('Outstanding inspections', canField ? listTasks(outstanding) : restricted(fs.tasks, 'tasks'), { flush: true, sub: `${(fs.tasks.assigned || 0) + (fs.tasks.in_progress || 0)} open · ${fs.overdue_tasks} overdue` })}
    ${card('Recent field observations', canField ? listObs(obs.slice(0, 6)) : restricted(fs.observations, 'observations'), { flush: true, sub: fs.last_observation ? `last ${fmt.date(fs.last_observation.slice(0, 10))}` : 'none recorded' })}
    ${card('Interventions and follow-up', canField ? listIv(ivs.slice(0, 6)) : restricted(fs.interventions, 'interventions'), { flush: true, sub: `${(fs.interventions.authorized || 0) + (fs.interventions.performed || 0)} awaiting follow-up` })}
  </div>

  <div class="grid g-3" style="margin-top:14px">
    ${card('Priority queue for this issue', '<div id="ovQueue"></div>', { flush: true, cls: 'span-2', sub: 'High priority first, then insufficient evidence' })}
    ${card('Calls against later radar', '<div class="chart" id="ovTimeline"></div>', { sub: 'High-priority calls and formations observed, per issue', foot: '<span>Click a bar to open that issue.</span><a href="#/replay">Full validation</a>' })}
  </div>`;

  map = makeMap('ovMap', { zoom: 12.5 });
  const rows = [...fc.rows].sort((a, z) => (a.category === 'high') - (z.category === 'high') || a.probability - z.probability);
  rows.forEach((r) => siteMarker(r, { onClick: (x) => window.openSite(x.site_id) }).addTo(map));
  footprintLayer(map, fc.rows);
  L.marker([b.station.lat, b.station.lon], { icon: L.divIcon({ className: '', html: '<div class="station-pin"></div>', iconSize: [16, 16] }), title: 'Gikomero weather station' }).bindTooltip('Gikomero automatic weather station').addTo(map);
  map.fitBounds(fc.rows.map((r) => [r.latitude, r.longitude]), { padding: [20, 20] });
  legendControl(map, riskLegend());

  const queue = fc.rows.filter((r) => r.category === 'high' || r.category === 'insufficient')
    .sort((a, z) => (z.category === 'high') - (a.category === 'high') || z.priority_score - a.priority_score).slice(0, 15);
  const t = table([
    { h: 'Point', f: (r) => `<span class="mono">${r.site_id}</span>` },
    { h: 'Priority', f: (r) => catTag(r.category) },
    { h: 'Probability', r: 1, f: (r) => r.probability.toFixed(3) },
    { h: 'Uncertainty', f: (r) => r.uncertainty_band },
    { h: 'Why', f: (r) => `<span style="color:var(--ink-2)">${r.category === 'high' ? '90% set contains only formation' : 'Model cannot decide at 90%'}${r.WATER_PERSISTENCE_PASSES ? ` · wet on ${r.WATER_PERSISTENCE_PASSES} recent passes` : ''}</span>` },
    { h: 'Task', f: (r) => (tasked.has(r.site_id) ? '<span class="status st-good">Assigned</span>' : '<span style="color:var(--ink-3)">None</span>') },
  ], queue, { onRow: (r) => window.openSite(r.site_id), max: 360, empty: 'Nothing flagged on this issue.' });
  document.getElementById('ovQueue').innerHTML = t.html; t.bind();

  const ch = chart('ovTimeline', {
    grid: { left: 36, right: 10, top: 36, bottom: 26 },
    legend: { data: ['High-priority calls', 'Formations observed'] },
    xAxis: { type: 'category', data: ver.map((v) => fmt.dshort(v.date)) }, yAxis: { type: 'value', name: 'points' },
    series: [
      { name: 'High-priority calls', type: 'bar', barMaxWidth: 10, data: ver.map((v) => v.formation_calls), itemStyle: { color: css('--cat-high'), borderRadius: [2, 2, 0, 0] } },
      { name: 'Formations observed', type: 'line', symbolSize: 4, data: ver.map((v) => v.observed_formation), lineStyle: { width: 2, color: series(1) }, itemStyle: { color: series(1) } },
    ],
  });
  ch.on('click', (p) => { const sel = document.getElementById('dateSel'); sel.value = ver[p.dataIndex].date; sel.dispatchEvent(new Event('change')); });
}

function alertsHTML(m, alerts, open) {
  const items = m.warnings.map((w) => `<div><span>${icon(ICON[w.level])} ${esc(w.message)}</span></div>`);
  if (alerts.length) {
    const n = (s) => alerts.filter((a) => a.state === s).length;
    items.push(`<div><span>${icon('bell')} <b>${alerts.length}</b> high-priority alerts on this issue: ${open.length} open, ${n('actioned')} with a task, ${n('acknowledged')} acknowledged, ${n('dismissed')} dismissed, ${n('expired')} expired.</span>${open.length ? '<a class="btn sm" href="#/field">Review</a>' : ''}</div>`);
  } else items.push('<div><span class="sub">No high-priority alerts on this issue.</span></div>');
  return `<div class="list" style="max-height:260px;overflow:auto">${items.join('')}</div>`;
}
function restricted(counts, what) {
  const n = Object.values(counts || {}).reduce((a, x) => a + x, 0);
  return `<div class="empty sm">${icon('lock')}<span>${n} ${what} recorded. Details are visible to signed-in field, analyst and supervisor roles.</span></div>`;
}
function listTasks(ts) {
  if (!ts.length) return '<div class="empty sm">No open inspection tasks.</div>';
  return `<div class="list">${ts.slice(0, 6).map((t) => `<div><span><span class="mono">T${t.id}</span> · <span class="mono">${t.site_id}</span> · ${esc(t.assignee || '')}${t.demo ? ' <span class="badge">DEMO</span>' : ''}</span><span class="sub" style="${t.overdue ? 'color:var(--crit)' : ''}">due ${fmt.dshort(t.due_date)}${t.overdue ? ' · overdue' : ''}</span></div>`).join('')}</div>`;
}
function listObs(os) {
  if (!os.length) return '<div class="empty sm">No field observations yet. Field officers record them under Field Surveillance.</div>';
  return `<div class="list">${os.map((o) => `<div><span><span class="mono">${o.site_id}</span> · water ${o.water_present} · larvae ${o.larvae_present.replace('_', ' ')}${o.demo ? ' <span class="badge">DEMO</span>' : ''}</span><span class="sub">${o.status.replace('_', ' ')} · ${fmt.dshort(o.observed_at.slice(0, 10))}</span></div>`).join('')}</div>`;
}
function listIv(is) {
  if (!is.length) return '<div class="empty sm">No interventions authorised.</div>';
  return `<div class="list">${is.map((i) => `<div><span><span class="mono">I${i.id}</span> · <span class="mono">${i.site_id}</span> · ${i.method.replace('_', ' ')}${i.demo ? ' <span class="badge">DEMO</span>' : ''}</span><span class="sub">${i.status}${i.outcome ? ` · ${i.outcome.replace('_', ' ')}` : ''}</span></div>`).join('')}</div>`;
}
