import { api, card, kpi, fmt, icon, table, makeMap, legendControl, chart, css, series, statusTag, toast, esc, decisionTag } from '../ui.js';
import { params } from '../app.js';

let map, routeLayer;
export function destroy() { map?.remove(); map = null; }

const AIR_FIELDS = [
  ['cruise_speed_ms', 'Transit speed', 'm/s'], ['work_speed_ms', 'Work speed over target', 'm/s'], ['altitude_m', 'Work height AGL', 'm'],
  ['swath_m', 'Effective swath', 'm'], ['endurance_min', 'Loaded endurance (net of reserve)', 'min'], ['tank_l', 'Tank volume', 'L'],
  ['verify_s', 'Hover-and-confirm per target', 's'], ['takeoff_land_s', 'Take-off + landing', 's'], ['battery_wh', 'Battery capacity', 'Wh'],
  ['p_flight_w', 'Flight power', 'W'], ['p_compute_w', 'Companion computer power', 'W'], ['p_spray_w', 'Pump power', 'W'],
];
const TRT_FIELDS = [['patch_area_m2', 'Treated area per target', 'm²'], ['spray_volume_l_ha', 'Carrier volume', 'L/ha'], ['bti_kg_ha', 'Bti product rate', 'kg/ha']];

export async function render(el, st) {
  const d = st.boot.planner_defaults;
  const cfg = { aircraft: { ...d.aircraft }, treatment: { ...d.treatment }, policy: { max_targets: 40, include_uncertain: true, min_uncertain_probability: 0.05 }, launch: { mode: 'mobile', zone_radius_m: 900 } };
  const focus = params().get('add');

  el.innerHTML = `
  <div class="page-head">
    <div><h1>Mission planning</h1><p>Turns the ${st.h}-day forecast for ${fmt.date(st.date)} into flyable sorties. Formation calls are flown as <b>verify-and-treat</b>; verify-first sites are flown <b>inspect-only</b> with the spray channel locked unless the onboard verifier clears them.</p></div>
    <div class="actions"><button class="btn" id="mpPreview">${icon('route')}Recompute</button><button class="btn primary" id="mpCommit">${icon('check')}Commit mission</button></div>
  </div>
  <div class="grid g-4" id="mpKpis"></div>
  <div class="grid g-main" style="margin-top:14px">
    ${card('Sortie routes', '<div class="map-host"><div class="map" id="mpMap" style="height:600px"></div></div>', { flush: true, sub: 'Diamonds: launch points · solid: verify-and-treat · ringed: inspect-only' })}
    <div class="grid" style="align-content:start">
      ${card('Targeting policy', `
        <div class="form">
          <div class="field"><label>Maximum targets per day <span class="u" id="mtV">40</span></label><input type="range" min="5" max="120" step="5" value="40" id="mt"></div>
          <label class="check"><input type="checkbox" id="incU" checked> Inspect verify-first sites (spray locked)</label>
          <div class="field"><label>Inspect verify-first sites above <span class="u" id="muV">0.05</span></label><input type="range" min="0" max="0.3" step="0.01" value="0.05" id="mu"></div>
          <div class="field"><label>Launch strategy</label><select id="lm"><option value="mobile">Mobile crew, launch per zone</option><option value="fixed">Single fixed base (weather station)</option></select></div>
          <div class="field"><label>Launch zone radius <span class="u" id="zrV">900 m</span></label><input type="range" min="300" max="2500" step="100" value="900" id="zr"></div>
        </div>`)}
      <details class="card"><summary class="card-h" style="cursor:pointer;padding-bottom:12px"><h3>Aircraft &amp; treatment profile</h3><span class="sub">edit</span></summary>
        <div class="card-b"><div class="assume">${icon('info')}Design values. Replace with the confirmed airframe specification before operational use.</div>
        <div class="form cols-2" style="margin-top:10px">${AIR_FIELDS.map(([k, l, u]) => `<div class="field"><label for="a_${k}">${l}<span class="u">${u}</span></label><input id="a_${k}" type="number" step="any" value="${d.aircraft[k]}"></div>`).join('')}
        ${TRT_FIELDS.map(([k, l, u]) => `<div class="field"><label for="t_${k}">${l}<span class="u">${u}</span></label><input id="t_${k}" type="number" step="any" value="${d.treatment[k]}"></div>`).join('')}</div></div></details>
      ${card('Coverage comparison', '<div class="chart sm" id="mpCmp"></div><div id="mpCmpNote" style="font-size:12px;color:var(--ink-2)"></div>', { sub: 'Same aircraft, same swath' })}
    </div>
  </div>
  <div style="margin-top:14px" id="mpSorties"></div>
  <div style="margin-top:14px" id="mpRegister"></div>`;

  map = makeMap('mpMap', { zoom: 13 });
  legendControl(map, `<b>Route legend</b><div class="row"><span class="dot" style="background:var(--signal)"></span>Verify and treat</div><div class="row"><span class="dot" style="background:none;border:2px solid var(--warn)"></span>Inspect only</div><div class="row"><span class="launch-pin" style="width:9px;height:9px;margin:0 3px"></span>Launch point</div><div class="row"><span class="station-pin" style="width:10px;height:10px;margin:0 2px"></span>Base / weather station</div>`);

  const read = () => {
    AIR_FIELDS.forEach(([k]) => { cfg.aircraft[k] = +document.getElementById('a_' + k).value; });
    TRT_FIELDS.forEach(([k]) => { cfg.treatment[k] = +document.getElementById('t_' + k).value; });
    cfg.policy.max_targets = +document.getElementById('mt').value;
    cfg.policy.include_uncertain = document.getElementById('incU').checked;
    cfg.policy.min_uncertain_probability = +document.getElementById('mu').value;
    cfg.launch.mode = document.getElementById('lm').value;
    cfg.launch.zone_radius_m = +document.getElementById('zr').value;
  };
  let last;
  const run = async (save = false) => {
    read();
    try {
      last = await api.post('/api/missions/plan', { h: st.h, date: st.date, ...cfg, save, author: document.getElementById('roleSel').selectedOptions[0].text });
    } catch (e) { toast('Planning failed: ' + e.message, true); return; }
    draw(last, st, cfg);
    if (save) { toast(`Mission ${last.id} committed to the register`); register(st); }
  };
  let tmr;
  const soon = () => { clearTimeout(tmr); tmr = setTimeout(() => run(false), 250); };
  [['mt', 'mtV', (v) => v], ['mu', 'muV', (v) => (+v).toFixed(2)], ['zr', 'zrV', (v) => `${v} m`]].forEach(([i, o, f]) => document.getElementById(i).addEventListener('input', (e) => { document.getElementById(o).textContent = f(e.target.value); soon(); }));
  el.querySelectorAll('#incU, #lm, input[id^="a_"], input[id^="t_"]').forEach((x) => x.addEventListener('change', soon));
  document.getElementById('mpPreview').addEventListener('click', () => run(false));
  document.getElementById('mpCommit').addEventListener('click', () => run(true));
  await run(false);
  if (focus) toast(`Showing the plan for ${st.date}; site ${focus} is ${last.sorties.some((s) => s.waypoints.some((w) => w.site_id === focus)) ? 'routed' : 'not among the selected targets'}.`);
  register(st);
}

function draw(res, st, cfg) {
  const t = res.totals;
  const lm = res.baseline.wet_prone_zone;
  const saving = t.flight_min ? 1 - t.flight_min / lm.flight_min : 0;
  document.getElementById('mpKpis').innerHTML = [
    kpi('Targets routed', fmt.n(t.targets || 0), `${t.treat || 0} verify-and-treat · ${t.verify_only || 0} inspect-only`, { accent: true }),
    kpi('Sorties', fmt.n(t.sorties || 0), `${t.zones || 0} launch zones · ${t.batteries || 0} battery cycles${t.infeasible_sorties ? ` · <span style="color:var(--crit)">${t.infeasible_sorties} over endurance</span>` : ''}`),
    kpi('Flight time', fmt.min(t.flight_min || 0), `Field day ${fmt.min(t.field_day_min || 0)} incl. ${fmt.n(t.drive_km || 0, 1)} km driving`),
    kpi('Flight time avoided', fmt.pct(saving, 1), `vs ${fmt.min(lm.flight_min)} to cover the ${fmt.n(lm.area_ha, 0)} ha wet-prone zone`, { tip: 'Lawnmower coverage of every site with any recorded water, at the same swath and speed' }),
  ].join('');

  routeLayer?.remove();
  routeLayer = L.layerGroup().addTo(map);
  const b = st.boot.station;
  L.marker([b.lat, b.lon], { icon: L.divIcon({ className: '', html: '<div class="station-pin"></div>', iconSize: [16, 16] }) }).bindTooltip('Base · Gikomero AWS').addTo(routeLayer);
  const brand = css('--brand'), signal = css('--signal'), warn = css('--warn'), ink = css('--ink');
  const bounds = [[b.lat, b.lon]];
  const seenLaunch = new Set();
  for (const s of res.sorties) {
    const lp = s.launch;
    const line = L.polyline([lp, ...s.waypoints.map((w) => [w.lat, w.lon]), lp], { color: brand, weight: 2.2, opacity: .9 }).bindTooltip(`<b>${s.id}</b> · ${s.n_targets} targets · ${fmt.n(s.distance_m / 1000, 2)} km · ${fmt.n(s.flight_s / 60, 1)} min`, { sticky: true });
    line.on('mouseover', () => line.setStyle({ weight: 4.5, color: ink })).on('mouseout', () => line.setStyle({ weight: 2.2, color: brand }));
    line.addTo(routeLayer);
    const key = lp.join();
    if (!seenLaunch.has(key)) {
      seenLaunch.add(key);
      L.marker(lp, { icon: L.divIcon({ className: '', html: '<div class="launch-pin"></div>', iconSize: [14, 14] }) }).bindTooltip(`Launch ${s.zone}`).addTo(routeLayer);
    }
    s.waypoints.forEach((w) => {
      const treat = w.action === 'VERIFY_AND_TREAT';
      L.circleMarker([w.lat, w.lon], { radius: treat ? 6 : 5, color: treat ? css('--s1') : warn, weight: treat ? 1.5 : 2, fillColor: treat ? signal : css('--s1'), fillOpacity: 1 })
        .bindTooltip(`<b class="mono">${w.site_id}</b> · ${s.id} stop ${w.seq}<br>${treat ? 'Verify and treat' : 'Inspect only (spray locked)'}<br>p = ${w.probability.toFixed(3)}`).on('click', () => window.openSite(w.site_id)).addTo(routeLayer);
      bounds.push([w.lat, w.lon]);
    });
  }
  if (cfg.launch.mode === 'mobile' && res.zones?.length) {
    L.polyline([[b.lat, b.lon], ...res.zones.map((z) => z.launch), [b.lat, b.lon]], { color: css('--ink-3'), weight: 1.4, dashArray: '2 6' }).bindTooltip('Crew vehicle route (straight-line × road factor)').addTo(routeLayer);
  }
  if (bounds.length > 1) map.fitBounds(bounds, { padding: [30, 30] });

  chart('mpCmp', {
    grid: { left: 120, right: 30, top: 6, bottom: 24 }, tooltip: { trigger: 'item', valueFormatter: (v) => fmt.min(v) },
    xAxis: { type: 'log', name: 'flight minutes (log)', nameLocation: 'middle', nameGap: 22, min: 10 },
    yAxis: { type: 'category', data: ['Sector, full cover', 'Wet-prone zone', 'Forecast-guided'] },
    series: [{ type: 'bar', barWidth: 14, label: { show: true, position: 'right', color: css('--ink-2'), fontSize: 11, formatter: (p) => fmt.min(p.value) },
      data: [res.baseline.sector.flight_min, lm.flight_min, t.flight_min || 0].map((v, i) => ({ value: v, itemStyle: { color: i === 2 ? css('--brand') : css('--ink-3'), borderRadius: [0, 4, 4, 0] } })) }],
  });
  const tm = res.timing;
  document.getElementById('mpCmpNote').innerHTML = `Energy for the plan ${fmt.n(t.energy_wh / 1000 || 0, 2)} kWh vs ${fmt.n(lm.energy_wh / 1000, 1)} kWh. Onboard loop budget at ${res.timing && cfg.aircraft.work_speed_ms} m/s: ${fmt.n(tm.latency_ms)} ms against a ${fmt.n(tm.deadline_ms)} ms deadline ${tm.meets_deadline ? '<span class="status st-good">met</span>' : '<span class="status st-crit">missed</span>'}.`;

  const rows = res.sorties;
  const tb = table([
    { h: 'Sortie', f: (s) => `<span class="mono">${s.id}</span>` }, { h: 'Zone', f: (s) => `<span class="mono">${s.zone}</span>` },
    { h: 'Launch', f: (s) => `<span class="mono">${fmt.ll(s.launch[0], s.launch[1])}</span>` },
    { h: 'Treat', r: 1, f: (s) => s.n_treat }, { h: 'Inspect', r: 1, f: (s) => s.n_verify_only },
    { h: 'Route km', r: 1, f: (s) => fmt.n(s.distance_m / 1000, 2) }, { h: 'Flight min', r: 1, f: (s) => fmt.n(s.flight_s / 60, 1) },
    { h: 'Endurance used', f: (s) => `<div class="bar-cell"><div class="gauge" style="width:90px"><span style="width:${Math.min(100, s.endurance_used_pct)}%;background:${s.endurance_used_pct > 95 ? 'var(--crit)' : s.endurance_used_pct > 80 ? 'var(--warn)' : 'var(--brand)'}"></span></div><span class="num">${fmt.n(s.endurance_used_pct)}%</span></div>` },
    { h: 'Spray L', r: 1, f: (s) => fmt.n(s.spray_l, 1) }, { h: 'Energy Wh', r: 1, f: (s) => fmt.n(s.energy_wh) },
    { h: 'Stops', f: (s) => s.waypoints.map((w) => `<span class="mono" style="color:${w.action === 'VERIFY_AND_TREAT' ? 'var(--ink)' : 'var(--warn)'}">${w.site_id}</span>`).join(' → ') },
  ], rows, { max: 420 });
  document.getElementById('mpSorties').innerHTML = card(`Sortie schedule · ${rows.length}`, tb.html, { flush: true, sub: `Issue ${res.issue_date} · formation expected by ${res.window.formation_expected_by}`, foot: '<span>Commit the mission to export QGroundControl .plan and MAVLink .waypoints files per sortie.</span>' });
}

async function register(st) {
  const ms = await api.get('/api/missions');
  const next = { PLANNED: 'APPROVED', APPROVED: 'IN_FLIGHT', IN_FLIGHT: 'COMPLETED' };
  const label = { APPROVED: 'Approve', IN_FLIGHT: 'Mark airborne', COMPLETED: 'Mark complete' };
  const t = table([
    { h: 'Mission', f: (m) => `<span class="mono">${m.id}</span>` },
    { h: 'Issue', f: (m) => `${m.issue_date} · ${m.horizon}d` }, { h: 'Status', f: (m) => statusTag(m.status) },
    { h: 'Sorties', r: 1, f: (m) => m.totals.sorties ?? 0 }, { h: 'Targets', r: 1, f: (m) => m.totals.targets ?? 0 },
    { h: 'Flight', r: 1, f: (m) => fmt.min(m.totals.flight_min) }, { h: 'Committed by', f: (m) => esc(m.author) },
    { h: 'Created (UTC)', f: (m) => `<span class="mono">${m.created_utc.replace('T', ' ').replace('Z', '')}</span>` },
    { h: 'Export', f: (m) => `<a class="btn sm" href="/api/missions/${m.id}/export?fmt=plan">.plan</a> <a class="btn sm" href="/api/missions/${m.id}/export?fmt=wpl">.waypoints</a> <a class="btn sm" href="/api/missions/${m.id}/export?fmt=geojson">GeoJSON</a> <a class="btn sm" href="/api/missions/${m.id}/export?fmt=csv">CSV</a>` },
    { h: '', f: (m) => (next[m.status] ? `<button class="btn sm primary" data-adv="${m.id}" data-to="${next[m.status]}">${label[next[m.status]]}</button>` : '') },
  ], ms, { max: 360, empty: 'No missions committed yet. Plan a day and press Commit mission.' });
  document.getElementById('mpRegister').innerHTML = card(`Mission register · ${ms.length}`, t.html, { flush: true, sub: 'Every state change is written to the audit trail', foot: '<span>.plan exports carry one sortie each (first sortie by default; add &amp;sortie=S02 for others). Spray commands are never in the file: the companion computer owns the valve.</span>' });
  document.querySelectorAll('[data-adv]').forEach((x) => x.addEventListener('click', async () => {
    await api.post(`/api/missions/${x.dataset.adv}/status`, { status: x.dataset.to, actor: document.getElementById('roleSel').selectedOptions[0].text });
    toast(`${x.dataset.adv} → ${x.dataset.to.replace('_', ' ').toLowerCase()}`);
    register(st);
  }));
}
