import { api, card, kpi, fmt, icon, table, toast, esc, modal } from '../ui.js';

export async function render(el, st) {
  const [s, users, audit, fb] = await Promise.all([api.get('/api/admin/status'), api.get('/api/admin/users'), api.get('/api/admin/audit?limit=300'), api.get('/api/feedback')]);
  const roles = st.user.roles;
  el.innerHTML = `
  <div class="page-head">
    <div><h1>Administration</h1><p>Accounts, service health, backups and the audit trail. Application uptime, forecast-engine readiness and forecast freshness are reported separately: the service can be up while its forecast is stale.</p></div>
    <div class="actions"><a class="btn" href="/api/docs" target="_blank" rel="noopener">${icon('file')}API reference</a></div>
  </div>
  <div class="grid g-4">
    ${kpi('Application', 'Up', `uptime ${fmt.n(s.uptime_s / 3600, 1)} h · engine ${esc(s.engine_version)}`, { accent: true })}
    ${kpi('Forecast engine', s.engine === 'ready' ? 'Ready' : esc(s.engine), `schema v${s.schema_version}`)}
    ${kpi('Forecast freshness', s.forecast.status === 'stale' ? `<span style="color:var(--crit)">Stale</span>` : 'Current', `latest issue ${s.forecast.latest_issue} · ${s.forecast.age_days} days old`)}
    ${kpi('Database', `${fmt.n(s.database_bytes / 1024, 0)}`, `${s.backups.length} backups on this server`, { unit: 'KB' })}
  </div>
  ${s.demo ? '<div class="note warn" style="margin-top:14px"><b>Demonstration mode is on.</b> Demonstration sign-in is open to anyone; records they create are flagged. Turn RINDA_DEMO off for operational use.</div>' : ''}
  <div class="grid g-2" style="margin-top:14px">
    ${card(`Users · ${users.length}`, table([
      { h: 'Name', f: (u) => `${esc(u.name)}${u.demo ? ' <span class="badge">DEMO</span>' : ''}` }, { h: 'Email', f: (u) => `<span class="mono">${esc(u.email)}</span>` },
      { h: 'Role', f: (u) => `<select data-role="${u.id}" aria-label="Role for ${esc(u.name)}" ${u.id === st.user.id ? 'disabled' : ''}>${roles.map((r) => `<option value="${r.role}" ${r.role === u.role ? 'selected' : ''}>${r.label}</option>`).join('')}</select>` },
      { h: 'Last sign-in', f: (u) => (u.last_login_utc || '–').replace('T', ' ').replace('Z', '') },
      { h: 'Active', f: (u) => (u.id === st.user.id ? 'You' : `<label class="check"><input type="checkbox" data-active="${u.id}" ${u.active ? 'checked' : ''}> active</label>`) },
    ], users, { max: 380 }).html, { flush: true, tools: '<button class="btn sm primary" id="newUser">Add user</button>' })}
    ${card('Service metrics since start', table([
      { h: 'Endpoint group', f: (g) => `<span class="mono">${g.group}</span>` }, { h: 'Requests', r: 1, k: 'requests' },
      { h: 'p50 ms', r: 1, f: (g) => g.p50_ms ?? '–' }, { h: 'p95 ms', r: 1, f: (g) => g.p95_ms ?? '–' },
      { h: '5xx', r: 1, f: (g) => `<span style="${g.errors_5xx ? 'color:var(--crit)' : ''}">${g.errors_5xx}</span>` },
    ], s.requests, { max: 380 }).html, { flush: true, sub: 'In-process; resets on restart' })}
    ${card('Backups', `<p style="margin:0 0 10px;font-size:12.5px;color:var(--ink-2)">A consistent copy of the operations database (users, tasks, observations, interventions, missions, audit). Download each backup and store it off this server; on the hosted demonstration the server's disk is wiped on every redeploy.</p>
      <div class="actions" style="margin-bottom:10px"><button class="btn primary" id="mkBackup">${icon('download')}Create backup</button></div>
      ${table([{ h: 'File', f: (b) => `<a class="mono" href="/api/v1/admin/backup/${encodeURIComponent(b.file)}">${esc(b.file)}</a>` }, { h: 'Size', r: 1, f: (b) => `${fmt.n(b.bytes / 1024, 0)} KB` }], s.backups, { empty: 'No backups yet.' }).html}`)}
    ${card('Configuration', `
      <div class="kv-row"><span>Operational rules version</span><span class="mono">${esc(s.rules_version)}</span></div>
      <div class="kv-row"><span>Rules approved by</span><span style="color:${/PENDING/.test(s.rules_approved_by) ? 'var(--warn)' : 'var(--ink)'}">${esc(s.rules_approved_by)}</span></div>
      <div class="kv-row"><span>Public read access</span><span>${s.public_read ? 'On (viewers need no account)' : 'Off'}</span></div>
      <div class="kv-row"><span>Demonstration sign-in</span><span>${s.demo ? 'On' : 'Off'}</span></div>
      <div class="kv-row"><span>Radar input, latest pass</span><span>${s.inputs.radar_last_pass}</span></div>
      <div class="kv-row"><span>Weather input, latest day</span><span>${s.inputs.weather_last_day}</span></div>
      <p style="font-size:11.5px;color:var(--ink-3);margin:8px 0 0">Rules live in config/operational_rules.json and change only through a reviewed commit. See docs/admin-guide.md.</p>`)}
  </div>
  <div class="grid g-2" style="margin-top:14px">
    ${card(`Audit trail · last ${audit.length}`, table([
      { h: 'Time (UTC)', f: (a) => `<span class="mono">${a.ts_utc.replace('T', ' ').replace('Z', '')}</span>` }, { h: 'Actor', f: (a) => esc(a.actor) },
      { h: 'Action', f: (a) => `<span class="mono">${esc(a.action)}</span>` }, { h: 'Detail', f: (a) => `<span style="white-space:normal">${esc(a.detail)}</span>` },
    ], audit, { max: 420 }).html, { flush: true, sub: 'Append-only' })}
    ${card(`User feedback · ${fb.length}`, table([
      { h: 'Time', f: (f) => f.ts_utc.slice(0, 16).replace('T', ' ') }, { h: 'Page', k: 'page' }, { h: 'Message', f: (f) => `<span style="white-space:normal">${esc(f.message)}</span>` },
    ], fb, { max: 420, empty: 'No feedback yet.' }).html, { flush: true })}
  </div>`;

  el.querySelector('#newUser').addEventListener('click', () => {
    const d = modal('Add user', `<form id="nu" class="form cols-2">
      <div class="field"><label for="nuN">Full name</label><input id="nuN" required minlength="2"></div>
      <div class="field"><label for="nuE">Email</label><input id="nuE" type="email" required></div>
      <div class="field"><label for="nuR">Role</label><select id="nuR">${roles.map((r) => `<option value="${r.role}">${r.label}</option>`).join('')}</select></div>
      <div class="field"><label for="nuO">Organisation</label><input id="nuO" placeholder="e.g. RBC, Gasabo DH"></div>
      <div class="field" style="grid-column:1/-1"><label for="nuP">Initial password</label><input id="nuP" type="password" autocomplete="new-password" required minlength="12"><span class="hint">Share it through a separate channel; the user should change it at first sign-in.</span></div>
      <div class="actions" style="grid-column:1/-1"><button class="btn primary">Create</button></div></form>`);
    d.querySelector('#nu').addEventListener('submit', async (e) => {
      e.preventDefault();
      try {
        await api.post('/api/admin/users', { name: d.querySelector('#nuN').value, email: d.querySelector('#nuE').value, role: d.querySelector('#nuR').value, organisation: d.querySelector('#nuO').value, password: d.querySelector('#nuP').value });
        toast('User created'); d.close(); render(el, st);
      } catch (er) { toast(er.message, true); }
    });
  });
  el.querySelectorAll('[data-role]').forEach((s2) => s2.addEventListener('change', async () => {
    try { await api.patch(`/api/admin/users/${s2.dataset.role}`, { role: s2.value }); toast('Role updated'); } catch (e) { toast(e.message, true); }
  }));
  el.querySelectorAll('[data-active]').forEach((c) => c.addEventListener('change', async () => {
    try { await api.patch(`/api/admin/users/${c.dataset.active}`, { active: c.checked }); toast(c.checked ? 'Account reactivated' : 'Account deactivated and signed out'); } catch (e) { toast(e.message, true); }
  }));
  el.querySelector('#mkBackup').addEventListener('click', async () => {
    try { const b = await api.post('/api/admin/backup'); toast(`Backup ${b.file} created`); render(el, st); } catch (e) { toast(e.message, true); }
  });
}
