// Monitoring & Reporting: RE-AIM indicators computed from RINDA's own records,
// the weekly larval source management report, a DHIS2-shaped export and the
// message outbox. Nothing here estimates malaria impact.
import { api, card, kpi, fmt, icon, chart, css, series, table, toast, esc, modal } from '../ui.js';
import { params } from '../app.js';

let ST;
export async function render(el, st) {
  ST = st;
  const tabs = [['indicators', 'Indicators (RE-AIM)', 'read:me'], ['weekly', 'Weekly report & DHIS2', 'export:report'],
    ['messages', 'Messages', 'read:outbox']].filter((x) => st.can(x[2]));
  const want = params().get('tab') || tabs[0]?.[0];
  el.innerHTML = `
  <div class="page-head">
    <div><h1>Monitoring &amp; reporting</h1><p>How RINDA is being used and what field teams found, for district vector control and the national malaria programme. Every figure is counted from records in RINDA: tasks, field observations, reviews, treatments and sign-ins. An indicator with no records shows "no data", never zero.</p></div>
  </div>
  <div class="note" style="margin-bottom:14px"><b>What this page does not show.</b> Malaria cases or incidence. RINDA is ready for controlled testing and is not validated for intervention decisions; any effect on malaria has to be measured against HMIS case data in a designed evaluation.</div>
  <div class="tabs" role="tablist">${tabs.map(([k, l]) => `<button role="tab" data-tab="${k}" aria-selected="${k === want}">${l}</button>`).join('')}</div>
  <div id="pgBody"></div>`;
  const show = (k) => {
    el.querySelectorAll('[data-tab]').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.tab === k)));
    ({ indicators, weekly, messages })[k](document.getElementById('pgBody'));
  };
  el.querySelectorAll('[data-tab]').forEach((b) => b.addEventListener('click', () => show(b.dataset.tab)));
  if (tabs.length) show(tabs.some((x) => x[0] === want) ? want : tabs[0][0]);
}

const pct = (v) => fmt.pct(v, v > 0 && v < 0.1 ? 1 : 0);
const val = (i) => {
  if (i.status === 'no_data') return '<span class="hint">no data</span>';
  if (i.unit === 'share') return `<b>${pct(i.value)}</b> <span class="hint">${i.numerator} / ${i.denominator}</span>${i.denominator < 20 ? '<br><span class="hint" title="Fewer than 20 records: too few to compare or to draw conclusions">few records</span>' : ''}`;
  if (i.unit === 'days') return `<b>${fmt.n(i.value, 1)}</b> <span class="hint">days</span>`;
  return `<b>${fmt.n(i.value)}</b>`;
};

// ------------------------------------------------------------ indicators
async function indicators(box, weeks = 12, demo = '') {
  const r = await api.get(`/api/me/indicators?weeks=${weeks}${demo ? `&demo=${demo}` : ''}`);
  const sec = Object.fromEntries(r.sections.map((s) => [s.key, s]));
  const pick = (k, id) => sec[k].indicators.find((i) => i.id === id);
  const head = (i) => (i.status === 'no_data' ? 'no data' : i.unit === 'share' ? pct(i.value) : fmt.n(i.value, i.unit === 'days' ? 1 : 0));
  box.innerHTML = `
  <div class="actions" style="margin:4px 0 12px;flex-wrap:wrap">
    <label class="hint" for="pgW">Period</label>
    <select id="pgW">${[4, 8, 12, 26, 52].map((w) => `<option value="${w}" ${w === weeks ? 'selected' : ''}>last ${w} weeks</option>`).join('')}</select>
    ${ST.boot.demo ? `<select id="pgD" aria-label="Records included"><option value="">All records (demonstration)</option><option value="exclude" ${demo === 'exclude' ? 'selected' : ''}>Operational only</option><option value="only" ${demo === 'only' ? 'selected' : ''}>Demonstration only</option></select>` : ''}
    <span class="hint">${esc(r.period.from)} to ${esc(r.period.to)} · ${esc(r.scope)} · ${esc(r.framework)}</span>
  </div>
  <div class="grid g-4">
    ${kpi('Reach: points inspected', head(pick('reach', 'R1')), pick('reach', 'R1').status === 'measured' ? `${pick('reach', 'R1').numerator} of ${pick('reach', 'R1').denominator} monitoring points` : 'no field observations in period', { accent: true })}
    ${kpi('Adoption: active accounts', head(pick('adoption', 'A1')), pick('adoption', 'A1').status === 'measured' ? `${pick('adoption', 'A1').numerator} of ${pick('adoption', 'A1').denominator} signed in` : 'no accounts')}
    ${kpi('Fidelity: on-time inspections', head(pick('implementation', 'I1')), pick('implementation', 'I1').status === 'measured' ? `${pick('implementation', 'I1').numerator} of ${pick('implementation', 'I1').denominator} due` : 'no tasks due in period')}
    ${kpi('Maintenance: active weeks', head(pick('maintenance', 'M1')), pick('maintenance', 'M1').status === 'measured' ? `${pick('maintenance', 'M1').numerator} of ${pick('maintenance', 'M1').denominator} weeks` : '')}
  </div>
  <div class="grid g-2" style="margin-top:14px">
    ${r.sections.map((s) => card(s.title, table([
      { h: '', f: (i) => `<span class="mono">${i.id}</span>` },
      { h: 'Indicator', f: (i) => `<span style="white-space:normal">${esc(i.label)}${i.note ? `<br><span class="hint">${esc(i.note)}</span>` : ''}</span>` },
      { h: 'Value', r: 1, f: val },
    ], s.indicators).html + (s.by_role ? `<div class="card-b">${table([
      { h: 'Role', f: (x) => `${esc(x.label)}<br><span class="hint">${esc(x.title || '')}</span>` },
      { h: 'Accounts', r: 1, k: 'accounts' }, { h: 'Signed in', r: 1, k: 'signed_in' },
    ], s.by_role, { empty: 'No accounts.' }).html}</div>` : ''), { flush: true, sub: s.question })).join('')}
    ${card('Weekly activity', '<div class="chart" id="pgChart" style="height:260px"></div>', { sub: 'Records per ISO week' })}
  </div>
  <div style="margin-top:14px">${card('Not measured by RINDA', `<ul style="margin:0;padding-left:18px">${r.not_measured.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>`)}</div>`;
  box.querySelector('#pgW').addEventListener('change', (e) => indicators(box, +e.target.value, demo));
  box.querySelector('#pgD')?.addEventListener('change', (e) => indicators(box, weeks, e.target.value));
  const wk = r.weekly;
  chart(box.querySelector('#pgChart'), {
    grid: { left: 36, right: 12, top: 30, bottom: 28 }, tooltip: { trigger: 'axis' },
    legend: { top: 0, textStyle: { color: css('--ink-2') } },
    xAxis: { type: 'category', data: wk.map((w) => w.week.slice(5)), axisLabel: { color: css('--ink-3') } },
    yAxis: { type: 'value', minInterval: 1, axisLabel: { color: css('--ink-3') }, splitLine: { lineStyle: { color: css('--line') } } },
    series: [['observations', 'Observations'], ['tasks', 'Tasks assigned'], ['treatments', 'Treatments'], ['habitats', 'New habitats']].map(([k, n], i) => ({
      name: n, type: 'bar', stack: null, data: wk.map((w) => w[k]), itemStyle: { color: series(i + 1) }, barMaxWidth: 14,
    })),
  });
}

// ---------------------------------------------------------------- weekly
function isoWeekNow() {
  const d = new Date(); d.setUTCHours(0, 0, 0, 0); d.setUTCDate(d.getUTCDate() + 4 - (d.getUTCDay() || 7));
  const y = d.getUTCFullYear(); const w = Math.ceil(((d - Date.UTC(y, 0, 1)) / 864e5 + 1) / 7);
  return `${y}-W${String(w).padStart(2, '0')}`;
}

async function weekly(box, week = isoWeekNow()) {
  const [r, dx] = await Promise.all([api.get(`/api/reports/weekly?week=${week}`), api.get(`/api/reports/dhis2?week=${week}`)]);
  const m = dx._rinda;
  box.innerHTML = `
  <div class="actions" style="margin:4px 0 12px;flex-wrap:wrap">
    <label for="wkSel" class="hint">Epidemiological week</label><input id="wkSel" type="week" value="${week}">
    <a class="btn primary" target="_blank" rel="noopener" href="/api/v1/reports/weekly.html?week=${week}">${icon('print')}Report (English)</a>
    <a class="btn" target="_blank" rel="noopener" href="/api/v1/reports/weekly.html?week=${week}&lang=rw">${icon('print')}Raporo (Kinyarwanda, draft)</a>
    <span class="hint">${esc(r.from)} to ${esc(r.to)} · ${esc(r.scope)}</span>
  </div>
  <div class="grid g-4">
    ${kpi('Inspections completed', fmt.n(r.counts.lsm_inspections_completed), `${r.counts.lsm_sites_inspected} distinct sites`, { accent: true })}
    ${kpi('Larvae positive', fmt.n(r.counts.lsm_sites_larvae_positive), `of ${r.counts.lsm_sites_sampled} sites sampled · ${r.counts.lsm_dips_total} dips`)}
    ${kpi('Anopheles positive', fmt.n(r.counts.lsm_sites_anopheles_positive), r.observations.mean_larvae_per_dip_positive != null ? `${r.observations.mean_larvae_per_dip_positive} larvae/dip where positive` : 'no positive sites')}
    ${kpi('Sites treated', fmt.n(r.counts.lsm_sites_treated), `${r.inspections.open_and_overdue_at_week_end} tasks overdue at week end`)}
  </div>
  <div class="grid g-2" style="margin-top:14px">
    ${card('DHIS2 / HMIS export', `
      <div class="note ${m.mapping_status === 'pending' ? 'warn' : ''}" style="margin-bottom:10px"><b>Mapping ${esc(m.mapping_status)}.</b> ${m.unmapped_elements.length} of ${dx.dataValues.length} data elements and the ${dx.orgUnit === 'UNMAPPED' ? 'organisation unit are' : 'data set is'} not yet mapped to DHIS2 identifiers. ${esc(m.note)}</div>
      <p style="margin:0 0 10px;font-size:12.5px;color:var(--ink-2)">A DHIS2 <span class="mono">dataValueSet</span> for period <span class="mono">${esc(dx.period)}</span>, weekly. RINDA never sends it anywhere; the HMIS team checks and imports it. ${m.demonstration ? '<b>Demonstration deployment: values are stamped DO NOT IMPORT.</b>' : ''}</p>
      <div class="actions"><a class="btn" href="/api/v1/reports/dhis2?week=${week}&format=csv">${icon('download')}CSV (DHIS2 import format)</a><button class="btn" id="dxJson">${icon('download')}JSON dataValueSet</button></div>
      <div style="margin-top:10px">${table([{ h: 'Data element', f: (v) => `<span class="mono" style="font-size:11px">${esc(v.dataElement)}</span>` }, { h: 'Value', r: 1, k: 'value' }], dx.dataValues).html}</div>`, { sub: `mapping v${esc(m.mapping_version)} · config/dhis2_mapping.json` })}
    ${card('Weekly summary', table([
      { h: 'Item', f: (x) => x[0] }, { h: 'Count', r: 1, f: (x) => (x[1] == null ? '–' : x[1]) },
    ], [
      ['Inspections assigned', r.inspections.assigned], ['of which from forecast alerts', r.inspections.forecast_led],
      ['Observations, not sampled', r.observations.not_sampled], ['Pupae positive', r.observations.pupae_positive],
      ['New habitats registered', r.counts.lsm_new_habitats], ['Treatments authorised', r.interventions.authorised],
      ['Treatments performed', r.interventions.performed], ['Closed with follow-up', r.interventions.closed],
      ...Object.entries(r.interventions.outcomes).map(([k, v]) => [`Follow-up: ${k.replace('_', ' ')}`, v]),
      ...Object.entries(r.alerts).map(([k, v]) => [`Alerts ${k}`, v]),
      ['Latest forecast issue', `${r.forecast.latest_issue} (${r.forecast.status}, ${r.forecast.age_days} d)`],
    ]).html, { flush: true, foot: `<span>${esc(r.statement)}</span>` })}
  </div>`;
  box.querySelector('#wkSel').addEventListener('change', (e) => { if (e.target.value) weekly(box, e.target.value); });
  box.querySelector('#dxJson').addEventListener('click', () => {
    const b = new Blob([JSON.stringify(dx, null, 2)], { type: 'application/json' });
    const a = document.createElement('a'); a.href = URL.createObjectURL(b); a.download = `rinda_dhis2_${dx.period}.json`; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  });
}

// -------------------------------------------------------------- messages
const MSG = { draft: ['st-warn', 'Draft · not sent'], queued: ['st-info', 'Queued'], sent_manually: ['st-good', 'Sent by hand'], cancelled: ['st-mute', 'Cancelled'] };
async function messages(box) {
  const r = await api.get('/api/messages');
  const can = ST.can('write:outbox');
  box.innerHTML = `
  <div class="note ${r.provider ? '' : 'warn'}" style="margin:4px 0 12px"><b>${r.provider ? `Provider: ${esc(r.provider)}` : 'No SMS or WhatsApp provider is connected.'}</b>
    RINDA prepares each message in the recipient's language (Kinyarwanda texts are drafts awaiting review) and records it here. Until a provider is approved and connected, copy the text, send it from a programme phone, then mark it as sent. Phone numbers are shown masked.</div>
  ${card(`Outbox · ${r.messages.length}`, table([
    { h: 'No.', f: (m) => `<span class="mono">M${m.id}</span>${m.demo ? ' <span class="badge">DEMO</span>' : ''}` },
    { h: 'Type', f: (m) => (m.kind === 'task_notice' ? `Task notice <span class="mono">${esc(m.ref || '')}</span>` : `Weekly digest <span class="mono">${esc(m.ref || '')}</span>`) },
    { h: 'To', f: (m) => `${esc(m.recipient_label || '')}<br><span class="hint mono">${esc(m.phone || 'no phone on file')}</span>` },
    { h: 'Channel', f: (m) => `${m.channel.toUpperCase()} · ${m.language === 'rw' ? 'Kinyarwanda (draft)' : 'English'}` },
    { h: 'Text', f: (m) => `<span style="white-space:normal;font-size:12px">${esc(m.body)}</span><br><span class="hint">${m.chars} characters${m.channel === 'sms' ? ` · ${m.sms_parts} SMS` : ''}</span>` },
    { h: 'Status', f: (m) => `<span class="status ${(MSG[m.status] || ['st-mute'])[0]}">${(MSG[m.status] || [0, m.status])[1]}</span>` },
    { h: '', f: (m) => (can && ['draft', 'queued'].includes(m.status) ? `<button class="btn sm" data-copy="${m.id}">Copy</button> <button class="btn sm primary" data-sent="${m.id}">Mark sent</button> <button class="btn sm ghost" data-cancel="${m.id}">Cancel</button>` : '') },
  ], r.messages, { max: 600, empty: 'No messages yet. Task notices are prepared automatically when an inspection is assigned.' }).html, {
    flush: true, tools: can ? `<button class="btn sm primary" id="mkDigest">${icon('bell')}Prepare weekly digest</button>` : '' })}`;
  const set = async (id, status) => { try { await api.post(`/api/messages/${id}`, { status }); toast(status === 'cancelled' ? 'Message cancelled' : 'Marked as sent'); messages(box); } catch (e) { toast(e.message, true); } };
  box.querySelectorAll('[data-sent]').forEach((b) => b.addEventListener('click', () => set(b.dataset.sent, 'sent_manually')));
  box.querySelectorAll('[data-cancel]').forEach((b) => b.addEventListener('click', () => set(b.dataset.cancel, 'cancelled')));
  box.querySelectorAll('[data-copy]').forEach((b) => b.addEventListener('click', async () => {
    const m = r.messages.find((x) => String(x.id) === b.dataset.copy);
    try { await navigator.clipboard.writeText(m.body); toast('Text copied'); } catch (e) { toast('Copy not available here; select the text instead.', true); }
  }));
  box.querySelector('#mkDigest')?.addEventListener('click', () => {
    const d = modal('Prepare weekly digest', `<form id="dg" class="form">
      <div class="field"><label for="dgW">Week</label><input id="dgW" type="week" value="${isoWeekNow()}" required></div>
      <div class="field"><label for="dgC">Channel</label><select id="dgC"><option value="sms">SMS</option><option value="whatsapp">WhatsApp</option></select></div>
      <p class="hint" style="margin:0">One message per active supervisor and programme manager, in each person's language. Nothing is sent automatically.</p>
      <button class="btn primary">Prepare</button></form>`);
    d.querySelector('#dg').addEventListener('submit', async (e) => {
      e.preventDefault();
      try { const x = await api.post('/api/messages/digest', { week: d.querySelector('#dgW').value, channel: d.querySelector('#dgC').value }); toast(`${x.drafted} digest(s) prepared`); d.close(); messages(box); } catch (er) { toast(er.message, true); }
    });
  });
}
