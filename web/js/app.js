import { api, disposeAll, drawer, toast, icon, fmt, esc, newScope, modal, banner } from './ui.js';
import { openSite } from './site.js';
import { flushQueue, queued } from './offline.js';

export const state = {
  boot: null, h: 7, date: null, user: null, meta: null,
  dateFor(h) { const ds = this.boot.horizons[h].dates; return ds.filter((d) => d <= this.date).pop() || ds[0]; },
  can(p) { return !!this.user?.permissions?.includes(p); },
};

const ROUTES = [
  { group: 'Operations', items: [
    ['overview', 'Situation Overview', 'overview'],
    ['forecast', 'Habitat Forecast', 'forecast'],
    ['field', 'Field Surveillance', 'clipboard', 'read:field'],
    ['missions', 'Mission Planning', 'mission'],
    ['flight', 'Flight Operations', 'flight'],
  ] },
  { group: 'Evidence', items: [
    ['replay', 'Replay & Validation', 'replay'],
    ['assurance', 'Model Assurance', 'assurance'],
    ['scenario', 'Scenario Lab', 'scenario'],
    ['network', 'Field Network', 'network'],
  ] },
  { group: 'Programme', items: [
    ['impact', 'Impact & Economics', 'impact'],
    ['bulletins', 'Reports & Bulletins', 'bulletin'],
    ['evidence', 'Programme Evidence', 'alignment'],
    ['governance', 'Data & Provenance', 'governance'],
    ['admin', 'Administration', 'shield', 'admin:system'],
    ['help', 'Help & Glossary', 'help'],
  ] },
];
const VIEWS = {
  overview: () => import('./views/overview.js'), forecast: () => import('./views/forecast.js'),
  field: () => import('./views/field.js'), missions: () => import('./views/missions.js'),
  flight: () => import('./views/flight.js'), replay: () => import('./views/replay.js'),
  scenario: () => import('./views/scenario.js'), assurance: () => import('./views/assurance.js'),
  network: () => import('./views/network.js'), impact: () => import('./views/impact.js'),
  bulletins: () => import('./views/bulletins.js'), evidence: () => import('./views/evidence.js'),
  governance: () => import('./views/governance.js'), admin: () => import('./views/admin.js'),
  help: () => import('./views/help.js'),
};
const ROLE_HOME = { viewer: 'overview', field_officer: 'field', analyst: 'replay', supervisor: 'overview', admin: 'admin' };

function navHTML() {
  return ROUTES.map((g) => {
    const items = g.items.filter((i) => !i[3] || state.can(i[3]));
    return `<div class="nav-group">${g.group}</div>` + items.map(([k, label, ic]) =>
      `<a class="nav-link" href="#/${k}" data-route="${k}">${icon(ic)}<span>${label}</span>${k === 'forecast' ? '<span class="badge hot" id="navAlerts" hidden></span>' : ''}${k === 'field' ? '<span class="badge" id="navTasks" hidden></span>' : ''}</a>`).join('');
  }).join('');
}

let current = null;
async function route() {
  const key = (location.hash.replace(/^#\/?/, '').split('?')[0]) || ROLE_HOME[state.user?.role] || 'overview';
  let name = VIEWS[key] ? key : 'overview';
  const item = ROUTES.flatMap((g) => g.items).find((i) => i[0] === name);
  if (item?.[3] && !state.can(item[3])) {
    toast(state.user?.authenticated ? 'Your role does not include that module.' : 'Sign in to open that module.', true);
    name = 'overview';
  }
  document.querySelectorAll('.nav-link').forEach((a) => {
    if (a.dataset.route === name) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  });
  const grp = ROUTES.find((g) => g.items.some((i) => i[0] === name));
  document.getElementById('crumbGroup').textContent = grp.group;
  document.getElementById('crumbView').textContent = grp.items.find((i) => i[0] === name)[1];
  document.title = `${grp.items.find((i) => i[0] === name)[1]} · RINDA`;
  document.getElementById('nav').classList.remove('open');
  drawer.close();
  newScope();
  const view = document.getElementById('view');
  disposeAll();
  current?.destroy?.();
  view.innerHTML = '<div class="skeleton" style="height:36px;width:320px;margin-bottom:18px"></div><div class="grid g-4">' + '<div class="skeleton" style="height:110px"></div>'.repeat(4) + '</div>';
  try {
    const mod = await VIEWS[name]();
    current = mod;
    await mod.render(view, state);
    view.focus({ preventScroll: true });
  } catch (e) {
    if (e.cancelled) return;
    console.error(e);
    view.innerHTML = `<div class="note crit" role="alert"><b>This page could not load.</b> ${esc(e.message)}
      <div style="margin-top:8px"><button class="btn sm" id="retryView">Try again</button></div></div>`;
    document.getElementById('retryView')?.addEventListener('click', route);
  }
}
export const rerender = () => route();
export const params = () => new URLSearchParams(location.hash.split('?')[1] || '');

// ---------------------------------------------------------------- banners
async function refreshMeta() {
  try { state.meta = await api.get(`/api/forecast/meta?h=${state.h}&date=${state.date}`); } catch (e) { if (!e.cancelled) state.meta = null; }
  renderBanners();
}

function renderBanners() {
  const m = state.meta;
  const out = [];
  if (state.boot.demo) {
    out.push(banner('warn', `${icon('info')}<div><b>Demonstration environment.</b> Forecasts are the real study archive; anything you create here is a labelled demonstration record.</div>`));
  }
  if (m) {
    const ms = m.model_status;
    const stale = m.warnings.find((w) => w.code === 'stale_forecast');
    if (stale) out.push(banner('critical', `${icon('alert')}<div><b>Stale forecast.</b> ${esc(stale.message)} Latest available issue: ${fmt.date(m.latest_issue)}. No forecast is being produced because no current satellite or weather inputs are connected.</div>`));
    out.push(banner('info', `${icon('shield')}<div><b>${esc(ms.label)}.</b> Outputs prioritise field inspection only; they never authorise treatment.
      <details><summary>Why</summary><ul style="margin:6px 0 0;padding-left:18px">${ms.reasons.map((r) => `<li>${esc(r)}</li>`).join('')}</ul></details></div>`));
    const lh = m.warnings.find((w) => w.code === 'long_horizon');
    if (lh) out.push(banner('warn', `${icon('info')}<div>${esc(lh.message)}</div>`));
    const fc = document.getElementById('freshChip');
    fc.hidden = false;
    fc.className = 'chip ' + (m.status === 'stale' ? 'bad' : 'ok');
    fc.lastChild.textContent = m.status === 'stale' ? `Stale · ${m.age_days} d old` : 'Current';
    fc.title = `Issued ${m.issue_date}; valid ${m.valid_window[0]} to ${m.valid_window[1]}`;
  }
  document.getElementById('banners').innerHTML = out.join('');
}

// -------------------------------------------------------------------- auth
async function loadUser() {
  state.user = await api.get('/api/auth/me');
  const u = state.user;
  document.getElementById('userName').textContent = u.authenticated ? u.name : 'Sign in';
  document.getElementById('userRole').innerHTML = u.authenticated ? `${esc(u.role_label)}${u.demo ? ' <span class="demo">DEMO</span>' : ''}` : '';
  document.getElementById('navlinks').innerHTML = navHTML();
}

function authDialog() {
  const u = state.user;
  if (u?.authenticated) {
    const d = modal('Account', `
      <dl class="dl"><dt>Name</dt><dd>${esc(u.name)}</dd><dt>Role</dt><dd>${esc(u.role_label)}</dd><dt>Email</dt><dd class="mono">${esc(u.email)}</dd>${u.demo ? '<dt>Account</dt><dd>Demonstration</dd>' : ''}</dl>
      <p style="margin:0;font-size:12px;color:var(--ink-3)">Permissions: ${u.permissions.map((p) => `<span class="badge">${p}</span>`).join(' ')}</p>
      ${u.demo ? '' : `<details><summary style="cursor:pointer">Change password</summary><form id="pwForm" class="form" style="margin-top:10px">
        <div class="field"><label for="pwC">Current password</label><input id="pwC" type="password" autocomplete="current-password" required></div>
        <div class="field"><label for="pwN">New password</label><input id="pwN" type="password" autocomplete="new-password" minlength="12" required><span class="hint">At least 12 characters, upper- and lower-case letters and a digit.</span></div>
        <button class="btn primary">Update password</button></form></details>`}
      <div class="actions"><button class="btn" id="signOut">${icon('x')}Sign out</button></div>`);
    d.querySelector('#signOut').addEventListener('click', async () => { await api.post('/api/auth/logout'); d.close(); await afterAuth('Signed out'); });
    d.querySelector('#pwForm')?.addEventListener('submit', async (e) => {
      e.preventDefault();
      try { await api.post('/api/auth/password', { current: d.querySelector('#pwC').value, new: d.querySelector('#pwN').value }); toast('Password updated'); d.close(); }
      catch (er) { toast(er.message, true); }
    });
    return;
  }
  const d = modal('Sign in', `
    <form id="loginForm" class="form">
      <div class="field"><label for="lgE">Email</label><input id="lgE" type="email" autocomplete="username" required></div>
      <div class="field"><label for="lgP">Password</label><input id="lgP" type="password" autocomplete="current-password" required></div>
      <p id="lgErr" role="alert" style="margin:0;color:var(--crit);font-size:12.5px"></p>
      <button class="btn primary">Sign in</button>
    </form>
    ${u?.demo_enabled ? `<div class="fieldset" style="border:1px solid var(--line);border-radius:6px;padding:12px">
      <b style="font-size:12.5px">Explore with a demonstration role</b>
      <p style="margin:4px 0 10px;font-size:12px;color:var(--ink-3)">No password. Everything you create is stored as a demonstration record.</p>
      <div class="actions">${u.roles.map((r) => `<button class="btn sm" data-demo="${r.role}">${r.label}</button>`).join('')}</div></div>` : ''}
    <p style="margin:0;font-size:11.5px;color:var(--ink-3)">Accounts are issued by your institution's RINDA administrator.${u?.public_read ? ' Forecasts and validation can be viewed without signing in.' : ''}</p>`);
  d.querySelector('#loginForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    try { await api.post('/api/auth/login', { email: d.querySelector('#lgE').value, password: d.querySelector('#lgP').value }); d.close(); await afterAuth('Signed in'); }
    catch (er) { d.querySelector('#lgErr').textContent = er.message; }
  });
  d.querySelectorAll('[data-demo]').forEach((b) => b.addEventListener('click', async () => {
    try { await api.post('/api/auth/demo', { role: b.dataset.demo }); d.close(); await afterAuth(`Signed in as ${b.textContent} (demonstration)`); }
    catch (er) { toast(er.message, true); }
  }));
}
export const openAuth = authDialog;

async function afterAuth(msg) {
  await loadUser();
  toast(msg);
  location.hash = '#/' + (ROLE_HOME[state.user.role] || 'overview');
  route();
  refreshBadges();
}

// ------------------------------------------------------------------ chrome
function fillDates() {
  const sel = document.getElementById('dateSel');
  const ds = state.boot.horizons[state.h].dates;
  if (!ds.includes(state.date)) state.date = state.dateFor(state.h);
  sel.innerHTML = [...ds].reverse().map((d, i) => `<option value="${d}">Issue ${d}${i === 0 ? ' (latest)' : ''}</option>`).join('');
  sel.value = state.date;
}

let switching = 0;
function changed() {
  const n = ++switching;  // rapid switching: only the last change renders
  setTimeout(() => { if (n === switching) { refreshMeta(); route(); refreshBadges(); } }, 120);
}

function setH(h) {
  state.h = h;
  document.querySelectorAll('#hSeg button').forEach((b) => b.setAttribute('aria-pressed', String(+b.dataset.h === h)));
  fillDates();
  changed();
}

function stepDate(dir) {
  const ds = state.boot.horizons[state.h].dates;
  const i = ds.indexOf(state.date) + dir;
  if (i >= 0 && i < ds.length) { state.date = ds[i]; document.getElementById('dateSel').value = state.date; changed(); }
}

function tick() {
  const t = new Date().toLocaleTimeString('en-GB', { timeZone: 'Africa/Kigali', hour: '2-digit', minute: '2-digit' });
  document.getElementById('clock').textContent = `${t} CAT`;
}

async function refreshBadges() {
  try {
    const alerts = await api.get(`/api/alerts?h=${state.h}&date=${state.date}`);
    const open = alerts.filter((a) => a.state === 'open').length;
    const bn = document.getElementById('bellN');
    bn.textContent = open; bn.hidden = !open;
    document.getElementById('bellBtn').title = open ? `${open} open alerts` : `No open alerts (${alerts.length} for this issue, ${alerts.filter((a) => a.expired).length} expired)`;
    const b = document.getElementById('navAlerts');
    const hi = alerts.length;
    if (b) { b.textContent = hi; b.hidden = !hi; }
    if (state.can('read:field')) {
      const s = await api.get('/api/field/summary');
      const t = (s.tasks.assigned || 0) + (s.tasks.in_progress || 0);
      const nt = document.getElementById('navTasks');
      if (nt) { nt.textContent = t; nt.hidden = !t; }
    }
  } catch (e) { /* badges are decoration */ }
}

function onboarding(force = false) {
  let seen = false;
  try { seen = localStorage.getItem('rinda.onboarded') === '2'; } catch (e) {}
  if (seen && !force) return;
  const d = modal('Welcome to RINDA', `
    <ol class="steps" style="margin:0;padding:0">
      <li><div><b>What it forecasts.</b> Whether new surface water will appear at each 50 m monitoring point 7 or 14 days ahead, from Sentinel-1 radar and station weather. It is a lead for inspection, not proof of larvae.</div></li>
      <li><div><b>How to read a site.</b> Every point carries a verification priority: <i>High</i>, <i>Insufficient evidence</i> (the model cannot tell, so verify) or <i>Lower</i>. Click any point for its evidence, inputs and model version.</div></li>
      <li><div><b>Check the stamp.</b> The banner and the chip next to the date tell you whether a forecast is current or stale. Stale forecasts are history, not guidance.</div></li>
      <li><div><b>From forecast to action.</b> Supervisors assign inspections, field officers record what they find, and only a verified field finding can lead to an authorised intervention.</div></li>
    </ol>
    <div class="actions"><button class="btn primary" id="obDone">Start</button><a class="btn" href="#/help" id="obHelp">${icon('help')}Open the guide</a></div>`);
  const done = () => { try { localStorage.setItem('rinda.onboarded', '2'); } catch (e) {} d.close(); };
  d.querySelector('#obDone').addEventListener('click', done);
  d.querySelector('#obHelp').addEventListener('click', done);
}
export const showOnboarding = () => onboarding(true);

// -------------------------------------------------------------------- boot
async function waitReady(msg) {
  const t0 = Date.now();
  for (;;) {
    const s = Math.round((Date.now() - t0) / 1000);
    try {
      const r = await fetch('/api/v1/ready', { cache: 'no-store' });
      if (r.ok) return true;
      msg.textContent = `Forecast engine loading models (${s} s)`;
    } catch (e) {
      msg.textContent = s < 5 ? 'Connecting to RINDA' : `Service waking up after inactivity (${s} s). This can take up to a minute on the hosted demonstration.`;
    }
    if (s > 180) return false;
    await new Promise((z) => setTimeout(z, 1500));
  }
}

async function boot() {
  const msg = document.getElementById('bootMsg');
  if (!(await waitReady(msg))) {
    msg.innerHTML = 'RINDA did not respond within three minutes. <a href="" onclick="location.reload()">Retry</a>';
    return;
  }
  msg.textContent = 'Loading operating picture';
  try {
    await loadUserSafe();
    state.boot = await api.get('/api/bootstrap');
  } catch (e) {
    msg.innerHTML = `${esc(e.message)} <button class="btn sm" onclick="location.reload()">Retry</button>`;
    if (e.status === 401) { document.getElementById('boot').remove(); document.getElementById('shell').hidden = false; authDialog(); }
    return;
  }
  const b = state.boot;
  state.date = b.horizons[7].dates.at(-1);
  document.getElementById('buildTag').textContent = `v${b.product.version}`;
  document.getElementById('modeChip').lastChild.textContent = b.mode === 'hindcast' ? 'Hindcast replay' : 'Operational';
  fillDates();

  document.querySelectorAll('#hSeg button').forEach((x) => x.addEventListener('click', () => setH(+x.dataset.h)));
  document.getElementById('dateSel').addEventListener('change', (e) => { state.date = e.target.value; changed(); });
  document.getElementById('datePrev').addEventListener('click', () => stepDate(-1));
  document.getElementById('dateNext').addEventListener('click', () => stepDate(1));
  document.getElementById('userChip').addEventListener('click', authDialog);
  document.getElementById('themeBtn').addEventListener('click', () => {
    const t = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = t;
    try { localStorage.setItem('rinda.theme', t); } catch (e) {}
    route();
  });
  document.getElementById('navToggle').addEventListener('click', () => document.getElementById('nav').classList.toggle('open'));
  document.getElementById('drawer').addEventListener('click', (e) => { if (e.target.closest('[data-close]')) drawer.close(); });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') drawer.close();
    if (e.target.matches('input, select, textarea')) return;
    if (e.key === '[') stepDate(-1);
    if (e.key === ']') stepDate(1);
  });
  const net = () => {
    const off = !navigator.onLine;
    const chip = document.getElementById('offlineChip');
    chip.hidden = !off && !queued();
    chip.textContent = off ? `Offline${queued() ? ` · ${queued()} queued` : ''}` : `${queued()} queued`;
    if (!off && queued()) flushQueue().then((n) => { if (n) { toast(`${n} queued observation(s) sent`); net(); } });
  };
  window.addEventListener('online', net); window.addEventListener('offline', net); window.addEventListener('rinda:queue', net);
  window.addEventListener('hashchange', route);
  window.openSite = (id, date) => openSite(id, date || state.date, state.h, state);
  tick(); setInterval(tick, 15000);

  document.getElementById('boot').remove();
  document.getElementById('shell').hidden = false;
  await refreshMeta();
  await route();
  refreshBadges();
  net();
  onboarding();
}

async function loadUserSafe() {
  try { await loadUser(); } catch (e) { state.user = { authenticated: false, permissions: [], role: 'none' }; throw e; }
}

boot();
export { fmt };
