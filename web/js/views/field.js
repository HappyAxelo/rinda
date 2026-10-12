// Field surveillance: alert → inspection → observation → review → authorised
// intervention → follow-up. Every action here is checked again on the server.
import { api, card, icon, table, toast, esc, modal, catTag, uuid } from '../ui.js';
import { enqueue, pending, discard, flushQueue } from '../offline.js';
import { params } from '../app.js';

const HABITATS = { rice_paddy: 'Rice paddy', marsh_swamp: 'Marsh / swamp', pond: 'Pond', puddle: 'Puddle', ditch_drain: 'Ditch / drain', borrow_pit: 'Borrow pit', stream_edge: 'Stream edge', container: 'Container', other: 'Other' };
const SPECIES = { an_gambiae_sl: 'An. gambiae s.l.', an_arabiensis: 'An. arabiensis', an_funestus: 'An. funestus', an_other: 'Other Anopheles', culicine: 'Culicine', not_identified: 'Not identified' };
const METHODS = { bti_ground: 'Bti, ground application', bti_drone: 'Bti, drone application', environmental_management: 'Environmental management (drain / fill)', other: 'Other (specify in protocol)' };
const OBS_STATUS = { submitted: ['st-info', 'Awaiting review'], needs_review: ['st-warn', 'Needs review'], verified: ['st-good', 'Verified'], rejected: ['st-mute', 'Rejected'] };
const TASK_STATUS = { assigned: ['st-info', 'Assigned'], in_progress: ['st-warn', 'In progress'], completed: ['st-good', 'Completed'], cancelled: ['st-mute', 'Cancelled'] };
const IV_STATUS = { authorized: ['st-warn', 'Authorised'], performed: ['st-info', 'Performed · follow-up due'], closed: ['st-good', 'Closed'] };
const ALERT_STATUS = { open: ['st-crit', 'Open'], actioned: ['st-good', 'Inspection assigned'], acknowledged: ['st-info', 'Acknowledged'], dismissed: ['st-mute', 'Dismissed'], expired: ['st-mute', 'Expired'] };
const tag = (m, k) => `<span class="status ${(m[k] || ['st-mute'])[0]}">${(m[k] || [0, k])[1]}</span>`;
const demo = (r) => (r.demo ? ' <span class="badge" title="Demonstration record">DEMO</span>' : '');

let ST;
export async function render(el, st) {
  ST = st;
  const tabs = [['tasks', 'Inspections'], ['record', 'Record observation'], ['review', 'Review queue', 'review:observation'],
    ['interventions', 'Interventions'], ['alerts', 'Alerts']].filter((t) => !t[2] || st.can(t[2]));
  const want = params().get('tab') || (st.user.role === 'field_officer' ? 'tasks' : 'alerts');
  el.innerHTML = `
  <div class="page-head">
    <div><h1>Field surveillance</h1><p>From forecast to verified evidence. Supervisors assign inspections; field officers record what they find, including when nothing was sampled; a second person reviews each record; only a verified finding can support an authorised intervention.</p></div>
    <div class="actions">${st.can('export:field') ? ['tasks', 'observations', 'interventions'].map((k) => `<a class="btn" href="/api/v1/field/export/${k}.csv">${icon('download')}${k[0].toUpperCase() + k.slice(1)} CSV</a>`).join('') : ''}</div>
  </div>
  <div class="tabs" role="tablist">${tabs.map(([k, l]) => `<button role="tab" data-tab="${k}" aria-selected="${k === want}">${l}</button>`).join('')}</div>
  <div id="fieldBody"></div>`;
  const show = (k) => {
    el.querySelectorAll('[data-tab]').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.tab === k)));
    ({ tasks, record, review, interventions, alerts })[k](document.getElementById('fieldBody'));
  };
  el.querySelectorAll('[data-tab]').forEach((b) => b.addEventListener('click', () => show(b.dataset.tab)));
  show(tabs.some((t) => t[0] === want) ? want : tabs[0][0]);
}

const fail = (e) => toast(e.message, true);

// ------------------------------------------------------------------- tasks
async function tasks(box) {
  const ts = await api.get('/api/tasks');
  const can = ST.can('assign:task');
  const t = table([
    { h: 'Task', f: (r) => `<span class="mono">T${r.id}</span>${demo(r)}` },
    { h: 'Point', f: (r) => `<a href="#" data-site="${r.site_id}" class="mono">${r.site_id}</a>` },
    { h: 'Priority', f: (r) => (['high', 'insufficient', 'moderate', 'lower'].includes(r.priority) ? catTag(r.priority) : r.priority) },
    { h: 'From forecast', f: (r) => (r.issue_date ? `${r.issue_date} · ${r.horizon}d` : 'Routine') },
    { h: 'Assigned to', f: (r) => esc(r.assignee || '') },
    { h: 'Due', f: (r) => `<span style="${r.overdue ? 'color:var(--crit);font-weight:600' : ''}">${r.due_date}${r.overdue ? ' · overdue' : ''}</span>` },
    { h: 'Status', f: (r) => tag(TASK_STATUS, r.status) },
    { h: '', f: (r) => actions(r) },
  ], ts, { max: 520, empty: can ? 'No inspections yet. Use "Assign inspection" or create them from open alerts.' : 'No inspections assigned to you.' });
  box.innerHTML = card(`Inspections · ${ts.length}`, t.html, { flush: true, tools: can ? `<button class="btn sm primary" id="newTask">${icon('clipboard')}Assign inspection</button>` : '' });
  box.querySelector('#newTask')?.addEventListener('click', () => taskDialog({}));
  box.querySelectorAll('[data-site]').forEach((a) => a.addEventListener('click', (e) => { e.preventDefault(); window.openSite(a.dataset.site); }));
  box.querySelectorAll('[data-ts]').forEach((b) => b.addEventListener('click', async () => {
    try { await api.post(`/api/tasks/${b.dataset.id}/status`, { status: b.dataset.ts }); toast(`Task T${b.dataset.id} updated`); tasks(box); } catch (e) { fail(e); }
  }));
  box.querySelectorAll('[data-obs]').forEach((b) => b.addEventListener('click', () => {
    const r = ts.find((x) => String(x.id) === b.dataset.obs);
    document.querySelector('[data-tab="record"]').click();
    setTimeout(() => prefill(r), 50);
  }));
}
function actions(r) {
  if (['completed', 'cancelled'].includes(r.status)) return '';
  const mine = r.assignee_id === ST.user.id;
  const out = [];
  if (mine && r.status === 'assigned') out.push(`<button class="btn sm" data-ts="in_progress" data-id="${r.id}">Start</button>`);
  if (mine || ST.can('write:observation')) out.push(`<button class="btn sm primary" data-obs="${r.id}">Record finding</button>`);
  if (ST.can('assign:task')) out.push(`<button class="btn sm ghost" data-ts="cancelled" data-id="${r.id}">Cancel</button>`);
  return out.join(' ');
}

async function taskDialog({ site_id = '', alert_id = null, priority = 'routine', issue_date = null, horizon = null, due = null, reason = '' }) {
  const people = await api.get('/api/field/assignees').catch(fail);
  if (!people) return;
  const sites = ST.boot.sites.map((s) => s.site_id);
  const d = modal('Assign inspection', `
    <form id="tf" class="form cols-2">
      <div class="field"><label for="tfS">Monitoring point</label><input id="tfS" list="tfSites" value="${esc(site_id)}" required pattern="G\\d{4}"><datalist id="tfSites">${sites.map((s) => `<option value="${s}">`).join('')}</datalist></div>
      <div class="field"><label for="tfA">Assign to</label><select id="tfA" required>${people.map((p) => `<option value="${p.id}">${esc(p.name)} · ${p.role.replace('_', ' ')}</option>`).join('')}</select>${people.length ? '' : '<span class="hint" style="color:var(--crit)">No active field officers. An administrator must create accounts first.</span>'}</div>
      <div class="field"><label for="tfD">Due date</label><input id="tfD" type="date" required value="${due || new Date(Date.now() + 3 * 864e5).toISOString().slice(0, 10)}"></div>
      <div class="field"><label for="tfP">Priority</label><select id="tfP">${['high', 'insufficient', 'lower', 'routine'].map((p) => `<option ${p === priority ? 'selected' : ''} value="${p}">${p === 'insufficient' ? 'Insufficient evidence' : p[0].toUpperCase() + p.slice(1)}</option>`).join('')}</select></div>
      <div class="field" style="grid-column:1/-1"><label for="tfR">Instructions</label><textarea id="tfR" maxlength="500">${esc(reason || 'Dip for larvae following the standard protocol; record sampling effort even if no larvae are found.')}</textarea></div>
      <p class="hint" style="grid-column:1/-1;margin:0">${alert_id ? `Linked to alert <span class="mono">${esc(alert_id)}</span>. The alert is marked as actioned.` : 'Not linked to a forecast alert.'}</p>
      <div class="actions" style="grid-column:1/-1"><button class="btn primary">Assign</button></div>
    </form>`);
  d.querySelector('#tf').addEventListener('submit', async (e) => {
    e.preventDefault();
    try {
      await api.post('/api/tasks', { site_id: d.querySelector('#tfS').value.trim(), assignee_id: +d.querySelector('#tfA').value, due_date: d.querySelector('#tfD').value,
        priority: d.querySelector('#tfP').value, reason: d.querySelector('#tfR').value, alert_id, issue_date, horizon });
      toast('Inspection assigned'); d.close();
      document.querySelector('[aria-selected="true"][data-tab]')?.click();
    } catch (er) { fail(er); }
  });
}
export { taskDialog };

// ----------------------------------------------------------- observations
function prefill(task) {
  const f = document.getElementById('of');
  if (!f || !task) return;
  f.querySelector('#ofS').value = task.site_id;
  f.querySelector('#ofT').value = task.id;
  f.querySelector('#ofS').dispatchEvent(new Event('change'));
}

function record(box) {
  const sites = ST.boot.sites.map((s) => s.site_id);
  const q = pending();
  const now = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  box.innerHTML = `<div class="grid g-main">
    ${card('Field observation', `
    <form id="of" class="form cols-2" novalidate>
      <div class="field"><label for="ofS">Monitoring point</label><input id="ofS" list="ofSites" required pattern="G\\d{4}" placeholder="G0221"><datalist id="ofSites">${sites.map((s) => `<option value="${s}">`).join('')}</datalist></div>
      <div class="field"><label for="ofT">Task number <span class="u">optional</span></label><input id="ofT" type="number" min="1" placeholder="from your inspection list"></div>
      <div class="field"><label for="ofW">Observed at (local time)</label><input id="ofW" type="datetime-local" required value="${now}"></div>
      <div class="field"><label>Position</label><div class="actions"><button type="button" class="btn sm" id="ofGps">${icon('pin')}Use device GPS</button><span class="hint" id="ofGpsTxt">No position yet</span></div>
        <input type="hidden" id="ofLat"><input type="hidden" id="ofLon"><input type="hidden" id="ofAcc"></div>
      <div class="field"><label for="ofWater">Water present</label><select id="ofWater" required><option value="">Choose</option><option value="yes">Yes</option><option value="no">No</option><option value="unknown">Could not access / unknown</option></select></div>
      <div class="field"><label for="ofH">Habitat type</label><select id="ofH"><option value="">Choose</option>${Object.entries(HABITATS).map(([k, v]) => `<option value="${k}">${v}</option>`).join('')}</select></div>
      <div class="field"><label for="ofL">Larvae</label><select id="ofL" required><option value="">Choose</option><option value="yes">Found</option><option value="no">Sampled, none found</option><option value="not_sampled">Not sampled</option></select><span class="hint">"Not sampled" is never treated as "none found".</span></div>
      <div class="field"><label for="ofDips">Dips taken</label><input id="ofDips" type="number" min="0" max="500" inputmode="numeric"></div>
      <div class="field"><label for="ofLC">Larvae counted</label><input id="ofLC" type="number" min="0" inputmode="numeric"></div>
      <div class="field"><label for="ofA">Anopheles</label><select id="ofA"><option value="not_identified">Not identified</option><option value="yes">Present</option><option value="no">Absent</option></select></div>
      <div class="field" style="grid-column:1/-1"><label>Species identified</label><div class="checks">${Object.entries(SPECIES).map(([k, v]) => `<label class="check"><input type="checkbox" name="sp" value="${k}">${v}</label>`).join('')}</div></div>
      <div class="field"><label>Stages</label><div class="checks"><label class="check"><input type="checkbox" name="stg" value="early_instar">Early instars (L1–L2)</label><label class="check"><input type="checkbox" name="stg" value="late_instar">Late instars (L3–L4)</label></div></div>
      <div class="field"><label for="ofP">Pupae</label><select id="ofP"><option value="not_sampled">Not sampled</option><option value="yes">Found</option><option value="no">Sampled, none found</option></select></div>
      <div class="field"><label for="ofPC">Pupae counted</label><input id="ofPC" type="number" min="0" inputmode="numeric"></div>
      <div class="field"><label for="ofDep">Water depth <span class="u">cm</span></label><input id="ofDep" type="number" min="0" max="500" step="0.5"></div>
      <div class="field"><label for="ofVeg">Vegetation cover <span class="u">%</span></label><input id="ofVeg" type="number" min="0" max="100"></div>
      <div class="field" style="grid-column:1/-1"><label for="ofN">Notes</label><textarea id="ofN" maxlength="2000" placeholder="Access, conditions, anything a reviewer should know"></textarea></div>
      <p id="ofErr" role="alert" style="grid-column:1/-1;margin:0;color:var(--crit);font-size:12.5px"></p>
      <div class="actions" style="grid-column:1/-1"><button class="btn primary">${icon('upload')}Submit observation</button><span class="hint">Works offline: records are queued on this device and sent when the connection returns.</span></div>
    </form>`)}
    ${card(`Queued on this device · ${q.length}`, q.length ? `<div class="list">${q.map((o) => `<div><span><span class="mono">${o.site_id}</span> · ${o.observed_at.replace('T', ' ').slice(0, 16)}${o._error ? `<br><span style="color:var(--crit)">${esc(o._error)}</span>` : ''}</span><button class="btn sm ghost" data-discard="${o.client_uuid}">Discard</button></div>`).join('')}</div><div class="card-b"><button class="btn sm" id="flush">Send now</button></div>` : '<div class="empty sm">Nothing waiting. Observations recorded offline appear here until they are sent.</div>', { flush: true })}
  </div>`;
  const f = box.querySelector('#of');
  box.querySelector('#ofGps').addEventListener('click', () => {
    const txt = box.querySelector('#ofGpsTxt');
    if (!navigator.geolocation) { txt.textContent = 'This device has no GPS access.'; return; }
    txt.textContent = 'Locating…';
    navigator.geolocation.getCurrentPosition((p) => {
      f.querySelector('#ofLat').value = p.coords.latitude; f.querySelector('#ofLon').value = p.coords.longitude; f.querySelector('#ofAcc').value = p.coords.accuracy;
      txt.textContent = `${p.coords.latitude.toFixed(5)}, ${p.coords.longitude.toFixed(5)} ± ${Math.round(p.coords.accuracy)} m`;
    }, (er) => { txt.textContent = `GPS unavailable: ${er.message}`; }, { enableHighAccuracy: true, timeout: 20000 });
  });
  box.querySelectorAll('[data-discard]').forEach((b) => b.addEventListener('click', () => { discard(b.dataset.discard); record(box); }));
  box.querySelector('#flush')?.addEventListener('click', async () => { const n = await flushQueue(); toast(`${n} sent`); record(box); });
  f.addEventListener('submit', async (e) => {
    e.preventDefault();
    const v = (id) => f.querySelector(id).value;
    const num = (id) => (v(id) === '' ? null : Number(v(id)));
    const err = [];
    if (!/^G\d{4}$/.test(v('#ofS').trim())) err.push('Enter a monitoring point such as G0221.');
    if (!v('#ofWater')) err.push('Say whether water was present.');
    if (!v('#ofL')) err.push('Say whether larvae were sampled.');
    if (['yes', 'no'].includes(v('#ofL')) && !num('#ofDips')) err.push('Record how many dips were taken.');
    if (err.length) { f.querySelector('#ofErr').textContent = err.join(' '); return; }
    const o = {
      client_uuid: uuid(), task_id: num('#ofT'), site_id: v('#ofS').trim(),
      lat: num('#ofLat'), lon: num('#ofLon'), gps_accuracy_m: num('#ofAcc'),
      observed_at: new Date(v('#ofW')).toISOString(), water_present: v('#ofWater'), habitat_type: v('#ofH') || null,
      larvae_present: v('#ofL'), dips: num('#ofDips'), larvae_count: num('#ofLC'), anopheles_present: v('#ofA'),
      species: [...f.querySelectorAll('[name="sp"]:checked')].map((x) => x.value), stages: [...f.querySelectorAll('[name="stg"]:checked')].map((x) => x.value),
      pupae_present: v('#ofP'), pupae_count: num('#ofPC'), water_depth_cm: num('#ofDep'), vegetation_cover_pct: num('#ofVeg'), notes: v('#ofN'),
    };
    try {
      const r = await api.post('/api/observations', o);
      toast(r.quality_flags?.length ? `Saved for review: ${r.quality_flags.join(', ')}` : 'Observation submitted for review');
      record(box);
    } catch (er) {
      if (er.offline || er.timeout) { enqueue(o); toast('No connection: observation queued on this device'); record(box); }
      else f.querySelector('#ofErr').textContent = er.message;
    }
  });
}

// ------------------------------------------------------------------ review
async function review(box) {
  const os = await api.get('/api/observations');
  const queue = os.filter((o) => ['submitted', 'needs_review'].includes(o.status));
  const cols = [
    { h: 'Obs.', f: (o) => `<span class="mono">O${o.id}</span>${demo(o)}` },
    { h: 'Point', f: (o) => `<span class="mono">${o.site_id}</span>` },
    { h: 'Observed', f: (o) => o.observed_at.replace('T', ' ').slice(0, 16) },
    { h: 'By', f: (o) => esc(o.observer || '') },
    { h: 'Water', k: 'water_present' },
    { h: 'Larvae', f: (o) => `${o.larvae_present.replace('_', ' ')}${o.dips ? ` · ${o.larvae_count ?? 0}/${o.dips} dips` : ''}` },
    { h: 'Anopheles', f: (o) => `${o.anopheles_present.replace('_', ' ')}${o.species.length ? ` · ${o.species.map((s) => SPECIES[s]).join(', ')}` : ''}` },
    { h: 'Quality flags', f: (o) => (o.quality_flags.length ? o.quality_flags.map((x) => `<span class="badge">${x}</span>`).join(' ') : '–') },
    { h: 'Status', f: (o) => tag(OBS_STATUS, o.status) },
  ];
  const tq = table([...cols, { h: '', f: (o) => (o.observer_id === ST.user.id ? '<span class="hint">Your record: another reviewer needed</span>' : `<button class="btn sm primary" data-rev="${o.id}">Review</button>`) }], queue, { max: 400, empty: 'No observations waiting for review.' });
  const ta = table([...cols, { h: 'Reviewer note', f: (o) => esc(o.review_comment || '') }, { h: '', f: (o) => (eligible(o) && ST.can('authorize:intervention') ? `<button class="btn sm" data-auth="${o.id}">Authorise intervention</button>` : '') }],
    os.filter((o) => !['submitted', 'needs_review'].includes(o.status)), { max: 400, empty: 'No reviewed observations yet.' });
  box.innerHTML = card(`Waiting for review · ${queue.length}`, tq.html, { flush: true, sub: 'A record is verified by someone other than the person who made it' })
    + `<div style="height:14px"></div>` + card('Reviewed observations', ta.html, { flush: true });
  box.querySelectorAll('[data-rev]').forEach((b) => b.addEventListener('click', () => reviewDialog(os.find((o) => String(o.id) === b.dataset.rev), () => review(box))));
  box.querySelectorAll('[data-auth]').forEach((b) => b.addEventListener('click', () => authoriseDialog(os.find((o) => String(o.id) === b.dataset.auth), () => review(box))));
}
const eligible = (o) => o.status === 'verified' && (o.larvae_present === 'yes' || (o.water_present === 'yes' && o.larvae_present === 'not_sampled'));

function reviewDialog(o, done) {
  const d = modal(`Review observation O${o.id}`, `
    <dl class="dl" style="grid-template-columns:auto 1fr auto 1fr">
      <dt>Point</dt><dd class="mono">${o.site_id}</dd><dt>Observed</dt><dd>${o.observed_at.replace('T', ' ').slice(0, 16)}</dd>
      <dt>Water</dt><dd>${o.water_present}</dd><dt>Habitat</dt><dd>${HABITATS[o.habitat_type] || '–'}</dd>
      <dt>Larvae</dt><dd>${o.larvae_present.replace('_', ' ')}</dd><dt>Count / dips</dt><dd>${o.larvae_count ?? '–'} / ${o.dips ?? '–'}</dd>
      <dt>Anopheles</dt><dd>${o.anopheles_present.replace('_', ' ')}</dd><dt>Pupae</dt><dd>${o.pupae_present.replace('_', ' ')}</dd>
      <dt>GPS</dt><dd class="mono">${o.lat != null ? `${o.lat.toFixed(5)}, ${o.lon.toFixed(5)} ± ${Math.round(o.gps_accuracy_m || 0)} m` : 'none'}</dd><dt>Flags</dt><dd>${o.quality_flags.join(', ') || 'none'}</dd>
    </dl>
    ${o.notes ? `<p style="margin:0;color:var(--ink-2)">${esc(o.notes)}</p>` : ''}
    <div class="field"><label for="rvC">Comment <span class="u">required unless verifying</span></label><textarea id="rvC" maxlength="1000"></textarea></div>
    <div class="actions"><button class="btn primary" data-d="verified">${icon('check')}Verify</button><button class="btn" data-d="needs_review">Return for correction</button><button class="btn ghost" data-d="rejected">Reject</button></div>`);
  d.querySelectorAll('[data-d]').forEach((b) => b.addEventListener('click', async () => {
    try { await api.post(`/api/observations/${o.id}/review`, { decision: b.dataset.d, comment: d.querySelector('#rvC').value }); toast(`O${o.id} ${b.dataset.d.replace('_', ' ')}`); d.close(); done(); } catch (e) { fail(e); }
  }));
}

function authoriseDialog(o, done) {
  const d = modal(`Authorise intervention at ${o.site_id}`, `
    <div class="note warn"><b>Human authorisation.</b> You are authorising treatment on the basis of verified field observation O${o.id}, not the forecast. Follow the national larval source management protocol and product label.</div>
    <form id="af" class="form">
      <div class="field"><label for="afM">Method</label><select id="afM">${Object.entries(METHODS).map(([k, v]) => `<option value="${k}">${v}</option>`).join('')}</select></div>
      <div class="field"><label for="afP">Product <span class="u">optional</span></label><input id="afP" maxlength="200" placeholder="e.g. Bti WDG, registered product and batch"></div>
      <div class="field"><label for="afD">Planned dose <span class="u">optional</span></label><input id="afD" maxlength="200" placeholder="as per label"></div>
      <div class="field"><label for="afR">Protocol reference</label><input id="afR" required minlength="3" maxlength="200" placeholder="SOP number or circular"></div>
      <button class="btn primary">Authorise</button>
    </form>`);
  d.querySelector('#af').addEventListener('submit', async (e) => {
    e.preventDefault();
    try {
      await api.post('/api/interventions', { observation_id: o.id, method: d.querySelector('#afM').value, product: d.querySelector('#afP').value,
        planned_dose: d.querySelector('#afD').value, protocol_ref: d.querySelector('#afR').value });
      toast('Intervention authorised'); d.close(); done();
    } catch (er) { fail(er); }
  });
}

// ----------------------------------------------------------- interventions
async function interventions(box) {
  const [iv, os] = await Promise.all([api.get('/api/interventions'), api.get('/api/observations')]);
  const t = table([
    { h: 'No.', f: (i) => `<span class="mono">I${i.id}</span>${demo(i)}` },
    { h: 'Point', f: (i) => `<span class="mono">${i.site_id}</span>` },
    { h: 'Evidence', f: (i) => `<span class="mono">O${i.observation_id}</span>` },
    { h: 'Method', f: (i) => METHODS[i.method] || i.method },
    { h: 'Protocol', f: (i) => esc(i.protocol_ref) },
    { h: 'Authorised', f: (i) => `${esc(i.authorized_by_name || '')} · ${(i.authorized_utc || '').slice(0, 10)}` },
    { h: 'Performed', f: (i) => (i.performed_at ? `${esc(i.performed_by_name || '')} · ${i.performed_at.slice(0, 10)} · ${esc(i.quantity_used || '')}` : '–') },
    { h: 'Outcome', f: (i) => (i.outcome ? i.outcome.replace('_', ' ') : '–') },
    { h: 'Status', f: (i) => tag(IV_STATUS, i.status) },
    { h: '', f: (i) => (i.status === 'authorized' && ST.can('record:intervention') ? `<button class="btn sm primary" data-perf="${i.id}">Record treatment</button>`
      : i.status === 'performed' && ST.can('review:observation') ? `<button class="btn sm" data-fu="${i.id}">Close with follow-up</button>` : '') },
  ], iv, { max: 520, empty: 'No interventions. They are authorised from verified observations in the review queue.' });
  box.innerHTML = card(`Interventions · ${iv.length}`, t.html, { flush: true, sub: 'Every intervention links to the verified observation that justified it' });
  box.querySelectorAll('[data-perf]').forEach((b) => b.addEventListener('click', () => {
    const d = modal(`Record treatment I${b.dataset.perf}`, `<form id="pf" class="form">
      <div class="field"><label for="pfW">Performed at</label><input id="pfW" type="datetime-local" required value="${new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16)}"></div>
      <div class="field"><label for="pfQ">Quantity used</label><input id="pfQ" required maxlength="200" placeholder="e.g. 0.2 kg Bti WDG in 8 L"></div>
      <div class="field"><label for="pfN">Notes</label><textarea id="pfN" maxlength="1000"></textarea></div>
      <button class="btn primary">Save</button></form>`);
    d.querySelector('#pf').addEventListener('submit', async (e) => {
      e.preventDefault();
      try { await api.post(`/api/interventions/${b.dataset.perf}/performed`, { performed_at: new Date(d.querySelector('#pfW').value).toISOString(), quantity_used: d.querySelector('#pfQ').value, notes: d.querySelector('#pfN').value }); toast('Treatment recorded'); d.close(); interventions(box); } catch (er) { fail(er); }
    });
  }));
  box.querySelectorAll('[data-fu]').forEach((b) => b.addEventListener('click', () => {
    const i = iv.find((x) => String(x.id) === b.dataset.fu);
    const later = os.filter((o) => o.site_id === i.site_id && o.status === 'verified' && o.observed_at > (i.performed_at || ''));
    const d = modal(`Close I${i.id} with a follow-up`, later.length ? `<form id="ff" class="form">
      <div class="field"><label for="ffO">Follow-up observation (verified, after treatment)</label><select id="ffO">${later.map((o) => `<option value="${o.id}">O${o.id} · ${o.observed_at.slice(0, 10)} · larvae ${o.larvae_present.replace('_', ' ')} ${o.larvae_count ?? ''}</option>`).join('')}</select></div>
      <div class="field"><label for="ffR">Outcome</label><select id="ffR"><option value="larvae_absent">Larvae absent</option><option value="larvae_reduced">Larvae reduced</option><option value="larvae_unchanged">Larvae unchanged</option><option value="inconclusive">Inconclusive</option></select></div>
      <button class="btn primary">Close intervention</button></form>`
      : `<div class="empty sm">No verified observation at ${i.site_id} after the treatment yet. Assign a follow-up inspection, then verify the result.</div>
         ${ST.can('assign:task') ? '<button class="btn primary" id="fuTask">Assign follow-up inspection</button>' : ''}`);
    d.querySelector('#fuTask')?.addEventListener('click', () => { d.close(); taskDialog({ site_id: i.site_id, priority: 'routine', reason: `Follow-up after intervention I${i.id}: dip and record larval density.` }); });
    d.querySelector('#ff')?.addEventListener('submit', async (e) => {
      e.preventDefault();
      try { await api.post(`/api/interventions/${i.id}/followup`, { observation_id: +d.querySelector('#ffO').value, outcome: d.querySelector('#ffR').value }); toast('Intervention closed'); d.close(); interventions(box); } catch (er) { fail(er); }
    });
  }));
}

// ------------------------------------------------------------------ alerts
async function alerts(box) {
  const h = ST.h;
  const al = await api.get(`/api/alerts?h=${h}&date=${ST.date}`);
  const can = ST.can('act:alert');
  const t = table([
    { h: 'Point', f: (a) => `<a href="#" class="mono" data-site="${a.site_id}">${a.site_id}</a>` },
    { h: 'Why', f: (a) => `<span style="white-space:normal">${esc(a.why)}</span>` },
    { h: 'Evidence', f: (a) => `p ${a.evidence.probability.toFixed(2)} · ${a.evidence.uncertainty_band} uncertainty${a.evidence.wet_passes_recent ? ` · wet ${a.evidence.wet_passes_recent}× recently` : ''}` },
    { h: 'Relevant until', f: (a) => `<span style="${a.expired ? 'color:var(--ink-3)' : ''}">${a.relevant_until}</span>` },
    { h: 'Responsible', f: (a) => a.responsible_role },
    { h: 'State', f: (a) => `${tag(ALERT_STATUS, a.state)}${a.state_note ? `<div class="hint">${esc(a.state_note)}</div>` : ''}` },
    { h: '', f: (a) => (can && ['open', 'acknowledged'].includes(a.state) ? `<button class="btn sm primary" data-task="${a.id}">Assign inspection</button> ${a.state === 'open' ? `<button class="btn sm" data-ack="${a.id}">Acknowledge</button>` : ''} <button class="btn sm ghost" data-dis="${a.id}">Dismiss</button>`
      : can && a.state === 'expired' && ST.boot.demo ? `<button class="btn sm" data-task="${a.id}" title="Demonstration only: practise the workflow on a historical alert">Assign exercise</button>` : '') },
  ], al, { max: 560, empty: 'No high-priority alerts on this issue.' });
  const expired = al.filter((a) => a.expired).length;
  box.innerHTML = (expired ? `<div class="note warn" style="margin-bottom:12px"><b>${expired} of ${al.length} alerts have expired.</b> Their validity window has passed, so acting on them now would be acting on history. They remain here as a record and for training.</div>` : '')
    + card(`Alerts for issue ${ST.date} · ${h}-day`, t.html, { flush: true, sub: 'Raised for High-priority points only; one alert per point per issue', foot: `<span>Recommended action: ${esc(al[0]?.action || 'Assign a field inspection before the target date.')}</span>` });
  box.querySelectorAll('[data-site]').forEach((a) => a.addEventListener('click', (e) => { e.preventDefault(); window.openSite(a.dataset.site); }));
  box.querySelectorAll('[data-ack]').forEach((b) => b.addEventListener('click', async () => { try { await api.post(`/api/alerts/${b.dataset.ack}`, { state: 'acknowledged' }); toast('Acknowledged'); alerts(box); } catch (e) { fail(e); } }));
  box.querySelectorAll('[data-dis]').forEach((b) => b.addEventListener('click', () => {
    const d = modal('Dismiss alert', `<form id="df" class="form"><div class="field"><label for="dfR">Reason</label><textarea id="dfR" required maxlength="500" placeholder="e.g. Site inspected yesterday under routine surveillance"></textarea></div><button class="btn primary">Dismiss</button></form>`);
    d.querySelector('#df').addEventListener('submit', async (e) => { e.preventDefault(); try { await api.post(`/api/alerts/${b.dataset.dis}`, { state: 'dismissed', note: d.querySelector('#dfR').value }); toast('Dismissed'); d.close(); alerts(box); } catch (er) { fail(er); } });
  }));
  box.querySelectorAll('[data-task]').forEach((b) => b.addEventListener('click', () => {
    const a = al.find((x) => x.id === b.dataset.task);
    const exercise = a.expired;
    taskDialog({ site_id: a.site_id, alert_id: a.id, priority: 'high', issue_date: a.issue_date, horizon: a.horizon,
      due: exercise ? null : a.target_date,
      reason: exercise ? `EXERCISE on historical alert ${a.id}: practise verification and dipping. Not an operational instruction.` : `Forecast alert ${a.id}: verify new surface water and dip for larvae before ${a.target_date}.` });
  }));
}
