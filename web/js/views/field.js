// Field surveillance: alert → inspection → observation → review → authorised
// intervention → follow-up. Every action here is checked again on the server.
import { api, card, icon, table, toast, esc, modal, catTag, uuid, fmt, empty } from '../ui.js';
import { enqueue, pending, discard, flushQueue } from '../offline.js';
import { params } from '../app.js';
import { t } from '../i18n.js';

const PERM = { permanent: 'Permanent', semi_permanent: 'Semi-permanent', temporary: 'Temporary' };
const SUN = { sunlit: 'Sunlit', partly_shaded: 'Partly shaded', shaded: 'Shaded' };
const HABITATS = { rice_paddy: 'Rice paddy', marsh_swamp: 'Marsh / swamp', pond: 'Pond', puddle: 'Puddle', ditch_drain: 'Ditch / drain', borrow_pit: 'Borrow pit', stream_edge: 'Stream edge', container: 'Container', other: 'Other' };
const SPECIES = { an_gambiae_sl: 'An. gambiae s.l.', an_arabiensis: 'An. arabiensis', an_funestus: 'An. funestus', an_other: 'Other Anopheles', culicine: 'Culicine', not_identified: 'Not identified' };
const METHODS = { bti_ground: 'Bti, ground application', bti_drone: 'Bti, drone application', environmental_management: 'Environmental management (drain / fill)', other: 'Other (specify in protocol)' };
const OBS_STATUS = { submitted: ['st-info', 'Awaiting review'], needs_review: ['st-warn', 'Needs review'], returned: ['st-warn', 'Returned for correction'], verified: ['st-good', 'Verified'], rejected: ['st-mute', 'Rejected'] };
const TASK_STATUS = { assigned: ['st-info', 'Assigned'], in_progress: ['st-warn', 'In progress'], completed: ['st-good', 'Completed'], cancelled: ['st-mute', 'Cancelled'] };
const IV_STATUS = { authorized: ['st-warn', 'Authorised'], performed: ['st-info', 'Completed · follow-up due'], closed: ['st-good', 'Closed'], rejected: ['st-mute', 'Not authorised'] };
const SUIT = { suitable_for_drone: 'Suitable for drone application', suitable_for_ground: 'Suitable for ground application', not_suitable: 'Not suitable for treatment', needs_second_visit: 'Needs a second visit' };
const METHOD_LABEL = { standard_dipper_350ml: 'Standard dipper (350 ml)', pipette: 'Pipette', net: 'Net', other: 'Other' };
const ALERT_STATUS = { open: ['st-crit', 'Open'], actioned: ['st-good', 'Inspection assigned'], acknowledged: ['st-info', 'Acknowledged'], dismissed: ['st-mute', 'Dismissed'], expired: ['st-mute', 'Expired'] };
const tag = (m, k) => `<span class="status ${(m[k] || ['st-mute'])[0]}">${(m[k] || [0, k])[1]}</span>`;
const demo = (r) => (r.demo ? ' <span class="badge" title="Demonstration record">DEMO</span>' : '');

let ST;
export async function render(el, st) {
  ST = st;
  const tabs = [['tasks', t('Inspections')], ['record', t('Record observation')], ['review', t('Review queue'), 'review:observation'],
    ['interventions', t('Interventions')], ['alerts', t('Alerts')]].filter((x) => !x[2] || st.can(x[2]));
  const want = params().get('tab') || (st.can('review:observation') ? 'review' : 'tasks');
  el.innerHTML = `
  <div class="page-head">
    <div><h1>${t('Field surveillance')}</h1><p>${st.can('assign:task') ? 'Assign inspections, review what officers record, decide on treatment and schedule follow-up. Only a verified field finding can support a treatment decision.' : 'Your inspections. Record what you find, including when nothing was sampled. A supervisor reviews each record.'}</p></div>
    <div class="actions">${st.can('export:field') ? ['tasks', 'observations', 'interventions'].map((k) => `<a class="btn" href="/api/v1/field/export/${k}.csv">${icon('download')}${k[0].toUpperCase() + k.slice(1)} CSV</a>`).join('') : ''}</div>
  </div>
  <div class="tabs" role="tablist">${tabs.map(([k, l]) => `<button role="tab" data-tab="${k}" aria-selected="${k === want}">${l}</button>`).join('')}</div>
  <div id="fieldBody"></div>`;
  const show = (k) => {
    el.querySelectorAll('[data-tab]').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.tab === k)));
    ({ tasks, record, review, interventions, alerts })[k](document.getElementById('fieldBody'));
  };
  el.querySelectorAll('[data-tab]').forEach((b) => b.addEventListener('click', () => show(b.dataset.tab)));
  show(tabs.some((x) => x[0] === want) ? want : tabs[0][0]);
}

const fail = (e) => toast(e.message, true);

// ------------------------------------------------------------------- tasks
async function tasks(box) {
  const ts = await api.get('/api/tasks');
  const can = ST.can('assign:task');
  const tb = table([
    { h: t('Task'), f: (r) => `<span class="mono">T${r.id}</span>${demo(r)}` },
    { h: t('Point'), f: (r) => (r.site_id.startsWith('H') ? `<span class="mono" title="Field-registered habitat">${r.site_id}</span>` : `<a href="#" data-site="${r.site_id}" class="mono">${r.site_id}</a>`) },
    { h: t('Priority'), f: (r) => (['high', 'insufficient', 'moderate', 'lower'].includes(r.priority) ? catTag(r.priority) : t(r.priority[0].toUpperCase() + r.priority.slice(1))) },
    { h: t('From forecast'), f: (r) => (r.issue_date ? `${r.issue_date} · ${r.horizon}d` : t('Routine')) },
    { h: t('Assigned to'), f: (r) => esc(r.assignee || '') },
    { h: t('Due'), f: (r) => `<span style="${r.overdue ? 'color:var(--crit);font-weight:600' : ''}">${r.due_date}${r.overdue ? ` · ${t('overdue')}` : ''}</span>` },
    { h: t('Status'), f: (r) => tag(TASK_STATUS, r.status) },
    { h: '', f: (r) => actions(r) },
  ], ts, { max: 520, empty: can ? 'No inspections yet. Use "Assign inspection" or create them from open alerts.' : 'No inspections assigned to you.' });
  const habs = await api.get('/api/habitats').catch(() => []);
  const pos = (id) => { const s = ST.boot.sites.find((x) => x.site_id === id); if (s) return [s.latitude, s.longitude]; const h = habs.find((x) => x.code === id); return h ? [h.lat, h.lon] : null; };
  const mine = ts.filter((r) => r.assignee_id === ST.user.id && !['completed', 'cancelled'].includes(r.status));
  const cards = mine.length ? `<div class="grid g-3" style="margin-bottom:18px">${mine.map((r) => { const p = pos(r.site_id); return `<section class="task-card">
      <div class="top-row"><span class="id"><span class="mono">${r.site_id}</span> · T${r.id}${demo(r)}</span>${tag(TASK_STATUS, r.status)}</div>
      <div>${['high', 'insufficient', 'moderate', 'lower'].includes(r.priority) ? catTag(r.priority) : `<span class="status st-mute">${t('Routine')}</span>`} <span style="${r.overdue ? 'color:var(--crit);font-weight:600' : 'color:var(--ink-2)'}">${t('Due')} ${fmt.date(r.due_date)}${r.overdue ? ` · ${t('overdue')}` : ''}</span></div>
      ${p ? `<div class="hint mono">${fmt.ll(p[0], p[1])}</div>` : ''}${r.reason ? `<div style="font-size:13px;color:var(--ink-2)">${esc(r.reason)}</div>` : ''}
      <div class="actions">${r.status === 'assigned' ? `<button class="btn" data-ts="in_progress" data-id="${r.id}">${icon('play')}Start inspection</button>` : ''}<button class="btn primary" data-obs="${r.id}">${icon('upload')}${t('Record finding')}</button>
        ${p ? `<a class="btn" href="https://www.google.com/maps/dir/?api=1&destination=${p[0]},${p[1]}" target="_blank" rel="noopener">${icon('navigate')}Navigate</a>` : ''}<button class="btn ghost" data-prev="${r.site_id}">${icon('replay')}Previous observations</button></div></section>`; }).join('')}</div>` : '';
  box.innerHTML = (can ? '' : `<h2 style="font-size:18px;margin:0 0 12px">My assignments · ${mine.length}</h2>${cards || `<div class="card" style="margin-bottom:18px">${empty('No inspections assigned to you', 'New assignments appear here.', 'check')}</div>`}`)
    + card(`${can ? t('Inspections') : 'All my inspections'} · ${ts.length}`, tb.html, { flush: true, tools: can ? `<button class="btn sm primary" id="newTask">${icon('clipboard')}${t('Assign inspection')}</button>` : '' });
  box.querySelectorAll('[data-prev]').forEach((b) => b.addEventListener('click', () => window.openSite(b.dataset.prev)));
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
  if (mine && r.status === 'assigned') out.push(`<button class="btn sm" data-ts="in_progress" data-id="${r.id}">${t('Start')}</button>`);
  if (mine || ST.can('write:observation')) out.push(`<button class="btn sm primary" data-obs="${r.id}">${t('Record finding')}</button>`);
  if (ST.can('assign:task')) out.push(`<button class="btn sm ghost" data-ts="cancelled" data-id="${r.id}">${t('Cancel')}</button>`);
  return out.join(' ');
}

async function taskDialog({ site_id = '', alert_id = null, priority = 'routine', issue_date = null, horizon = null, due = null, reason = '' }) {
  const people = await api.get('/api/field/assignees').catch(fail);
  if (!people) return;
  const sites = ST.boot.sites.map((s) => s.site_id);
  const d = modal('Assign inspection', `
    <form id="tf" class="form cols-2">
      <div class="field"><label for="tfS">Monitoring point</label><input id="tfS" list="tfSites" value="${esc(site_id)}" required pattern="[GH]\\d{4}"><datalist id="tfSites">${sites.map((s) => `<option value="${s}">`).join('')}</datalist></div>
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
function prefill(task, tries = 0) {
  const f = document.getElementById('of');
  if (!task) return;
  if (!f) { if (tries < 40) setTimeout(() => prefill(task, tries + 1), 50); return; }
  f.querySelector('#ofS').value = task.site_id;
  f.querySelector('#ofT').value = task.id;
  f.querySelector('#ofS').dispatchEvent(new Event('change'));
}

async function record(box) {
  const sites = ST.boot.sites.map((s) => s.site_id);
  const habs = await api.get('/api/habitats').catch(() => []);
  const q = pending();
  const mineObs = await api.get('/api/observations?status=returned').catch(() => []);
  const returned = mineObs.filter((o) => o.observer_id === ST.user.id);
  const methods = ST.boot.protocol.sampling_methods;
  const now = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  const sync = !navigator.onLine ? `<div class="sync off" role="status">${icon('alert')}Offline. ${q.length} observation(s) are saved on this device and will be sent when the connection returns.</div>`
    : q.length ? `<div class="sync pending" role="status">${icon('sync')}${q.length} observation(s) saved on this device are waiting to be sent.</div>`
      : `<div class="sync" role="status">${icon('check')}Everything recorded on this device has been sent.</div>`;
  box.innerHTML = `${sync}${returned.length ? `<div class="note warn" style="margin-bottom:14px"><b>${returned.length} observation(s) returned for correction.</b> ${returned.map((o) => `<span class="mono">O${o.id}</span> at <span class="mono">${o.site_id}</span>: ${esc(o.review_comment || '')}`).join(' · ')} Record a corrected observation for the same location.</div>` : ''}<div class="grid g-main">
    ${card(t('Field observation'), `
    <form id="of" class="form cols-2" novalidate>
      <div class="field" style="grid-column:1/-1"><label class="check"><input type="checkbox" id="ofNew"> <b>${t('New habitat, not a monitoring point')}</b></label><span class="hint">${t('Register it with your GPS position; it gets its own habitat code for review, treatment and follow-up.')}</span></div>
      <div class="field" id="ofSWrap"><label for="ofS">${t('Monitoring point')}</label><input id="ofS" list="ofSites" required pattern="[GH]\\d{4}" placeholder="G0221 / H0001"><datalist id="ofSites">${sites.map((s) => `<option value="${s}">`).join('')}${habs.map((h) => `<option value="${h.code}">`).join('')}</datalist></div>
      <div class="field" id="ofDescWrap" hidden><label for="ofDesc">${t('Habitat description')}</label><input id="ofDesc" maxlength="300" placeholder="e.g. drainage ditch behind the health post"></div>
      <div class="field"><label for="ofT">${t('Task number')} <span class="u">${t('optional')}</span></label><input id="ofT" type="number" min="1" placeholder="from your inspection list"></div>
      <div class="field"><label for="ofW">${t('Observed at (local time)')}</label><input id="ofW" type="datetime-local" required value="${now}"></div>
      <div class="field"><label>${t('Position')}</label><div class="actions"><button type="button" class="btn sm" id="ofGps">${icon('pin')}${t('Use device GPS')}</button><span class="hint" id="ofGpsTxt">${t('No position yet')}</span></div>
        <input type="hidden" id="ofLat"><input type="hidden" id="ofLon"><input type="hidden" id="ofAcc"></div>
      <div class="field"><label for="ofWater">${t('Water present')}</label><select id="ofWater" required><option value="">${t('Choose')}</option><option value="yes">${t('Yes')}</option><option value="no">${t('No')}</option><option value="unknown">${t('Could not access / unknown')}</option></select></div>
      <div class="field"><label for="ofH">${t('Habitat type')}</label><select id="ofH"><option value="">${t('Choose')}</option>${Object.entries(HABITATS).map(([k, v]) => `<option value="${k}">${v}</option>`).join('')}</select></div>
      <div class="field"><label for="ofPerm">${t('Habitat permanence')}</label><select id="ofPerm"><option value="">${t('Choose')}</option>${Object.entries(PERM).map(([k, v]) => `<option value="${k}">${t(v)}</option>`).join('')}</select></div>
      <div class="field"><label for="ofSun">${t('Sun exposure')}</label><select id="ofSun"><option value="">${t('Choose')}</option>${Object.entries(SUN).map(([k, v]) => `<option value="${k}">${t(v)}</option>`).join('')}</select></div>
      <div class="field"><label for="ofL">${t('Larvae')}</label><select id="ofL" required><option value="">${t('Choose')}</option><option value="yes">${t('Found')}</option><option value="no">${t('Sampled, none found')}</option><option value="not_sampled">${t('Not sampled')}</option></select><span class="hint">${t('"Not sampled" is never treated as "none found".')}</span></div>
      <div class="field"><label for="ofDips">${t('Dips taken')}</label><input id="ofDips" type="number" min="0" max="500" inputmode="numeric"></div>
      <div class="field"><label for="ofMeth">Sampling method</label><select id="ofMeth"><option value="">${t('Choose')}</option>${methods.map((m) => `<option value="${m}">${METHOD_LABEL[m] || m}</option>`).join('')}</select></div>
      <div class="field"><label for="ofLC">${t('Larvae counted')} <span class="u">all larvae</span></label><input id="ofLC" type="number" min="0" inputmode="numeric"><span class="hint" id="ofDens"></span></div>
      <div class="field"><label for="ofALC">Anopheles larvae counted <span class="u">${t('optional')}</span></label><input id="ofALC" type="number" min="0" inputmode="numeric"></div>
      <div class="field"><label for="ofA">Anopheles</label><select id="ofA"><option value="not_identified">${t('Not identified')}</option><option value="yes">${t('Present')}</option><option value="no">${t('Absent')}</option></select></div>
      <div class="field" style="grid-column:1/-1"><label>${t('Species identified')}</label><div class="checks">${Object.entries(SPECIES).map(([k, v]) => `<label class="check"><input type="checkbox" name="sp" value="${k}">${v}</label>`).join('')}</div></div>
      <div class="field"><label>${t('Stages')}</label><div class="checks"><label class="check"><input type="checkbox" name="stg" value="early_instar">Early instars (L1–L2)</label><label class="check"><input type="checkbox" name="stg" value="late_instar">Late instars (L3–L4)</label></div></div>
      <div class="field"><label for="ofP">${t('Pupae')}</label><select id="ofP"><option value="not_sampled">${t('Not sampled')}</option><option value="yes">${t('Found')}</option><option value="no">${t('Sampled, none found')}</option></select></div>
      <div class="field"><label for="ofPC">${t('Pupae counted')}</label><input id="ofPC" type="number" min="0" inputmode="numeric"></div>
      <div class="field"><label for="ofDep">${t('Water depth')} <span class="u">cm</span></label><input id="ofDep" type="number" min="0" max="500" step="0.5"></div>
      <div class="field"><label for="ofVeg">${t('Vegetation cover')} <span class="u">%</span></label><input id="ofVeg" type="number" min="0" max="100"></div>
      <div class="field"><label for="ofHouse">${t('Distance to nearest house')} <span class="u">m</span></label><input id="ofHouse" type="number" min="0" max="100000" inputmode="numeric"></div>
      <div class="field" style="grid-column:1/-1"><label for="ofPh">${icon('camera')} Photographs <span class="u">${t('optional')} · up to 3 · needs a connection</span></label><input id="ofPh" type="file" accept="image/jpeg,image/png,image/webp" capture="environment" multiple><span class="hint">Do not photograph people or homes.</span></div>
      <div class="field" style="grid-column:1/-1"><label for="ofN">${t('Notes')}</label><textarea id="ofN" maxlength="2000" placeholder="Access, conditions, anything a reviewer should know"></textarea></div>
      <p id="ofErr" role="alert" style="grid-column:1/-1;margin:0;color:var(--crit);font-size:12.5px"></p>
      <div class="actions" style="grid-column:1/-1"><button class="btn primary">${icon('upload')}${t('Submit observation')}</button><span class="hint">${t('Works offline: records are queued on this device and sent when the connection returns.')}</span></div>
    </form>`)}
    ${card(`${t('Queued on this device')} · ${q.length}`, q.length ? `<div class="list">${q.map((o) => `<div><span><span class="mono">${o.site_id || 'new habitat'}</span> · ${o.observed_at.replace('T', ' ').slice(0, 16)}${o._error ? `<br><span style="color:var(--crit)">${esc(o._error)}</span>` : ''}</span><button class="btn sm ghost" data-discard="${o.client_uuid}">${t('Discard')}</button></div>`).join('')}</div><div class="card-b"><button class="btn sm" id="flush">${t('Send now')}</button></div>` : '<div class="empty sm">Nothing waiting. Observations recorded offline appear here until they are sent.</div>', { flush: true })}
  </div>`;
  const f = box.querySelector('#of');
  const isNew = () => f.querySelector('#ofNew').checked;
  f.querySelector('#ofNew').addEventListener('change', () => {
    f.querySelector('#ofSWrap').hidden = isNew(); f.querySelector('#ofDescWrap').hidden = !isNew();
    if (isNew()) { f.querySelector('#ofS').value = ''; f.querySelector('#ofT').value = ''; }
  });
  const dens = () => {
    const d = Number(f.querySelector('#ofDips').value), c = f.querySelector('#ofLC').value;
    f.querySelector('#ofDens').textContent = d > 0 && c !== '' ? `${t('Larvae per dip')}: ${(Number(c) / d).toFixed(2)}` : '';
  };
  f.querySelector('#ofDips').addEventListener('input', dens); f.querySelector('#ofLC').addEventListener('input', dens);
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
  const preset = params().get('site');
  if (preset && !f.querySelector('#ofS').value) f.querySelector('#ofS').value = preset;
  f.addEventListener('submit', async (e) => {
    e.preventDefault();
    const v = (id) => f.querySelector(id).value;
    const num = (id) => (v(id) === '' ? null : Number(v(id)));
    const err = [];
    if (!isNew() && !/^[GH]\d{4}$/.test(v('#ofS').trim())) err.push('Enter a monitoring point such as G0221 or a habitat code such as H0001.');
    if (isNew() && v('#ofLat') === '') err.push('A new habitat needs a GPS position: tap "Use device GPS".');
    if (!v('#ofWater')) err.push('Say whether water was present.');
    if (!v('#ofL')) err.push('Say whether larvae were sampled.');
    if (['yes', 'no'].includes(v('#ofL')) && !num('#ofDips')) err.push('Record how many dips were taken.');
    if (err.length) { f.querySelector('#ofErr').textContent = err.join(' '); return; }
    const o = {
      client_uuid: uuid(), task_id: isNew() ? null : num('#ofT'), site_id: isNew() ? null : v('#ofS').trim(),
      new_habitat: isNew(), habitat_description: isNew() ? v('#ofDesc') : '',
      habitat_permanence: v('#ofPerm') || null, sun_exposure: v('#ofSun') || null, distance_to_house_m: num('#ofHouse'),
      lat: num('#ofLat'), lon: num('#ofLon'), gps_accuracy_m: num('#ofAcc'),
      observed_at: new Date(v('#ofW')).toISOString(), water_present: v('#ofWater'), habitat_type: v('#ofH') || null,
      larvae_present: v('#ofL'), dips: num('#ofDips'), larvae_count: num('#ofLC'), anopheles_larvae_count: num('#ofALC'), sampling_method: v('#ofMeth') || null, anopheles_present: v('#ofA'),
      species: [...f.querySelectorAll('[name="sp"]:checked')].map((x) => x.value), stages: [...f.querySelectorAll('[name="stg"]:checked')].map((x) => x.value),
      pupae_present: v('#ofP'), pupae_count: num('#ofPC'), water_depth_cm: num('#ofDep'), vegetation_cover_pct: num('#ofVeg'), notes: v('#ofN'),
    };
    const photos = [...f.querySelector('#ofPh').files].slice(0, 3);
    try {
      const r = await api.post('/api/observations', o);
      let sent = 0;
      for (const ph of photos) {
        const fd = new FormData(); fd.append('file', ph);
        try { await api.upload(`/api/observations/${r.id}/photos`, fd); sent++; } catch (pe) { toast(`A photograph was not saved: ${pe.message}`, true); }
      }
      if (sent) toast(`${sent} photograph(s) saved with O${r.id}`);
      toast(r.habitat_code ? `New habitat registered as ${r.habitat_code}; observation sent for review` : r.quality_flags?.length ? `Saved for review: ${r.quality_flags.join(', ')}` : 'Observation submitted for review');
      record(box);
    } catch (er) {
      if (er.offline || er.timeout) {
        if (!enqueue(o)) { f.querySelector('#ofErr').textContent = 'This device could not save the observation (storage is full or blocked). Keep this form open and write the details down until you have a connection.'; return; }
        toast(photos.length ? 'No connection: the observation is saved on this device. Photographs were not saved; add them when you are online.' : 'No connection: the observation is saved on this device and will be sent later'); record(box);
      }
      else f.querySelector('#ofErr').textContent = er.message;
    }
  });
}

// ------------------------------------------------------------------ review
async function review(box) {
  const os = await api.get('/api/observations');
  const queue = os.filter((o) => ['submitted', 'needs_review'].includes(o.status));
  const ivAll = await api.get('/api/interventions');
  const decided = new Set(ivAll.filter((i) => ['authorized', 'performed'].includes(i.status)).map((i) => i.observation_id));
  const cols = [
    { h: 'Obs.', f: (o) => `<span class="mono">O${o.id}</span>${demo(o)}` },
    { h: 'Point', f: (o) => `<span class="mono">${o.site_id}</span>` },
    { h: 'Observed', f: (o) => o.observed_at.replace('T', ' ').slice(0, 16) },
    { h: 'By', f: (o) => esc(o.observer || '') },
    { h: 'Water', k: 'water_present' },
    { h: 'Larvae', f: (o) => `${o.larvae_present.replace('_', ' ')}${o.dips ? ` · ${o.larvae_count ?? 0}/${o.dips} dips` : ''}${o.larvae_per_dip != null && o.larvae_present === 'yes' ? ` · ${o.larvae_per_dip}/dip` : ''}` },
    { h: 'Anopheles', f: (o) => `${o.anopheles_present.replace('_', ' ')}${o.species.length ? ` · ${o.species.map((s) => SPECIES[s]).join(', ')}` : ''}` },
    { h: 'Quality flags', f: (o) => (o.quality_flags.length ? o.quality_flags.map((x) => `<span class="badge">${x}</span>`).join(' ') : '–') },
    { h: 'Status', f: (o) => tag(OBS_STATUS, o.status) },
  ];
  const tq = table([...cols, { h: '', f: (o) => (o.observer_id === ST.user.id ? '<span class="hint">Your record: another reviewer needed</span>' : `<button class="btn sm primary" data-rev="${o.id}">Review</button>`) }], queue, { max: 400, empty: 'No observations waiting for review.' });
  const ta = table([...cols, { h: 'Reviewer note', f: (o) => esc(o.review_comment || '') },
    { h: 'Intervention', f: (o) => (o.status !== 'verified' ? '–' : decided.has(o.id) ? '<span class="status st-info">Decision recorded</span>' : eligible(o) ? '<span class="status st-warn">Eligible for review</span>' : '<span class="status st-mute">Evidence insufficient</span>') },
    { h: '', f: (o) => (eligible(o) && !decided.has(o.id) && ST.can('authorize:intervention') ? `<button class="btn sm primary" data-auth="${o.id}">Assess for treatment</button>` : '') }],
    os.filter((o) => !['submitted', 'needs_review'].includes(o.status)), { max: 400, empty: 'No reviewed observations yet.' });
  box.innerHTML = card(`Waiting for review · ${queue.length}`, tq.html, { flush: true, sub: 'A record is verified by someone other than the person who made it' })
    + `<div style="height:14px"></div>` + card('Reviewed observations', ta.html, { flush: true });
  box.querySelectorAll('[data-rev]').forEach((b) => b.addEventListener('click', () => reviewDialog(os.find((o) => String(o.id) === b.dataset.rev), () => review(box))));
  box.querySelectorAll('[data-auth]').forEach((b) => b.addEventListener('click', () => authoriseDialog(os.find((o) => String(o.id) === b.dataset.auth), () => review(box))));
}
const eligible = (o) => { const p = ST.boot.protocol.intervention; if (o.status !== 'verified') return false; if (o.larvae_present === 'yes') return !(p.require_anopheles_confirmation && o.anopheles_present !== 'yes'); return !!p.accept_water_without_larvae_sample && o.water_present === 'yes' && o.larvae_present === 'not_sampled'; };

function reviewDialog(o, done) {
  const d = modal(`Review observation O${o.id}`, `
    <dl class="dl" style="grid-template-columns:auto 1fr auto 1fr">
      <dt>Point</dt><dd class="mono">${o.site_id}</dd><dt>Observed</dt><dd>${o.observed_at.replace('T', ' ').slice(0, 16)}</dd>
      <dt>Water</dt><dd>${o.water_present}</dd><dt>Habitat</dt><dd>${HABITATS[o.habitat_type] || '–'}</dd>
      <dt>Larvae</dt><dd>${o.larvae_present.replace('_', ' ')}</dd><dt>Count / dips</dt><dd>${o.larvae_count ?? '–'} / ${o.dips ?? '–'}${o.anopheles_larvae_count != null ? ` · ${o.anopheles_larvae_count} Anopheles` : ''}</dd>
      <dt>Method</dt><dd>${METHOD_LABEL[o.sampling_method] || '–'}</dd><dt>Stages</dt><dd>${o.stages.map((s) => s.replace('_', ' ')).join(', ') || '–'}</dd>
      <dt>Anopheles</dt><dd>${o.anopheles_present.replace('_', ' ')}</dd><dt>Pupae</dt><dd>${o.pupae_present.replace('_', ' ')}</dd>
      <dt>Permanence</dt><dd>${PERM[o.habitat_permanence] || '–'}</dd><dt>Sun / nearest house</dt><dd>${SUN[o.sun_exposure] || '–'} / ${o.distance_to_house_m != null ? `${o.distance_to_house_m} m` : '–'}</dd>
      <dt>GPS</dt><dd class="mono">${o.lat != null ? `${o.lat.toFixed(5)}, ${o.lon.toFixed(5)} ± ${Math.round(o.gps_accuracy_m || 0)} m` : 'none'}</dd><dt>Flags</dt><dd>${o.quality_flags.join(', ') || 'none'}</dd>
    </dl>
    ${o.notes ? `<p style="margin:0;color:var(--ink-2)">${esc(o.notes)}</p>` : ''}
    <div class="field"><label for="rvC">Reason <span class="u">required to return or reject</span></label><textarea id="rvC" maxlength="1000"></textarea></div>
    <div class="actions"><button class="btn primary" data-d="verified">${icon('check')}Verify</button><button class="btn" data-d="returned">Return for correction</button><button class="btn ghost" data-d="rejected">Reject</button></div>`, { wide: true });
  api.get(`/api/dossier/${o.site_id}`).then((ds) => { const ph = (ds.photos || []).filter((p) => p.observation_id === o.id); if (ph.length) d.querySelector('.dl').insertAdjacentHTML('afterend', `<div class="photos">${ph.map((p) => `<a href="/api/v1/photos/${p.id}" target="_blank" rel="noopener"><img src="/api/v1/photos/${p.id}" alt="Photograph from this observation"></a>`).join('')}</div>`); }).catch(() => {});
  d.querySelectorAll('[data-d]').forEach((b) => b.addEventListener('click', async () => {
    if (b.dataset.d !== 'verified' && !d.querySelector('#rvC').value.trim()) { toast('Give a reason so the officer knows what to correct.', true); d.querySelector('#rvC').focus(); return; }
    try { await api.post(`/api/observations/${o.id}/review`, { decision: b.dataset.d, comment: d.querySelector('#rvC').value }); toast(`O${o.id} ${b.dataset.d.replace('_', ' ')}`); d.close(); done(); } catch (e) { fail(e); }
  }));
}

function authoriseDialog(o, done) {
  const pr = ST.boot.protocol;
  const d = modal(`Treatment decision at ${o.site_id}`, `
    <div class="note warn"><b>Your decision, not the forecast's.</b> This rests on verified field observation O${o.id}: larvae ${o.larvae_present.replace('_', ' ')}${o.dips ? `, ${o.larvae_count ?? 0} in ${o.dips} dips` : ''}, Anopheles ${o.anopheles_present.replace('_', ' ')}.</div>
    ${pr.status !== 'approved' ? `<div class="note"><b>Protocol settings: awaiting approval.</b> ${esc(pr.intervention.note)} No treatment threshold is set until RBC approves one.</div>` : ''}
    <form id="af" class="form">
      <div class="field"><label for="afS">Habitat suitability assessment</label><select id="afS" required><option value="">Choose</option>${pr.habitat_suitability_options.map((k) => `<option value="${k}">${SUIT[k] || k}</option>`).join('')}</select></div>
      <div class="field"><label for="afM">Method</label><select id="afM">${Object.entries(METHODS).map(([k, v]) => `<option value="${k}">${v}</option>`).join('')}</select></div>
      <div class="field"><label for="afP">Product <span class="u">optional</span></label><input id="afP" maxlength="200" placeholder="e.g. Bti WDG, registered product and batch"></div>
      <div class="field"><label for="afD">Planned dose <span class="u">optional</span></label><input id="afD" maxlength="200" placeholder="as per label"></div>
      <div class="field"><label for="afR">Protocol reference</label><input id="afR" required minlength="3" maxlength="200" placeholder="SOP number or circular"></div>
      <div class="actions"><button class="btn primary">${icon('check')}Authorise treatment</button><button type="button" class="btn" id="afNo">Do not authorise</button></div>
    </form>`, { wide: true });
  d.querySelector('#afNo').addEventListener('click', () => {
    const r = modal('Do not authorise treatment', `<form id="nf" class="form"><div class="field"><label for="nfR">Reason</label><textarea id="nfR" required minlength="5" maxlength="1000" placeholder="e.g. habitat drying, not suitable for treatment, second visit needed"></textarea></div><button class="btn primary">Record decision</button></form>`);
    r.querySelector('#nf').addEventListener('submit', async (e) => { e.preventDefault(); try { await api.post('/api/interventions/reject', { observation_id: o.id, reason: r.querySelector('#nfR').value, habitat_suitability: d.querySelector('#afS').value || null }); toast('Decision recorded: not authorised'); r.close(); d.close(); done(); } catch (er) { fail(er); } });
  });
  d.querySelector('#af').addEventListener('submit', async (e) => {
    e.preventDefault();
    try {
      await api.post('/api/interventions', { observation_id: o.id, method: d.querySelector('#afM').value, product: d.querySelector('#afP').value,
        planned_dose: d.querySelector('#afD').value, protocol_ref: d.querySelector('#afR').value, habitat_suitability: d.querySelector('#afS').value });
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
    { h: 'Method', f: (i) => (i.status === 'rejected' ? `<span style="white-space:normal;color:var(--ink-2)">${esc(i.decision_reason || '')}</span>` : METHODS[i.method] || i.method) },
    { h: 'Suitability', f: (i) => SUIT[i.habitat_suitability] || '–' },
    { h: 'Protocol', f: (i) => (i.status === 'rejected' ? '–' : `${esc(i.protocol_ref)}${i.protocol_status && i.protocol_status !== 'approved' ? ' <span class="badge" title="Protocol settings were awaiting approval when this was authorised">pending settings</span>' : ''}`) },
    { h: 'Authorised', f: (i) => `${esc(i.authorized_by_name || '')} · ${(i.authorized_utc || '').slice(0, 10)}` },
    { h: 'Performed', f: (i) => (i.performed_at ? `${esc(i.performed_by_name || '')} · ${i.performed_at.slice(0, 10)} · ${esc(i.quantity_used || '')}` : '–') },
    { h: 'Outcome', f: (i) => (i.outcome ? i.outcome.replace('_', ' ') : '–') },
    { h: 'Status', f: (i) => tag(IV_STATUS, i.status) },
    { h: '', f: (i) => (i.status === 'authorized' && ST.can('record:intervention') ? `<button class="btn sm primary" data-perf="${i.id}">Record treatment</button>`
      : i.status === 'performed' && ST.can('review:observation') ? `<button class="btn sm" data-fu="${i.id}">Close with follow-up</button>` : '') },
  ], iv, { max: 520, empty: 'No interventions. They are authorised from verified observations in the review queue.' });
  box.innerHTML = card(`Treatment decisions · ${iv.length}`, t.html, { flush: true, sub: 'Every decision links to the verified observation behind it', foot: '<span>Order of work: field verification, entomological evidence, suitability assessment, protocol reference, supervisor decision, treatment record, follow-up.</span>' });
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
