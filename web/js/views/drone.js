// Drone operations: aircraft, missions and what actually happened on each flight.
// Inspection flights look only. Treatment missions can include only habitats a
// supervisor has authorised for drone application after a verified field finding.
import { api, card, icon, esc, fmt, table, toast, modal, empty, stat, NODATA } from '../ui.js';

let ST;
const MS = { PLANNED: ['st-warn', 'Awaiting approval'], APPROVED: ['st-good', 'Approved'], IN_FLIGHT: ['st-info', 'In flight'], COMPLETED: ['st-good', 'Completed'], CANCELLED: ['st-mute', 'Cancelled or rejected'] };
const tag = (k) => `<span class="status ${MS[k][0]}">${MS[k][1]}</span>`;
const kind = (k) => (k === 'treatment' ? '<span class="evi treatment_authorised">Treatment mission</span>' : '<span class="evi satellite_observed">Inspection flight</span>');

export async function render(el, st) {
  ST = st;
  const [ms, meteo, ivs] = await Promise.all([api.get('/api/missions'), api.get('/api/map/meteo'), st.can('read:field') ? api.get('/api/interventions') : Promise.resolve(null)]);
  const a = st.boot.planner_defaults.aircraft, tr = st.boot.planner_defaults.treatment;
  const ready = (ivs || []).filter((i) => i.status === 'authorized' && i.method === 'bti_drone');
  const flown = ms.filter((m) => m.flight_result);
  const can = st.can('plan:mission');
  const t = table([
    { h: 'Mission', f: (m) => `<span class="mono">${esc(m.id)}</span>${m.demo ? ' <span class="badge">DEMO</span>' : ''}` }, { h: 'Type', f: (m) => kind(m.kind) }, { h: 'Status', f: (m) => tag(m.status) },
    { h: 'Targets', r: 1, f: (m) => m.totals.targets ?? 0 }, { h: 'Sorties', r: 1, f: (m) => m.totals.sorties ?? 0 }, { h: 'Est. distance', r: 1, f: (m) => (m.totals.distance_km != null ? `${fmt.n(m.totals.distance_km, 1)} km` : '–') },
    { h: 'Est. flight time', r: 1, f: (m) => fmt.min(m.totals.flight_min) }, { h: 'Planned by', f: (m) => esc(m.author || '') }, { h: '', f: (m) => `<button class="btn sm" data-open="${esc(m.id)}">Open</button>` },
  ], ms, { empty: 'No missions planned yet.', max: 440 });
  el.innerHTML = `
  <div class="page-head">
    <div><h1>Drone operations</h1><p>Mission planning, approval, flight files and flight records. Mission files carry no spray command.</p></div>
    <div class="actions">${can ? `<a class="btn" href="#/missions">${icon('eye')}Plan inspection flight</a><button class="btn primary" id="newTreat">${icon('send')}Plan treatment mission</button>` : '<span class="hint">Planning needs an analyst or supervisor account.</span>'}</div>
  </div>
  <div class="stats" style="margin-bottom:18px">
    ${stat('Missions awaiting approval', ms.length ? ms.filter((m) => m.status === 'PLANNED').length : null, '', 'clock', 'amber')}
    ${stat('Approved, not yet flown', ms.length ? ms.filter((m) => m.status === 'APPROVED').length : null, '', 'check')}
    ${stat('Flights with recorded results', ms.length ? flown.length : null, '', 'cliplist', 'blue')}
    ${stat('Habitats authorised for drone treatment', ivs ? (ivs.length ? ready.length : null) : null, ivs ? 'Verified, assessed and authorised by a supervisor' : 'Visible to field and supervisor roles', 'shield')}
  </div>
  <div class="grid g-main">
    <div class="grid" style="align-content:start">
      ${card(`Missions · ${ms.length}`, t.html, { flush: true, sub: 'Flight-controller files are released only after approval' })}
      ${card('Flight history', flown.length ? table([{ h: 'Mission', f: (m) => `<span class="mono">${esc(m.id)}</span>` }, { h: 'Flown on', f: (m) => m.flight_result.flown_on }, { h: 'Sorties', r: 1, f: (m) => m.flight_result.sorties_flown },
        { h: 'Targets reached', r: 1, f: (m) => m.flight_result.targets_reached }, { h: 'Water confirmed', r: 1, f: (m) => m.flight_result.water_confirmed ?? '–' }, { h: 'Treated', r: 1, f: (m) => m.flight_result.treated_sites.length },
        { h: 'Product', f: (m) => esc(m.flight_result.product_used || '–') }, { h: 'Recorded by', f: (m) => `${esc(m.flight_result.recorded_by)}${m.flight_result.demo ? ' <span class="badge">DEMO</span>' : ''}` }], flown, { max: 300 }).html
    : empty('No flight results recorded', 'After a mission flies, the crew records what was reached and treated here.'), { flush: flown.length > 0, sub: 'As recorded by the crew', foot: '<a href="#/flight">Onboard timing logs and analytics</a>' })}
    </div>
    <div class="grid" style="align-content:start">
      ${card('Aircraft', `<div class="note warn" style="margin-bottom:12px">${icon('info')} <b>Planning estimates.</b> One aircraft profile is configured, with assumed values. Confirm them against the 10-litre spray drone before any operational use.</div>
        <div class="kvs"><div><span>Tank capacity</span>${a.tank_l} L</div><div><span>Endurance, loaded</span>${a.endurance_min} min</div><div><span>Work speed</span>${a.work_speed_ms} m/s</div><div><span>Transit speed</span>${a.cruise_speed_ms} m/s</div>
          <div><span>Swath</span>${a.swath_m} m</div><div><span>Work height</span>${a.altitude_m} m</div><div><span>Carrier volume</span>${tr.spray_volume_l_ha} L/ha</div><div><span>Area per target</span>${tr.patch_area_m2} m²</div></div>`, { sub: 'Assumed profile', foot: '<a href="#/missions">Edit in the planner</a>' })}
      ${card('Weather', meteo.available ? `<div class="kvs"><div><span>Latest station reading</span>${fmt.date(meteo.observed_date)}</div><div><span>Rain that day</span>${fmt.n(meteo.rainfall_mm, 1)} mm</div><div><span>Mean temperature</span>${meteo.temp_mean_c ?? NODATA} °C</div><div><span>Wind</span>${NODATA}</div></div>
        <p class="hint" style="margin:10px 0 0">Historical record ending ${fmt.date(meteo.record_ends)}; not current conditions. ${esc(meteo.wind_note)} Check wind and rain on site before flying.</p>` : `<p class="nodata" style="margin:0">${NODATA}</p>`, { sub: 'Not a live feed' })}
    </div>
  </div>`;
  el.querySelectorAll('[data-open]').forEach((b) => b.addEventListener('click', () => open(b.dataset.open, el)));
  el.querySelector('#newTreat')?.addEventListener('click', () => treat(ready, el));
}

async function treat(ready, el) {
  if (!ST.can('read:field')) { toast('Treatment missions need a supervisor account, because they draw on field records.', true); return; }
  const d = modal('Plan treatment mission', ready.length ? `
    <div class="note">${icon('shield')} Only habitats with a supervisor-authorised drone intervention can be selected. Each rests on a verified field finding and a recorded suitability assessment.</div>
    <form id="tmF" class="form"><div class="field"><label>Approved habitats</label><div class="checks" style="display:grid">${ready.map((i) => `<label class="check"><input type="checkbox" name="s" value="${esc(i.site_id)}" checked> <span class="mono">${esc(i.site_id)}</span> · intervention I${i.id} · protocol ${esc(i.protocol_ref)}</label>`).join('')}</div></div>
      <div class="actions"><button type="button" class="btn" id="tmPrev">${icon('route')}Preview route</button><button class="btn primary">${icon('check')}Save and request approval</button></div><div id="tmOut"></div></form>`
    : empty('No habitat is authorised for drone treatment', 'A supervisor authorises treatment under Field operations, after a verified field finding with larvae and a suitability assessment. Forecasts cannot be selected.', 'lock'), { wide: true });
  const sel = () => [...d.querySelectorAll('[name="s"]:checked')].map((x) => x.value);
  const plan = async (save) => {
    if (!sel().length) { toast('Select at least one habitat.', true); return null; }
    try { return await api.post('/api/missions/plan', { kind: 'treatment', site_ids: sel(), h: ST.h, date: ST.date, save }); } catch (e) { toast(e.message, true); return null; }
  };
  d.querySelector('#tmPrev')?.addEventListener('click', async () => {
    const r = await plan(false); if (!r) return;
    d.querySelector('#tmOut').innerHTML = `<div class="kvs" style="margin-top:12px"><div><span>Sorties</span>${r.totals.sorties}</div><div><span>Est. distance</span>${fmt.n(r.totals.distance_km, 1)} km</div><div><span>Est. flight time</span>${fmt.min(r.totals.flight_min)}</div>
      <div><span>Est. spray volume</span>${fmt.n(r.totals.spray_l, 1)} L</div><div><span>Batteries</span>${r.totals.batteries}</div><div><span>Sorties over endurance</span>${r.totals.infeasible_sorties}</div></div><p class="hint">${esc(r.estimates_note)}</p>`;
  });
  d.querySelector('#tmF')?.addEventListener('submit', async (e) => { e.preventDefault(); const r = await plan(true); if (r) { toast(`Mission ${r.id} saved. It now waits for supervisor approval.`); d.close(); render(el, ST); } });
}

async function open(id, el) {
  const m = await api.get(`/api/missions/${encodeURIComponent(id)}`);
  const res = m.result, tt = res.totals, k = m.kind || 'inspection';
  const appr = ST.can('approve:mission'), plan = ST.can('plan:mission');
  const stops = res.sorties.flatMap((s) => s.waypoints.map((w) => ({ ...w, sortie: s.id })));
  const fr = m.flight_result ? JSON.parse(m.flight_result) : null;
  const d = modal(`Mission ${esc(m.id)}`, `
    <div style="display:flex;gap:8px;flex-wrap:wrap">${kind(k)} ${tag(m.status)}</div>
    <div class="note ${k === 'treatment' ? 'warn' : ''}">${esc(res.authorization_note || (k === 'treatment' ? 'Treatment mission.' : 'Inspection flight: no treatment is planned.'))}</div>
    <div class="kvs"><div><span>Targets</span>${tt.targets}</div><div><span>Sorties</span>${tt.sorties}</div><div><span>Est. distance</span>${fmt.n(tt.distance_km, 1)} km</div><div><span>Est. flight time</span>${fmt.min(tt.flight_min)}</div>
      <div><span>Est. field day</span>${fmt.min(tt.field_day_min)}</div><div><span>Est. spray volume</span>${k === 'treatment' ? `${fmt.n(tt.spray_l, 1)} L` : 'None: inspection only'}</div><div><span>Batteries</span>${tt.batteries}</div><div><span>Sorties over endurance</span>${tt.infeasible_sorties}</div></div>
    <p class="hint" style="margin:0">Planning estimates from the assumed aircraft profile (${m.params.aircraft.tank_l} L tank, ${m.params.aircraft.endurance_min} min endurance).${m.decision_note ? ` Decision note: ${esc(m.decision_note)}` : ''}</p>
    <details class="tech"><summary>Targets (${stops.length})</summary><div>${stops.map((w) => `<span class="badge" title="${w.sortie} stop ${w.seq}">${esc(w.site_id)}</span>`).join(' ')}</div></details>
    ${fr ? `<div class="note good"><b>Flight result recorded.</b> Flown ${fr.flown_on}: ${fr.sorties_flown} sorties, ${fr.targets_reached} targets reached, ${fr.treated_sites.length} treated. ${esc(fr.notes || '')}</div>` : ''}
    <div class="actions">
      ${m.status === 'PLANNED' && appr ? `<button class="btn primary" data-s="APPROVED">${icon('check')}Approve</button><button class="btn" id="rej">Reject</button>` : ''}
      ${m.status === 'PLANNED' && !appr ? '<span class="hint">Waiting for a supervisor to approve or reject.</span>' : ''}
      ${m.status === 'APPROVED' && plan ? `<button class="btn primary" data-s="IN_FLIGHT">${icon('play')}Mark in flight</button><button class="btn ghost" data-s="CANCELLED">Cancel</button>` : ''}
      ${m.status === 'IN_FLIGHT' && plan ? `<button class="btn primary" data-s="COMPLETED">${icon('check')}Mark completed</button>` : ''}
      ${['IN_FLIGHT', 'COMPLETED'].includes(m.status) && plan ? `<button class="btn" id="rec">${icon('cliplist')}${fr ? 'Update' : 'Record'} flight result</button>` : ''}
    </div>
    ${plan ? `<div class="actions">${['APPROVED', 'IN_FLIGHT', 'COMPLETED'].includes(m.status) ? `<a class="btn sm" href="/api/v1/missions/${m.id}/export?fmt=plan">${icon('download')}QGroundControl .plan</a><a class="btn sm" href="/api/v1/missions/${m.id}/export?fmt=wpl">${icon('download')}MAVLink .waypoints</a>` : '<span class="hint">Flight-controller files unlock after approval.</span>'}
      <a class="btn sm" href="/api/v1/missions/${m.id}/export?fmt=geojson">${icon('download')}GeoJSON</a><a class="btn sm" href="/api/v1/missions/${m.id}/export?fmt=csv">${icon('download')}CSV</a></div>` : ''}`, { wide: true });
  const setS = async (s, extra = {}) => { try { await api.post(`/api/missions/${encodeURIComponent(id)}/status`, { status: s, ...extra }); toast(`Mission ${MS[s][1].toLowerCase()}`); d.close(); render(el, ST); } catch (e) { toast(e.message, true); } };
  d.querySelectorAll('[data-s]').forEach((b) => b.addEventListener('click', () => setS(b.dataset.s)));
  d.querySelector('#rej')?.addEventListener('click', () => {
    const r = modal('Reject mission', `<form id="rjF" class="form"><div class="field"><label for="rjN">Reason</label><textarea id="rjN" required minlength="3" maxlength="500"></textarea></div><button class="btn primary">Reject mission</button></form>`);
    r.querySelector('#rjF').addEventListener('submit', (e) => { e.preventDefault(); const note = r.querySelector('#rjN').value; r.close(); setS('CANCELLED', { reject: true, note }); });
  });
  d.querySelector('#rec')?.addEventListener('click', () => {
    const r = modal('Record flight result', `<form id="frF" class="form"><div class="form cols-2">
      <div class="field"><label for="frD">Flown on</label><input type="date" id="frD" required value="${fr?.flown_on || new Date().toISOString().slice(0, 10)}"></div><div class="field"><label for="frS">Sorties flown</label><input type="number" id="frS" min="0" required value="${fr?.sorties_flown ?? tt.sorties}"></div>
      <div class="field"><label for="frT">Targets reached</label><input type="number" id="frT" min="0" required value="${fr?.targets_reached ?? ''}"></div><div class="field"><label for="frW">Targets where water was seen <span class="u">optional</span></label><input type="number" id="frW" min="0" value="${fr?.water_confirmed ?? ''}"></div></div>
      ${k === 'treatment' ? `<div class="field"><label>Habitats actually treated</label><div class="checks">${stops.map((w) => `<label class="check"><input type="checkbox" name="tr" value="${esc(w.site_id)}" ${fr?.treated_sites?.includes(w.site_id) ? 'checked' : ''}><span class="mono">${esc(w.site_id)}</span></label>`).join('')}</div></div>
      <div class="field"><label for="frP">Product and quantity used</label><input id="frP" maxlength="200" value="${esc(fr?.product_used || '')}" placeholder="e.g. Bti WDG, 0.4 kg in 8 L"></div>` : '<p class="hint" style="margin:0">Inspection flight: treatment cannot be recorded.</p>'}
      <div class="field"><label for="frN">Notes</label><textarea id="frN" maxlength="2000">${esc(fr?.notes || '')}</textarea></div><button class="btn primary">Save result</button>
      ${k === 'treatment' ? '<p class="hint" style="margin:0">Also record each treatment against its intervention under Field operations, so follow-up inspections can be scheduled.</p>' : ''}</form>`);
    r.querySelector('#frF').addEventListener('submit', async (e) => {
      e.preventDefault();
      const num = (i) => (r.querySelector(i).value === '' ? null : +r.querySelector(i).value);
      try {
        await api.post(`/api/missions/${encodeURIComponent(id)}/result`, { flown_on: r.querySelector('#frD').value, sorties_flown: num('#frS'), targets_reached: num('#frT'), water_confirmed: num('#frW'),
          treated_sites: [...r.querySelectorAll('[name="tr"]:checked')].map((x) => x.value), product_used: r.querySelector('#frP')?.value || '', notes: r.querySelector('#frN').value });
        toast('Flight result saved'); r.close(); d.close(); render(el, ST);
      } catch (er) { toast(er.message, true); }
    });
  });
}
