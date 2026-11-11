import { api, disposeAll, drawer, toast, icon, fmt, esc, newScope, modal, banner, auditNote } from './ui.js';
import { flushQueue, queued } from './offline.js';
import { t, getLang, setLang } from './i18n.js';

export const state = {
  boot: null, h: 7, date: null, user: null, meta: null,
  dateFor(h) { const ds = this.boot.horizons[h].dates; return ds.filter((d) => d <= this.date).pop() || ds[0]; },
  can(p) { return !!this.user?.permissions?.includes(p); },
};

// Six modules. Each has tabs; a tab is [route, label, permission?]. Old routes stay valid.
const MODULES = [
  { key: 'overview', label: 'National Situation Overview', short: 'Overview', icon: 'dashboard', tabs: [['overview', 'Overview']] },
  { key: 'map', label: 'National Surveillance Map', short: 'Surveillance map', icon: 'map', tabs: [
    ['map', 'Map'], ['forecast', 'Forecast table'], ['issue', 'Forecast issue summary'], ['network', 'Field network'], ['dossier', 'Habitat dossier']] },
  { key: 'field', label: 'Field Surveillance & Operations', short: 'Field operations', icon: 'cliplist', perm: 'read:field', tabs: [['field', 'Field workspace', 'read:field']] },
  { key: 'drone', label: 'Drone Operations', short: 'Drone operations', icon: 'send', tabs: [
    ['drone', 'Operations'], ['missions', 'Inspection flight planner'], ['flight', 'Flight performance']] },
  { key: 'reports', label: 'Monitoring, Impact & Reporting', short: 'Reporting', icon: 'chart', tabs: [
    ['reports', 'Indicators', 'read:me'], ['programme', 'RE-AIM, weekly report and DHIS2', 'read:me'], ['bulletins', 'Bulletins'], ['impact', 'Costs and planning estimates']] },
  { key: 'admin', label: 'Administration & Evidence', short: 'Admin and evidence', icon: 'sliders', tabs: [
    ['admin', 'Users and system', 'admin:system'], ['models', 'Data and models', 'read:models'], ['assurance', 'Model assurance'], ['replay', 'Historical replay'],
    ['scenario', 'Scenario lab'], ['evidence', 'Programme evidence'], ['governance', 'Data provenance'], ['help', 'Help and glossary']] },
];
const VIEWS = {
  overview: () => import('./views/overview.js'), map: () => import('./views/map.js'), dossier: () => import('./views/dossier.js'),
  forecast: () => import('./views/forecast.js'), issue: () => import('./views/issue.js'),
  field: () => import('./views/field.js'), drone: () => import('./views/drone.js'), missions: () => import('./views/missions.js'),
  flight: () => import('./views/flight.js'), replay: () => import('./views/replay.js'),
  scenario: () => import('./views/scenario.js'), assurance: () => import('./views/assurance.js'),
  network: () => import('./views/network.js'), impact: () => import('./views/impact.js'), reports: () => import('./views/reports.js'),
  bulletins: () => import('./views/bulletins.js'), evidence: () => import('./views/evidence.js'),
  governance: () => import('./views/governance.js'), admin: () => import('./views/admin.js'), models: () => import('./views/models.js'),
  help: () => import('./views/help.js'), programme: () => import('./views/programme.js'),
};
// Pages that read the forecast issue selected in the top bar.
const FORECAST_ROUTES = new Set(['map', 'forecast', 'issue', 'missions', 'replay', 'scenario', 'bulletins', 'field', 'impact']);
const ROLE_HOME = { viewer: 'overview', field_officer: 'field', analyst: 'map', supervisor: 'overview', programme_manager: 'overview', admin: 'overview' };

const allowed = (tab) => !tab[2] || state.can(tab[2]);
const tabsOf = (m) => m.tabs.filter(allowed);
const moduleOf = (route) => MODULES.find((m) => m.tabs.some((x) => x[0] === route));

function navHTML() {
  return MODULES.filter((m) => (!m.perm || state.can(m.perm)) && tabsOf(m).length).map((m) => {
    const first = tabsOf(m)[0][0];
    return `<a class="nav-link" href="#/${first}" data-module="${m.key}" title="${esc(t(m.label))}">${icon(m.icon)}<span>${t(m.label)}</span>${m.key === 'field' ? '<span class="badge" id="navTasks" hidden></span>' : ''}</a>`;
  }).join('');
}

let current = null;
async function route() {
  const key = (location.hash.replace(/^#\/?/, '').split('?')[0]) || ROLE_HOME[state.user?.role] || 'overview';
  let name = VIEWS[key] ? key : 'overview';
  let mod = moduleOf(name);
  const tab = mod.tabs.find((x) => x[0] === name);
  if (!allowed(tab)) {
    toast(state.user?.authenticated ? 'Your role does not include that page.' : 'Sign in to open that page.', true);
    name = 'overview'; mod = moduleOf(name);
  }
  document.querySelectorAll('.nav-link').forEach((a) => {
    if (a.dataset.module === mod.key) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  });
  const label = mod.tabs.find((x) => x[0] === name)[1];
  document.getElementById('crumbGroup').textContent = t(mod.label);
  document.getElementById('crumbView').textContent = label;
  document.title = `${label} · RINDA`;
  const sub = document.getElementById('subnav');
  const tabs = tabsOf(mod).filter((x) => x[0] !== 'dossier' || name === 'dossier');
  sub.hidden = tabs.length < 2;
  sub.innerHTML = tabs.map(([k, l]) => `<a href="#/${k}" ${k === name ? 'aria-current="page"' : ''}>${esc(l)}</a>`).join('');
  // field officers never act on forecast alerts, so their workspace skips the forecast controls
  document.getElementById('fcCtx').hidden = !FORECAST_ROUTES.has(name) || (name === 'field' && !state.can('act:alert'));
  document.getElementById('nav').classList.remove('open');
  renderBanners(name);
  drawer.close();
  newScope();
  const view = document.getElementById('view');
  disposeAll();
  current?.destroy?.();
  view.innerHTML = '<div class="skeleton" style="height:34px;width:300px;margin-bottom:20px"></div><div class="stats">' + '<div class="skeleton" style="height:112px;border-radius:12px"></div>'.repeat(4) + '</div>';
  try {
    const m = await VIEWS[name]();
    current = m;
    await m.render(view, state);
    view.focus({ preventScroll: true });
  } catch (e) {
    if (e.cancelled) return;
    console.error(e);
    view.innerHTML = `<div class="note crit" role="alert"><b>This page could not load.</b> ${esc(e.message)}
      <div style="margin-top:10px"><button class="btn sm" id="retryView">Try again</button></div></div>`;
    document.getElementById('retryView')?.addEventListener('click', route);
  }
}
export const rerender = () => route();
export const params = () => new URLSearchParams(location.hash.split('?')[1] || '');

// ---------------------------------------------------------------- banners
async function refreshMeta() {
  try { state.meta = await api.get(`/api/forecast/meta?h=${state.h}&date=${state.date}`); } catch (e) { if (!e.cancelled) state.meta = null; }
}

function renderBanners(name) {
  const m = state.meta;
  const out = [];
  if (getLang() === 'rw') {
    out.push(banner('warn', `${icon('info')}<div><b>Ikinyarwanda: ${t('Draft translation')}.</b> Navigation and field screens are in draft Kinyarwanda that RBC and district teams have not yet reviewed. Other pages stay in English.</div>`));
  }
  if (state.offlineBoot) {
    out.push(banner('warn', `${icon('alert')}<div><b>Offline.</b> Showing the copy saved on this device the last time it was online. New observations are kept on this device and sent when the connection returns.</div>`));
  }
  if (state.boot?.demo && name !== 'map') {
    out.push(banner('warn', `${icon('info')}<div><b>Demonstration environment.</b> Anything you create here is stored and labelled as a demonstration record.</div>`));
  }
  if (FORECAST_ROUTES.has(name) && name !== 'field') {
    out.push(auditNote(state.boot?.audit));
    if (m && name !== 'map') {
      const stale = m.warnings.find((w) => w.code === 'stale_forecast');
      if (stale) out.push(banner('critical', `${icon('clock')}<div><b>Forecast stale.</b> ${esc(stale.message)} Latest issue in the archive: ${fmt.date(m.latest_issue)}. No current satellite or weather inputs are connected, so no new forecast is being produced.</div>`));
      const lh = m.warnings.find((w) => w.code === 'long_horizon');
      if (lh) out.push(banner('warn', `${icon('info')}<div>${esc(lh.message)}</div>`));
    }
  }
  if (m) {
    const fc = document.getElementById('freshChip');
    fc.hidden = false;
    fc.className = 'chip ' + (m.status === 'stale' ? 'bad' : 'ok');
    fc.lastChild.textContent = m.status === 'stale' ? `Forecast stale · ${m.age_days} d old` : 'Current';
    fc.title = `Issued ${m.issue_date}; valid ${m.valid_window[0]} to ${m.valid_window[1]}`;
  }
  document.getElementById('banners').innerHTML = out.join('');
}

// -------------------------------------------------------------------- auth
async function loadUser() {
  state.user = await api.get('/api/auth/me');
  const u = state.user;
  document.getElementById('userName').textContent = u.authenticated ? u.name : t('Sign in');
  document.getElementById('userRole').innerHTML = u.authenticated ? `${esc(u.role_label)}${u.demo ? ' <span class="demo">DEMO</span>' : ''}` : '';
  document.getElementById('userChip').title = u.role_title || '';
  if (u.authenticated && u.language && u.language !== getLang()) setLang(u.language);
  paintLang();
  document.getElementById('navlinks').innerHTML = navHTML();
}

function authDialog() {
  const u = state.user;
  if (u?.authenticated) {
    const d = modal('Account', `
      <dl class="dl"><dt>Name</dt><dd>${esc(u.name)}</dd><dt>Role</dt><dd>${esc(u.role_label)}<br><span class="hint">${esc(u.role_title || '')} <i>(proposed RBC mapping)</i></span></dd>${u.district ? `<dt>District</dt><dd>${esc(u.district)}</dd>` : ''}<dt>Email</dt><dd class="mono">${esc(u.email)}</dd>${u.demo ? '<dt>Account</dt><dd>Demonstration</dd>' : ''}</dl>
      <details class="tech"><summary>Permissions</summary><div>${u.permissions.map((p) => `<span class="badge">${p}</span>`).join(' ')}</div></details>
      ${u.demo ? '' : `<details class="tech"><summary>Change password</summary><div><form id="pwForm" class="form" style="margin-top:10px">
        <div class="field"><label for="pwC">Current password</label><input id="pwC" type="password" autocomplete="current-password" required></div>
        <div class="field"><label for="pwN">New password</label><input id="pwN" type="password" autocomplete="new-password" minlength="12" required><span class="hint">At least 12 characters, upper- and lower-case letters and a digit.</span></div>
        <button class="btn primary">Update password</button></form></div></details>`}
      <div class="actions"><button class="btn" id="signOut">${icon('x')}Sign out</button></div>`);
    d.querySelector('#signOut').addEventListener('click', async () => {
      await api.post('/api/auth/logout');
      try { await caches.delete('rinda-api-v2'); } catch (e) { /* no cache storage */ }
      d.close(); await afterAuth('Signed out');
    });
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
      <p id="lgErr" role="alert" style="margin:0;color:var(--crit);font-size:13px"></p>
      <button class="btn primary">Sign in</button>
    </form>
    ${u?.demo_enabled ? `<div class="fieldset">
      <b style="font-size:13.5px">Explore with a demonstration role</b>
      <p style="margin:4px 0 10px;font-size:12.5px;color:var(--ink-3)">No password. Everything you create is stored as a demonstration record.</p>
      <div class="actions">${u.roles.map((r) => `<button class="btn sm" data-demo="${r.role}" title="${esc(r.title || '')}">${r.label}</button>`).join('')}</div></div>` : ''}
    <p class="hint" style="margin:0">Accounts are issued by your institution's RINDA administrator.${u?.public_read ? ' Forecast maps and validation can be viewed without signing in; field records cannot.' : ''}</p>`);
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
  sel.innerHTML = [...ds].reverse().map((d, i) => `<option value="${d}">Issued ${d}${i === 0 ? ' (latest)' : ''}</option>`).join('');
  sel.value = state.date;
}

let switching = 0;
function changed() {
  const n = ++switching;  // rapid switching: only the last change renders
  setTimeout(async () => { if (n === switching) { await refreshMeta(); route(); } }, 120);
}
export function setIssue(date) {
  if (!state.boot.horizons[state.h].dates.includes(date) || date === state.date) return;
  state.date = date; document.getElementById('dateSel').value = date; refreshMeta().then(() => renderBanners(currentRoute()));
}
const currentRoute = () => location.hash.replace(/^#\/?/, '').split('?')[0] || 'overview';

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

export async function refreshBadges() {
  try {
    const o = await api.get('/api/overview');
    const n = o.actions.reduce((a, x) => a + x.n, 0);
    const bn = document.getElementById('bellN');
    bn.textContent = n; bn.hidden = !n;
    document.getElementById('bellBtn').title = n ? `${n} items need attention` : 'Nothing needs attention';
    if (state.can('read:field')) {
      const s = await api.get('/api/field/summary');
      const k = (s.tasks.assigned || 0) + (s.tasks.in_progress || 0);
      const nt = document.getElementById('navTasks');
      if (nt) { nt.textContent = k; nt.hidden = !k; }
    }
  } catch (e) { /* badges are decoration */ }
}

function onboarding(force = false) {
  let seen = false;
  try { seen = localStorage.getItem('rinda.onboarded') === '3'; } catch (e) {}
  if (seen && !force) return;
  const d = modal('Welcome to RINDA', `
    <ol class="steps" style="margin:0;padding:0">
      <li><div><b>One map, several kinds of evidence.</b> Satellite, sensor, weather, field and operational records sit on the same map. Each is labelled with where it came from and when.</div></li>
      <li><div><b>A forecast is a lead, not a finding.</b> It suggests where new surface water may appear. It is research-only, and it never confirms mosquito breeding.</div></li>
      <li><div><b>Only field work confirms a habitat.</b> An officer inspects and dips, a supervisor reviews, and only a verified finding can lead to an authorised treatment.</div></li>
      <li><div><b>"No data available" means exactly that.</b> RINDA never shows a zero or an estimate in place of a missing record.</div></li>
    </ol>
    <div class="actions"><button class="btn primary" id="obDone">Start</button><a class="btn" href="#/help" id="obHelp">${icon('help')}Open the guide</a></div>`);
  const done = () => { try { localStorage.setItem('rinda.onboarded', '3'); } catch (e) {} d.close(); };
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
      msg.textContent = `Loading models (${s} s)`;
    } catch (e) {
      msg.textContent = s < 5 ? 'Connecting to RINDA' : `Service waking up after inactivity (${s} s). This can take up to a minute on the hosted demonstration.`;
    }
    if (s > 180) return false;
    await new Promise((z) => setTimeout(z, 1500));
  }
}

function paintLang() {
  document.querySelectorAll('#langSeg button').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.lang === getLang())));
}
async function chooseLang(l) {
  setLang(l);
  paintLang();
  if (state.user?.authenticated) { try { await api.post('/api/auth/preferences', { language: l }); state.user.language = l; } catch (e) { /* saved locally anyway */ } }
  document.getElementById('navlinks').innerHTML = navHTML();
  document.getElementById('userName').textContent = state.user?.authenticated ? state.user.name : t('Sign in');
  route();
}

// Offline app shell: a service worker keeps the console and the last answers to a
// few read requests (bootstrap, account, tasks) on the device.
function registerShell() {
  if (!('serviceWorker' in navigator) || location.protocol === 'file:') return;
  navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(() => { /* offline shell is optional */ });
  const warm = () => ['/api/v1/bootstrap', '/api/v1/auth/me', '/api/v1/tasks', '/api/v1/habitats', '/api/v1/field/summary', '/api/v1/overview']
    .forEach((u) => fetch(u, { credentials: 'same-origin' }).catch(() => {}));
  navigator.serviceWorker.addEventListener('controllerchange', warm, { once: true });
}

async function boot() {
  const msg = document.getElementById('bootMsg');
  registerShell();
  if (!navigator.onLine) {
    state.offlineBoot = true;
  } else if (!(await waitReady(msg))) {
    msg.innerHTML = 'RINDA did not respond within three minutes. <a href="" onclick="location.reload()">Retry</a>';
    return;
  }
  msg.textContent = 'Loading the situation overview';
  try {
    await loadUserSafe();
    state.boot = await api.get('/api/bootstrap');
  } catch (e) {
    msg.innerHTML = state.offlineBoot
      ? 'You are offline and this device has no saved copy of RINDA yet. Open RINDA once while connected, then it will start without a connection. <button class="btn sm" onclick="location.reload()">Retry</button>'
      : `${esc(e.message)} <button class="btn sm" onclick="location.reload()">Retry</button>`;
    if (e.status === 401) { document.getElementById('boot').remove(); document.getElementById('shell').hidden = false; authDialog(); }
    return;
  }
  const b = state.boot;
  state.date = b.horizons[7].dates.at(-1);
  document.getElementById('buildTag').textContent = `v${b.product.version}`;
  fillDates();

  document.querySelectorAll('#hSeg button').forEach((x) => x.addEventListener('click', () => setH(+x.dataset.h)));
  document.getElementById('dateSel').addEventListener('change', (e) => { state.date = e.target.value; changed(); });
  document.getElementById('datePrev').addEventListener('click', () => stepDate(-1));
  document.getElementById('dateNext').addEventListener('click', () => stepDate(1));
  document.getElementById('userChip').addEventListener('click', authDialog);
  document.querySelectorAll('#langSeg button').forEach((x) => x.addEventListener('click', () => chooseLang(x.dataset.lang)));
  const shell = document.getElementById('shell');
  try { if (localStorage.getItem('rinda.nav') === 'collapsed') shell.classList.add('collapsed'); } catch (e) {}
  document.getElementById('navCollapse').addEventListener('click', () => {
    const c = shell.classList.toggle('collapsed');
    try { localStorage.setItem('rinda.nav', c ? 'collapsed' : 'open'); } catch (e) {}
    window.dispatchEvent(new Event('resize'));
  });
  document.getElementById('navToggle').addEventListener('click', () => document.getElementById('nav').classList.toggle('open'));
  document.getElementById('navScrim').addEventListener('click', () => document.getElementById('nav').classList.remove('open'));
  document.getElementById('drawer').addEventListener('click', (e) => { if (e.target.closest('[data-close]')) drawer.close(); });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { drawer.close(); document.getElementById('nav').classList.remove('open'); }
    if (e.target.matches('input, select, textarea') || document.getElementById('fcCtx').hidden) return;
    if (e.key === '[') stepDate(-1);
    if (e.key === ']') stepDate(1);
  });
  const net = () => {
    const off = !navigator.onLine;
    const chip = document.getElementById('offlineChip');
    chip.hidden = !off && !queued();
    chip.textContent = off ? `Offline${queued() ? ` · ${queued()} waiting to send` : ''}` : `${queued()} waiting to send`;
    if (!off && queued()) flushQueue().then((n) => { if (n) { toast(`${n} saved observation(s) sent`); net(); } });
  };
  window.addEventListener('online', net); window.addEventListener('offline', net); window.addEventListener('rinda:queue', net);
  window.addEventListener('hashchange', route);
  // One entry point for "show me this place", used by every view.
  window.openSite = async (id) => { const m = await import('./views/dossier.js'); m.openDossierDrawer(id, state); };

  document.getElementById('boot').remove();
  shell.hidden = false;
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
