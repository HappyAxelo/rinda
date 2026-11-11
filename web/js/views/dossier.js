// Unified habitat dossier: one profile per monitoring location or field-registered
// habitat. The same summary feeds the map side panel and the drawer; the full
// page adds the complete history. Evidence kinds are never merged.
import { api, card, chart, drawer, fmt, esc, icon, css, series, evi, opStatus, auditNote, empty, pill, NODATA, table } from '../ui.js';
import { params } from '../app.js';

const yes = (v) => ({ yes: 'Yes', no: 'No', unknown: 'Unknown', not_sampled: 'Not sampled', not_identified: 'Not identified' }[v] || v || '–');
const SPECIES = { an_gambiae_sl: 'An. gambiae s.l.', an_arabiensis: 'An. arabiensis', an_funestus: 'An. funestus', an_other: 'Other Anopheles', culicine: 'Culicine', not_identified: 'Not identified' };

function latest(d, st) {
  const rows = d.forecasts?.[st.h] || d.forecasts?.[String(st.h)] || [];
  return rows.filter((r) => r.date <= st.date).pop() || null;
}

function adminLine(a) {
  const parts = [a.cell && `${a.cell} Cell`, a.sector && `${a.sector} Sector`, `${a.district} District`, a.province].filter(Boolean);
  return parts.join(' · ');
}

function steps(d) {
  if (d.restricted) return '';
  const o = d.observations || [], iv = (d.interventions || []).filter((i) => i.status !== 'rejected');
  const ver = o.filter((x) => x.status === 'verified');
  const s = [
    ['Inspected', o.length > 0], ['Reviewed', ver.length > 0], ['Larvae confirmed', ver.some((x) => x.larvae_present === 'yes')],
    ['Treatment authorised', iv.length > 0], ['Treated', iv.some((i) => ['performed', 'closed'].includes(i.status))], ['Follow-up done', iv.some((i) => i.status === 'closed')],
  ];
  return `<div class="steps-h" aria-label="Progress at this location">${s.map(([l, done]) => `<div class="${done ? 'done' : ''}">${l}</div>`).join('')}</div>`;
}

function forecastBlock(d, st) {
  if (d.kind === 'habitat') return '<p class="hint" style="margin:0">Field-registered habitats carry no forecast. Forecasts exist only for the 600 radar monitoring locations.</p>';
  const f = latest(d, st);
  if (!f) return `<p class="nodata" style="margin:0">Forecast unavailable for this location on the selected issue.</p>`;
  const meta = st.meta;
  return `<div class="kvs">
    <div><span>Signal</span><b>${esc(f.label)}</b></div>
    <div><span>Issued</span>${fmt.date(f.date)} · ${st.h}-day</div>
    <div><span>Target date</span>${meta ? fmt.date(meta.target_date) : '–'}</div>
    <div><span>Prediction score</span>${f.probability.toFixed(2)} · ${esc(f.uncertainty_band)} uncertainty</div>
    <div><span>Model</span>${esc(st.boot.model.model_version)} · research only</div>
    <div><span>Later radar</span>${f.y == null ? 'Not yet observed' : f.y ? 'Water signal appeared' : 'No water signal'}</div>
  </div>
  <p class="hint" style="margin:10px 0 0">${meta?.status === 'stale' ? `Forecast stale: issued ${meta.age_days} days ago. ` : ''}A forecast is a research-only lead for inspection. It does not show mosquito breeding and never authorises treatment.</p>`;
}

function satBlock(d) {
  const s = d.satellite;
  if (!s) return '<p class="hint" style="margin:0">No satellite series: this habitat is not one of the radar monitoring locations.</p>';
  const last = s.radar.at(-1);
  return `<div class="kvs">
    <div><span>Latest radar pass</span>${last ? fmt.date(last.date) : NODATA}</div>
    <div><span>Water signal on that pass</span>${last ? (last.wet ? 'Yes' : 'No') : '–'}</div>
    <div><span>Passes with a water signal</span>${fmt.pct(s.wet_fraction)} of ${fmt.n(s.n_acquisitions)}</div>
    <div><span>Backscatter</span>${last ? `VV ${last.vv_db.toFixed(1)} dB · VH ${last.vh_db.toFixed(1)} dB` : '–'}</div>
  </div><p class="hint" style="margin:10px 0 0">Satellite-observed. A radar water signal is a 50 m sample and is not a confirmed breeding site.</p>`;
}

function fieldBlock(d) {
  if (d.restricted) {
    const c = d.field_counts;
    return `<p style="margin:0;color:var(--ink-2)">${icon('lock')} ${c.observations} field observation(s) and ${c.interventions} intervention record(s) here. Sign in with a field, analyst or supervisor role to see them.</p>`;
  }
  const o = d.observations || [];
  if (!o.length) return '<p class="nodata" style="margin:0">Not yet inspected. Nothing here confirms or rules out larvae.</p>';
  const x = o.at(-1);
  return `<div class="kvs">
    <div><span>Last inspection</span>${fmt.date(x.observed_at.slice(0, 10))}</div>
    <div><span>Review status</span>${esc(x.status.replace('_', ' '))}</div>
    <div><span>Water present</span>${yes(x.water_present)}</div>
    <div><span>Larvae</span>${yes(x.larvae_present)}${x.dips ? ` · ${x.larvae_count ?? 0} in ${x.dips} dips` : ''}</div>
    <div><span>Anopheles</span>${yes(x.anopheles_present)}</div>
    <div><span>Inspections on record</span>${o.length}</div>
  </div>`;
}

function actions(d, st, full) {
  const out = [];
  if (!full) out.push(`<a class="btn primary" href="#/dossier?id=${d.id}">${icon('file')}Open full dossier</a>`);
  if (st.can('assign:task')) out.push(`<button class="btn" data-act="task">${icon('cliplist')}Assign inspection</button>`);
  if (st.can('write:observation')) out.push(`<a class="btn" href="#/field?tab=record&site=${d.id}">${icon('upload')}Record observation</a>`);
  out.push(`<a class="btn" href="#/map?focus=${d.id}">${icon('map')}Show on map</a>`);
  if (d.kind !== 'habitat') out.push(`<button class="btn ghost" data-act="tech">${icon('assurance')}Technical forecast evidence</button>`);
  return `<div class="actions">${out.join('')}</div>`;
}

export function summaryHTML(d, st, { full = false } = {}) {
  return `
    <div><div class="eyebrow">${d.kind === 'habitat' ? 'Field-registered habitat' : 'Monitoring location'}</div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">${opStatus(d.status, d.status_label)} ${evi(d.evidence_tier)}</div>
      <p style="margin:8px 0 0;color:var(--ink-2);font-size:13.5px">${esc(adminLine(d.administrative))}<br><span class="mono">${fmt.ll(d.lat, d.lon)}</span></p>
      ${d.administrative.note ? `<p class="hint" style="margin:4px 0 0;color:var(--warn)">${esc(d.administrative.note)}</p>` : ''}
      ${d.responsible_officer ? `<p class="hint" style="margin:4px 0 0">Responsible officer: ${esc(d.responsible_officer)}</p>` : ''}</div>
    ${steps(d)}
    <section><h4 class="sec-h">${evi('predicted')}</h4>${forecastBlock(d, st)}</section>
    <section><h4 class="sec-h">${evi('satellite_observed')}</h4>${satBlock(d)}</section>
    <section><h4 class="sec-h">${evi('sensor_observed')}</h4><p class="nodata" style="margin:0">${esc(d.iot.message)}</p></section>
    <section><h4 class="sec-h"><span class="evi none">${icon('info')}Weather context</span></h4>${meteoBlock(d.meteorology)}</section>
    <section><h4 class="sec-h">${evi('field_verified')}</h4>${fieldBlock(d)}</section>
    ${actions(d, st, full)}`;
}

function meteoBlock(m) {
  if (!m?.available) return `<p class="nodata" style="margin:0">${NODATA}</p>`;
  return `<div class="kvs"><div><span>Station record ends</span>${fmt.date(m.record_ends)}</div><div><span>Rain, last 14 recorded days</span>${fmt.n(m.rain_14d_mm, 1)} mm</div>
    <div><span>Mean temperature</span>${m.temp_mean_c == null ? NODATA : `${m.temp_mean_c} °C`}</div></div>
    <p class="hint" style="margin:8px 0 0">Historical record from the Gikomero station. Not a live feed.</p>`;
}

function bindActions(root, d, st) {
  root.querySelector('[data-act="task"]')?.addEventListener('click', async () => {
    const { taskDialog } = await import('./field.js');
    drawer.close();
    taskDialog({ site_id: d.id, priority: 'routine', reason: 'Inspect this location: check for water, dip for larvae and record sampling effort.' });
  });
  root.querySelector('[data-act="tech"]')?.addEventListener('click', async () => {
    const { openSite } = await import('../site.js');
    openSite(d.id, st.date, st.h, st);
  });
}

export async function loadSummary(target, id, st) {
  target.innerHTML = '<div class="skeleton" style="height:90px"></div><div class="skeleton" style="height:180px"></div>';
  try {
    const d = await api.get(`/api/dossier/${encodeURIComponent(id)}`);
    target.innerHTML = summaryHTML(d, st);
    bindActions(target, d, st);
    return d;
  } catch (e) {
    if (!e.cancelled) target.innerHTML = `<div class="note crit">${esc(e.message)}</div>`;
    return null;
  }
}

export function openDossierDrawer(id, st) {
  drawer.open(`<span class="mono">${esc(id)}</span>`, '');
  loadSummary(drawer.body(), id, st);
}

// ------------------------------------------------------------------ full page
export async function render(el, st) {
  const id = params().get('id');
  if (!id) {
    el.innerHTML = `<div class="page-head"><div><h1>Habitat dossier</h1><p>Everything RINDA holds about one location, in date order.</p></div></div>
      ${card('', `<form id="dsFind" class="form" style="max-width:420px"><div class="field"><label for="dsId">Location or habitat code</label><input id="dsId" placeholder="G0221 or H0001" pattern="[GgHh][0-9]{4}" required></div><button class="btn primary">Open dossier</button></form>`)}`;
    el.querySelector('#dsFind').addEventListener('submit', (e) => { e.preventDefault(); location.hash = `#/dossier?id=${el.querySelector('#dsId').value.trim().toUpperCase()}`; });
    return;
  }
  const d = await api.get(`/api/dossier/${encodeURIComponent(id)}`);
  const full = !d.restricted;
  const obs = d.observations || [], ivs = d.interventions || [];
  const h = d.habitat;
  const lastObs = obs.at(-1);
  el.innerHTML = `
  <div class="dossier-head">
    <div><div class="eyebrow">${d.kind === 'habitat' ? 'Field-registered habitat' : 'Monitoring location'}</div>
      <h1 style="margin:0;font-size:28px;letter-spacing:-.02em"><span class="mono">${esc(d.id)}</span></h1>
      <p style="margin:6px 0 10px;color:var(--ink-2)">${esc(adminLine(d.administrative))} · <span class="mono">${fmt.ll(d.lat, d.lon)}</span></p>
      <div style="display:flex;gap:8px;flex-wrap:wrap">${opStatus(d.status, d.status_label)} ${evi(d.evidence_tier)}</div>
      ${d.administrative.note ? `<p class="hint" style="color:var(--warn);margin:8px 0 0">${esc(d.administrative.note)}</p>` : ''}</div>
    ${actions(d, st, true)}
  </div>
  ${steps(d) ? card('', steps(d)) + '<div style="height:18px"></div>' : ''}
  <div class="grid g-main">
    <div class="grid" style="align-content:start">
      ${card('History', d.timeline.length ? `<ol class="tl">${d.timeline.slice(0, 60).map((e) => `<li><span class="d">${fmt.date(e.date)}</span><span class="dot" style="background:${dotColor(e.kind)}"></span><span class="b">${evi(e.kind)}<b style="margin-top:4px">${esc(e.label)}</b><span>${esc(e.detail || '')}</span></span></li>`).join('')}</ol>${d.timeline.length > 60 ? `<p class="hint">Showing the latest 60 of ${d.timeline.length} events.</p>` : ''}` : empty('No events on record', 'Nothing has been observed, predicted or recorded here yet.'), { sub: 'Newest first. Each entry says what kind of evidence it is.' })}
      ${d.satellite ? card('Satellite history', `<div class="chart" id="dsRadar"></div><div class="legend"><span><i class="ln" style="background:${series(2)}"></i>VV backscatter</span><span><i class="ln" style="background:${series(3)}"></i>VH backscatter</span><span><i style="background:var(--info-soft);border:1px solid var(--info)"></i>Water signal</span></div>
        <details class="tech" style="margin-top:12px"><summary>How the water signal is derived</summary><div>A pass counts as a water signal when VV is at or below ${d.satellite.water_thresholds.vv_db_max} dB and VH at or below ${d.satellite.water_thresholds.vh_db_max} dB, on the orbit-harmonised Sentinel-1 series. The thresholds were calibrated against Sentinel-2, not against field observations. Sentinel-1C is not yet harmonised, which inflates recent signals.</div></details>`, { sub: 'Sentinel-1, 50 m sample · satellite-observed' }) : ''}
      ${d.forecasts ? card('Forecast history', `<div class="chart sm" id="dsFc"></div><p class="hint" style="margin:8px 0 0">Research-only. Scores as issued at the time, with later radar signals marked.</p>`, { sub: `${st.h}-day horizon` }) : ''}
      ${full ? card('Entomological history', entoTable(obs, d.samples || []), { flush: true, sub: 'Field inspections and survey samples' }) : ''}
      ${full && d.photos?.length ? card('Photographs', `<div class="photos">${d.photos.map((p) => `<a href="/api/v1/photos/${p.id}" target="_blank" rel="noopener"><img src="/api/v1/photos/${p.id}" alt="Photograph from observation O${p.observation_id}" loading="lazy"></a>`).join('')}</div>`) : ''}
    </div>
    <div class="grid" style="align-content:start">
      ${card('Identification', `<div class="kvs"><div><span>Code</span><span class="mono" style="display:inline;color:var(--ink)">${esc(d.id)}</span></div><div><span>Type</span>${d.kind === 'habitat' ? 'Field-registered habitat' : '50 m radar monitoring location'}</div>
        <div><span>Coordinates</span><span class="mono" style="display:inline;color:var(--ink)">${fmt.ll(d.lat, d.lon)}</span></div><div><span>Cell</span>${esc(d.administrative.cell || 'Not resolved')}</div>
        <div><span>Sector</span>${esc(d.administrative.sector || 'Outside Gikomero')}</div><div><span>District</span>${esc(d.administrative.district)}</div></div>`)}
      ${card('Habitat characteristics', habitatBlock(h, lastObs, d))}
      ${card('Current forecast', forecastBlock(d, st), { sub: 'Predicted' })}
      ${card('Sensor readings', `<p class="nodata" style="margin:0">${esc(d.iot.message)}</p>`, { sub: 'Sensor-observed' })}
      ${card('Weather context', meteoBlock(d.meteorology))}
      ${full ? card('Inspection tasks', listRows((d.tasks || []).map((t) => [`T${t.id} · ${esc(t.assignee || 'unassigned')}`, `${t.status.replace('_', ' ')} · due ${fmt.dshort(t.due_date)}`]), 'No inspection has been assigned here.'), { flush: true }) : ''}
      ${full ? card('Treatment decisions', listRows(ivs.map((i) => [`I${i.id} · ${i.status === 'rejected' ? 'Not authorised' : esc(i.method.replace(/_/g, ' '))}`, i.status === 'rejected' ? esc(i.decision_reason || '') : `${i.status}${i.protocol_ref ? ` · protocol ${esc(i.protocol_ref)}` : ''}${i.outcome ? ` · ${i.outcome.replace(/_/g, ' ')}` : ''}`]), 'No treatment decision recorded.'), { flush: true, sub: `Protocol settings: ${esc(d.protocol.label.toLowerCase())}` }) : ''}
      ${full ? card('Drone missions', listRows((d.missions || []).map((m) => [`<span class="mono">${esc(m.id)}</span>`, `${m.kind} · ${m.status.toLowerCase().replace('_', ' ')}`]), 'No mission has included this location.'), { flush: true }) : ''}
      ${d.audit_history ? card('Audit history', listRows(d.audit_history.slice(0, 15).map((a) => [`${esc(a.action)}`, `${a.ts_utc.slice(0, 16).replace('T', ' ')} · ${esc(a.actor)}`]), 'No audited actions mention this location.'), { flush: true }) : ''}
      ${!full ? card('Field records', fieldBlock(d)) : ''}
    </div>
  </div>
  <div style="margin-top:18px">${d.kind !== 'habitat' ? auditNote(d.model_audit) : ''}</div>`;
  bindActions(el, d, st);

  if (d.satellite) {
    const r = d.satellite.radar;
    const bands = [];
    r.forEach((p, i) => { if (p.wet) bands.push([{ xAxis: p.date }, { xAxis: r[Math.min(i + 1, r.length - 1)].date }]); });
    chart('dsRadar', {
      grid: { left: 44, right: 14, top: 14, bottom: 48 }, legend: { show: false },
      xAxis: { type: 'time' }, yAxis: { type: 'value', name: 'dB', scale: true },
      dataZoom: [{ type: 'inside' }, { type: 'slider', height: 16, bottom: 6, borderColor: 'transparent', backgroundColor: css('--s2'), fillerColor: css('--brand-soft') }],
      series: [
        { name: 'VV', type: 'line', showSymbol: false, data: r.map((p) => [p.date, p.vv_db]), lineStyle: { width: 2, color: series(2) }, itemStyle: { color: series(2) },
          markArea: { silent: true, itemStyle: { color: css('--info-soft') }, data: bands },
          markLine: { silent: true, symbol: 'none', lineStyle: { color: css('--ink-3'), type: 'dashed' }, label: { show: false }, data: [{ yAxis: d.satellite.water_thresholds.vv_db_max }] } },
        { name: 'VH', type: 'line', showSymbol: false, data: r.map((p) => [p.date, p.vh_db]), lineStyle: { width: 2, color: series(3) }, itemStyle: { color: series(3) } },
      ],
    });
  }
  if (d.forecasts) {
    const hist = d.forecasts[st.h] || d.forecasts[String(st.h)] || [];
    chart('dsFc', {
      grid: { left: 40, right: 14, top: 30, bottom: 26 }, legend: { data: ['Prediction score', 'Later radar water signal'] },
      xAxis: { type: 'time' }, yAxis: { type: 'value', max: 1, min: 0 },
      series: [
        { name: 'Prediction score', type: 'line', step: 'middle', showSymbol: false, data: hist.map((f) => [f.date, f.probability]), lineStyle: { width: 2, color: series(5) }, itemStyle: { color: series(5) } },
        { name: 'Later radar water signal', type: 'scatter', symbolSize: 9, data: hist.filter((f) => f.y === 1).map((f) => [f.date, 1]), itemStyle: { color: series(2), borderColor: '#fff', borderWidth: 2 } },
      ],
    });
  }
}

const dotColor = (k) => ({ predicted: css('--signal'), satellite_observed: css('--water'), field_verified: css('--brand'), entomologically_confirmed: css('--terra'),
  treatment_authorised: '#8A5FBF', treatment_completed: css('--good'), follow_up: css('--good') }[k] || css('--ink-3'));

function listRows(rows, none) {
  return rows.length ? `<div class="list">${rows.map(([a, b]) => `<div><span>${a}</span><span class="sub" style="text-align:right">${b}</span></div>`).join('')}</div>` : `<div class="empty sm">${none}</div>`;
}

function habitatBlock(h, o, d) {
  const src = o || {};
  const rows = [['Habitat type', (h?.habitat_type || src.habitat_type || '').replace(/_/g, ' ')], ['Permanence', (h?.permanence || src.habitat_permanence || '').replace(/_/g, ' ')],
    ['Sun exposure', (src.sun_exposure || '').replace(/_/g, ' ')], ['Water depth', src.water_depth_cm != null ? `${src.water_depth_cm} cm` : ''],
    ['Vegetation cover', src.vegetation_cover_pct != null ? `${src.vegetation_cover_pct}%` : ''], ['Nearest house', src.distance_to_house_m != null ? `${src.distance_to_house_m} m` : '']];
  if (d.restricted) return '<p class="hint" style="margin:0">Recorded during field inspection. Visible to field, analyst and supervisor accounts.</p>';
  if (!rows.some((r) => r[1])) return `<p class="nodata" style="margin:0">${NODATA}. Characteristics are recorded during a field inspection.</p>`;
  return `<div class="kvs">${rows.map(([k, v]) => `<div><span>${k}</span>${esc(v) || `<span class="nodata" style="display:inline;font-size:13px">Not recorded</span>`}</div>`).join('')}</div>${h?.description ? `<p style="margin:10px 0 0;color:var(--ink-2)">${esc(h.description)}</p>` : ''}`;
}

function entoTable(obs, samples) {
  const rows = [...obs].reverse();
  if (!rows.length && !samples.length) return empty('No entomological records', 'This location has not been dipped. Nothing here confirms or rules out larvae.');
  const t = table([
    { h: 'Date', f: (o) => fmt.date(o.observed_at.slice(0, 10)) }, { h: 'Record', f: (o) => `<span class="mono">O${o.id}</span>${o.demo ? ' <span class="badge">DEMO</span>' : ''}` },
    { h: 'Water', f: (o) => yes(o.water_present) }, { h: 'Larvae', f: (o) => yes(o.larvae_present) },
    { h: 'Per dip', r: 1, f: (o) => (o.dips && o.larvae_count != null && o.larvae_present === 'yes' ? (o.larvae_count / o.dips).toFixed(2) : '–') },
    { h: 'Method', f: (o) => esc((o.sampling_method || '').replace(/_/g, ' ')) || '–' },
    { h: 'Species', f: (o) => (o.species || '').split(',').filter(Boolean).map((s) => SPECIES[s] || s).join(', ') || '–' },
    { h: 'Stages', f: (o) => (o.stages || '').split(',').filter(Boolean).map((s) => s.replace('_', ' ')).join(', ') || '–' },
    { h: 'Pupae', f: (o) => yes(o.pupae_present) }, { h: 'Collector', f: (o) => esc(o.observer || '') }, { h: 'Review', f: (o) => pill(o.status === 'verified' ? 'Current' : 'Processing').replace(/>[^<]+</, `>${o.status.replace('_', ' ')}<`) },
  ], rows, { max: 380 });
  return t.html;
}
