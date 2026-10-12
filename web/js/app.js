import { api, disposeAll, drawer, toast, icon, fmt } from './ui.js';
import { openSite } from './site.js';

export const state = {
  boot: null, h: 7, date: null, role: 'pm',
  listeners: new Set(),
  dateFor(h) { const ds = this.boot.horizons[h].dates; return ds.filter((d) => d <= this.date).pop() || ds[0]; },
};

const ROUTES = [
  { group: 'Operations', items: [
    ['overview', 'Command Overview', 'overview'],
    ['forecast', 'Habitat Forecast', 'forecast'],
    ['missions', 'Mission Planning', 'mission'],
    ['flight', 'Flight Operations', 'flight'],
  ] },
  { group: 'Intelligence', items: [
    ['scenario', 'Scenario Lab', 'scenario'],
    ['assurance', 'Model Assurance', 'assurance'],
    ['network', 'Field Network', 'network'],
  ] },
  { group: 'Programme', items: [
    ['impact', 'Impact & Economics', 'impact'],
    ['bulletins', 'Bulletins', 'bulletin'],
    ['evidence', 'Programme Evidence', 'alignment'],
    ['governance', 'Data & Governance', 'governance'],
  ] },
];
const VIEWS = {
  overview: () => import('./views/overview.js'), forecast: () => import('./views/forecast.js'),
  missions: () => import('./views/missions.js'), flight: () => import('./views/flight.js'),
  scenario: () => import('./views/scenario.js'), assurance: () => import('./views/assurance.js'),
  network: () => import('./views/network.js'), impact: () => import('./views/impact.js'),
  bulletins: () => import('./views/bulletins.js'), evidence: () => import('./views/evidence.js'),
  governance: () => import('./views/governance.js'),
};
// what each role sees first; every role can still open every module
const ROLE_HOME = { pm: 'overview', dvco: 'forecast', pilot: 'missions', analyst: 'assurance' };

function navHTML() {
  return ROUTES.map((g) => `<div class="nav-group">${g.group}</div>` + g.items.map(([k, label, ic]) =>
    `<a class="nav-link" href="#/${k}" data-route="${k}">${icon(ic)}<span>${label}</span>${k === 'forecast' ? '<span class="badge hot" id="navAlerts" hidden></span>' : ''}${k === 'flight' ? '<span class="badge" id="navLogs" hidden></span>' : ''}</a>`).join('')).join('');
}

let current = null;
async function route() {
  const key = (location.hash.replace(/^#\/?/, '').split('?')[0]) || ROLE_HOME[state.role];
  const name = VIEWS[key] ? key : 'overview';
  document.querySelectorAll('.nav-link').forEach((a) => {
    if (a.dataset.route === name) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  });
  const grp = ROUTES.find((g) => g.items.some((i) => i[0] === name));
  document.getElementById('crumbGroup').textContent = grp.group;
  document.getElementById('crumbView').textContent = grp.items.find((i) => i[0] === name)[1];
  document.getElementById('nav').classList.remove('open');
  drawer.close();
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
    console.error(e);
    view.innerHTML = `<div class="note crit"><b>This module could not load.</b> ${e.message}</div>`;
  }
}
export const rerender = () => route();
export const params = () => new URLSearchParams(location.hash.split('?')[1] || '');

function fillDates() {
  const sel = document.getElementById('dateSel');
  const ds = state.boot.horizons[state.h].dates;
  if (!ds.includes(state.date)) state.date = state.dateFor(state.h);
  sel.innerHTML = [...ds].reverse().map((d) => `<option value="${d}">Issue ${d}</option>`).join('');
  sel.value = state.date;
}

function setH(h) {
  state.h = h;
  document.querySelectorAll('#hSeg button').forEach((b) => b.setAttribute('aria-pressed', String(+b.dataset.h === h)));
  fillDates();
  route();
}

function stepDate(dir) {
  const ds = state.boot.horizons[state.h].dates;
  const i = ds.indexOf(state.date) + dir;
  if (i >= 0 && i < ds.length) { state.date = ds[i]; document.getElementById('dateSel').value = state.date; route(); }
}

function tick() {
  const t = new Date().toLocaleTimeString('en-GB', { timeZone: 'Africa/Kigali', hour: '2-digit', minute: '2-digit' });
  document.getElementById('clock').textContent = `${t} CAT`;
}

async function refreshBadges() {
  try {
    const f = await api.get(`/api/forecast?h=${state.h}&date=${state.date}`);
    const n = f.summary.decisions.formation || 0;
    const b = document.getElementById('navAlerts');
    b.textContent = n; b.hidden = !n;
    const logs = await api.get('/api/flightlogs');
    const l = document.getElementById('navLogs');
    l.textContent = logs.length; l.hidden = !logs.length;
  } catch (e) { /* badges are decoration */ }
}

async function boot() {
  const msg = document.getElementById('bootMsg');
  let b;
  for (let i = 0; i < 60; i++) {
    try { b = await api.get('/api/bootstrap'); break; } catch (e) { msg.textContent = 'Forecast engine warming up'; await new Promise((r) => setTimeout(r, 1000)); }
  }
  if (!b) { msg.textContent = 'Engine unreachable. Start the server and reload.'; return; }
  msg.textContent = 'Loading operating picture';
  state.boot = b;
  try { state.role = localStorage.getItem('rinda.role') || 'pm'; } catch (e) {}
  state.date = b.horizons[7].dates.at(-1);
  document.getElementById('navlinks').innerHTML = navHTML();
  document.getElementById('buildTag').textContent = `v${b.product.version}`;
  document.getElementById('roleSel').value = state.role;
  document.getElementById('modeChip').lastChild.textContent = b.mode === 'hindcast' ? 'Hindcast replay' : 'Operational';
  fillDates();

  document.querySelectorAll('#hSeg button').forEach((x) => x.addEventListener('click', () => setH(+x.dataset.h)));
  document.getElementById('dateSel').addEventListener('change', (e) => { state.date = e.target.value; route(); refreshBadges(); });
  document.getElementById('datePrev').addEventListener('click', () => { stepDate(-1); refreshBadges(); });
  document.getElementById('dateNext').addEventListener('click', () => { stepDate(1); refreshBadges(); });
  document.getElementById('roleSel').addEventListener('change', (e) => {
    state.role = e.target.value;
    try { localStorage.setItem('rinda.role', state.role); } catch (er) {}
    toast(`Workspace switched to ${e.target.selectedOptions[0].text}`);
    location.hash = '#/' + ROLE_HOME[state.role];
  });
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
    if (e.key === '[') { stepDate(-1); refreshBadges(); }
    if (e.key === ']') { stepDate(1); refreshBadges(); }
  });
  window.addEventListener('hashchange', route);
  window.openSite = (id, date) => openSite(id, date || state.date, state.h);
  tick(); setInterval(tick, 15000);

  document.getElementById('boot').remove();
  document.getElementById('shell').hidden = false;
  await route();
  refreshBadges();
}

boot();
export { fmt };
