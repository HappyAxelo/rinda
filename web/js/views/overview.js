// National situation overview. Every number is a count of records; a count with
// no records behind it reads "No data available".
import { api, card, icon, esc, fmt, stat, pill, auditNote, empty, makeMap, css, opStatus } from '../ui.js';

let map;
export function destroy() { map?.remove(); map = null; }

const F = { start: '', end: '', province: '', district: '', sector: '', cell: '', source: '', evidence: '' };
const PROVINCES = ['Kigali City', 'Eastern', 'Northern', 'Southern', 'Western'];

export async function render(el, st) {
  const areas = await api.get('/api/geo/areas');
  const cells = areas.filter((a) => a.level === 'cell');
  const qs = () => Object.entries(F).filter(([, v]) => v).map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&');
  el.innerHTML = `
  <div class="page-head">
    <div><h1>National situation overview</h1><p>Where surveillance is taking place, what evidence is held, and what needs attention. Counts come only from records in RINDA.</p></div>
    <div class="actions"><a class="btn primary" href="#/map">${icon('map')}Open surveillance map</a></div>
  </div>
  <form class="filterbar" id="ovF" aria-label="Filters">
    <div class="field"><label for="fS">From</label><input type="date" id="fS" value="${F.start}"></div>
    <div class="field"><label for="fE">To</label><input type="date" id="fE" value="${F.end}"></div>
    <div class="field"><label for="fP">Province</label><select id="fP"><option value="">All of Rwanda</option>${PROVINCES.map((p) => `<option ${F.province === p ? 'selected' : ''}>${p}</option>`).join('')}</select></div>
    <div class="field"><label for="fD">District</label><select id="fD"><option value="">All districts</option><option ${F.district === 'Gasabo' ? 'selected' : ''}>Gasabo</option></select></div>
    <div class="field"><label for="fSe">Sector</label><select id="fSe"><option value="">All sectors</option><option ${F.sector === 'Gikomero' ? 'selected' : ''}>Gikomero</option></select></div>
    <div class="field"><label for="fC">Cell</label><select id="fC" ${cells.length ? '' : 'disabled title="Cell boundaries are not loaded yet"'}><option value="">${cells.length ? 'All cells' : 'Not available'}</option>${cells.map((c) => `<option ${F.cell === c.name ? 'selected' : ''}>${esc(c.name)}</option>`).join('')}</select></div>
    <div class="field"><label for="fSo">Data source</label><select id="fSo"><option value="">All sources</option>${[['earth_observation', 'Earth observation'], ['iot', 'IoT nodes'], ['meteorology', 'Meteorology'], ['entomology', 'Entomology'], ['drone', 'Drone records']].map(([k, l]) => `<option value="${k}" ${F.source === k ? 'selected' : ''}>${l}</option>`).join('')}</select></div>
    <div class="field"><label for="fEv">Evidence status</label><select id="fEv"><option value="">Any status</option>${Object.entries(st.boot.status_labels).map(([k, l]) => `<option value="${k}" ${F.evidence === k ? 'selected' : ''}>${l}</option>`).join('')}</select></div>
    <button type="button" class="btn ghost" id="fReset">${icon('reset')}Reset</button>
  </form>
  <div id="ovBody"></div>`;
  const read = () => { F.start = v('fS'); F.end = v('fE'); F.province = v('fP'); F.district = v('fD'); F.sector = v('fSe'); F.cell = v('fC'); F.source = v('fSo'); F.evidence = v('fEv'); };
  const v = (id) => document.getElementById(id).value;
  el.querySelector('#ovF').addEventListener('change', () => { read(); load(); });
  el.querySelector('#fReset').addEventListener('click', () => { Object.keys(F).forEach((k) => { F[k] = ''; }); render(el, st); });

  async function load() {
    const body = document.getElementById('ovBody');
    const o = await api.get(`/api/overview?${qs()}`);
    const c = o.counts, cov = o.coverage;
    const where = cov.onboarded ? '' : `<div class="note warn" style="margin-bottom:18px">${icon('info')} <b>${esc(cov.area)} is not onboarded.</b> ${esc(cov.note)}</div>`;
    body.innerHTML = `${where}
    <div class="stats">
      ${stat('Geographic coverage', cov.onboarded ? `${cov.operational_sectors} sector` : null, cov.onboarded ? 'Gikomero, Gasabo District. Pilot only.' : '', 'map')}
      ${stat('Registered monitoring locations', c.registered_locations, cov.onboarded && !F.cell ? `${cov.sites_in_sector} inside the sector, ${cov.sites_outside_sector} around it` : '', 'pin')}
      ${stat('Environmental observations held', c.environmental_observations, 'Radar passes, station days and sensor readings', 'layers', 'blue')}
      ${stat('Inspections pending', c.inspections_pending, '', 'cliplist', 'amber')}
      ${stat('Verified water habitats', c.verified_water, 'Water confirmed by a reviewed field visit', 'droplet', 'blue')}
      ${stat('Confirmed positive habitats', c.confirmed_positive, 'Anopheles larvae found by dipping', 'alert', 'terra')}
      ${stat('Interventions authorised', c.interventions_authorised, '', 'shield')}
      ${stat('Treatments completed', c.treatments_completed, '', 'check')}
      ${stat('Follow-up due', c.follow_up_due, 'Treated, with no follow-up scheduled', 'replay', 'amber')}
    </div>
    <p class="hint" style="margin:10px 2px 18px">${esc(o.scope)}. "${esc(o.no_data_label)}" means RINDA holds no records for that count; it is never shown as zero.</p>
    <div class="grid g-main">
      ${card('Surveillance area', '<div class="map-host"><div class="map mid" id="ovMap" role="region" aria-label="Map preview of the surveillance area"></div></div>', { flush: true, sub: 'Gikomero Sector boundary and monitoring locations', foot: `<span>Sector boundary: geoBoundaries, from NISR 2012. Cell boundaries are not loaded.</span><a href="#/map">Open full map ${icon('arrow')}</a>` })}
      <div class="grid" style="align-content:start">
        ${card('Action required', o.actions.length ? o.actions.map((a) => `<a class="action-row ${a.tone}" href="${a.route}"><span class="n">${a.n}</span><span class="t">${esc(a.label)}</span>${icon('chevR')}</a>`).join('') : empty('Nothing needs attention', st.user.authenticated ? 'No task, review or approval is waiting for your role.' : 'Sign in to see tasks for your role.', 'check'), { flush: true })}
        ${card('Data freshness', o.freshness.map((f) => `<div class="fresh-row"><span class="nm">${esc(f.name)}</span>${pill(f.label)}<span class="d">${f.last_observation ? `Latest record ${fmt.date(f.last_observation.slice(0, 10))}` : 'No records received'} · ${f.mode === 'historical' ? 'historical archive, no live connection' : 'recorded in RINDA'}</span></div>`).join('') || empty('No source selected'), { flush: true, sub: 'Each source has its own status', foot: '<span>RINDA is not real-time. Historical means a stored archive.</span>' })}
      </div>
    </div>
    <div class="grid g-2" style="margin-top:18px">
      ${card('Evidence status of inspected locations', Object.keys(o.status_counts).length ? `<div class="list">${Object.entries(o.status_counts).map(([k, n]) => `<div><span>${opStatus(k, o.status_labels[k])}</span><b>${n}</b></div>`).join('')}</div>` : empty('No field activity recorded', 'Statuses appear once inspections are assigned and observations recorded.'), { flush: true })}
      ${card('Forecast status', `${auditNote(o.audit)}<p style="margin:12px 0 0;color:var(--ink-2)">Forecasts are shown as research-only leads on the surveillance map. They are not counted above and never authorise treatment.</p>`)}
    </div>`;
    drawMap(st, o, cells);
  }

  async function drawMap(st2, o, cellList) {
    map?.remove();
    map = makeMap('ovMap', { zoom: 12 });
    const b = await api.get('/api/geo/boundaries');
    const sector = L.geoJSON(b.sector, { style: { color: css('--brand'), weight: 2.5, fillColor: css('--brand'), fillOpacity: 0.04 }, interactive: false }).addTo(map);
    L.geoJSON({ type: 'FeatureCollection', features: b.cells }, {
      style: (f) => ({ color: css('--sage'), weight: 1.5, dashArray: '4 4', fillColor: css('--sage'), fillOpacity: F.cell === f.properties.name ? 0.28 : 0.05 }),
      onEachFeature: (f, l) => {
        const info = cellList.find((c) => c.name === f.properties.name);
        l.bindTooltip(`<b>${esc(f.properties.name)} Cell</b><br>${info?.n_sites ?? 0} monitoring locations`, { sticky: true });
        l.on('click', () => { F.cell = F.cell === f.properties.name ? '' : f.properties.name; document.getElementById('fC').value = F.cell; load(); });
      },
    }).addTo(map);
    st2.boot.sites.forEach((s) => L.circleMarker([s.latitude, s.longitude], { radius: 2.2, weight: 0, fillColor: css('--ink-3'), fillOpacity: 0.55, interactive: false }).addTo(map));
    if (o.coverage.onboarded) map.fitBounds(sector.getBounds(), { padding: [16, 16] });
    else map.setView([-1.94, 29.87], 8);
  }

  await load();
}
