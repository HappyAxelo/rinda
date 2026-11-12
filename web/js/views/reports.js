// Monitoring, impact and reporting: four indicator groups, counted from records.
// One measure per chart; an indicator with no records reads "No data available".
import { api, card, icon, esc, fmt, chart, css, empty, table, NODATA } from '../ui.js';

const R = { start: '', end: '' };
const ICON = { operational: 'cliplist', environmental: 'layers', entomological: 'droplet', programme: 'chart' };

function tile(i) {
  let v;
  if (i.value == null) v = `<div class="v none">${NODATA}</div>`;
  else if (i.unit === 'share') v = `<div class="v">${fmt.pct(i.value, 0)}</div><div class="m">${i.numerator} of ${i.denominator}${i.denominator < 20 ? ' · few records' : ''}</div>`;
  else v = `<div class="v">${fmt.n(i.value, Number.isInteger(i.value) ? 0 : 1)}<small style="font-size:13px;font-weight:500;color:var(--ink-3);margin-left:4px">${['count', 'sectors'].includes(i.unit) ? '' : esc(i.unit)}</small></div>`;
  return `<section class="stat" style="min-height:104px"><div class="l">${esc(i.label)}</div>${v}${i.note ? `<div class="m">${esc(i.note)}</div>` : ''}</section>`;
}

export async function render(el, st) {
  const q = () => [R.start && `start=${R.start}`, R.end && `end=${R.end}`].filter(Boolean).join('&');
  el.innerHTML = `
  <div class="page-head">
    <div><h1>Monitoring, impact and reporting</h1><p>Programme activity and surveillance findings, from recorded data.</p></div>
    <div class="actions" id="rpActions"></div>
  </div>
  <form class="filterbar" id="rpF"><div class="field"><label for="rpS">From</label><input type="date" id="rpS" value="${R.start}"></div><div class="field"><label for="rpE">To</label><input type="date" id="rpE" value="${R.end}"></div>
    <button type="button" class="btn ghost" id="rpAll">${icon('reset')}All records</button><span class="spacer"></span><span class="hint" id="rpScope"></span></form>
  <div id="rpBody"></div>`;
  el.querySelector('#rpF').addEventListener('change', () => { R.start = el.querySelector('#rpS').value; R.end = el.querySelector('#rpE').value; load(); });
  el.querySelector('#rpAll').addEventListener('click', () => { R.start = ''; R.end = ''; render(el, st); });

  async function load() {
    const r = await api.get(`/api/reports/indicators?${q()}`);
    el.querySelector('#rpScope').textContent = r.scope;
    el.querySelector('#rpActions').innerHTML = st.can('export:report')
      ? `<a class="btn" href="/api/v1/reports/indicators.csv?${q()}">${icon('download')}CSV</a><a class="btn" href="/api/v1/reports/indicators.pdf?${q()}">${icon('file')}PDF report</a><a class="btn" href="#/programme?tab=weekly">${icon('bulletin')}Weekly report and DHIS2</a>`
      : `<a class="btn" href="#/bulletins">${icon('bulletin')}Bulletins</a>`;
    const cells = Object.entries(r.by_cell);
    const sp = Object.entries(r.species);
    document.getElementById('rpBody').innerHTML = `
      <div class="note" style="margin-bottom:18px">${icon('info')} ${esc(r.claim)}</div>
      ${r.groups.map((g) => `<section style="margin-bottom:22px"><h2 style="font-size:18px;margin:0 0 12px;display:flex;gap:8px;align-items:center">${icon(ICON[g.key])}${esc(g.label)}</h2><div class="stats">${g.indicators.map(tile).join('')}</div></section>`).join('')}
      <div class="grid g-2">
        ${card('Radar dry-to-wet transitions per month', r.trends.months.length ? '<div class="chart" id="rpOn"></div>' : empty('No radar passes in this period'), { sub: 'Satellite-observed', foot: '<span>Counts rise from 2025 partly because Sentinel-1C is not harmonised. Read as a signal count, not as flooding.</span>' })}
        ${card('Rainfall per month (mm)', r.trends.rainfall_mm.some((x) => x != null) ? '<div class="chart" id="rpRain"></div>' : empty('No station record in this period'), { sub: 'Gikomero station', foot: '<span>The station record ends on 15 January 2026. Later months are missing, not dry.</span>' })}
        ${card('Species identified', sp.length ? '<div class="chart" id="rpSp"></div>' : empty('No species identified yet', 'Species appear here once field officers or RBC laboratory records identify them.'), { sub: 'Field observations' })}
        ${card('Inspections by cell', cells.length ? table([{ h: 'Cell', k: 0, f: (x) => esc(x[0]) }, { h: 'Inspections', r: 1, f: (x) => x[1].inspections }, { h: 'Larvae found', r: 1, f: (x) => x[1].positive }], cells, { max: 300 }).html : empty('No inspections recorded'), { flush: cells.length > 0, sub: 'Geographic summary' })}
      </div>`;
    const bar = (id, data, color) => chart(id, { grid: { left: 46, right: 12, top: 12, bottom: 30 }, xAxis: { type: 'category', data: r.trends.months }, yAxis: { type: 'value' },
      series: [{ type: 'bar', barMaxWidth: 14, data, itemStyle: { color, borderRadius: [4, 4, 0, 0] } }] });
    if (r.trends.months.length) bar('rpOn', r.trends.radar_onsets, css('--s-2'));
    if (r.trends.rainfall_mm.some((x) => x != null)) bar('rpRain', r.trends.rainfall_mm, css('--s-1'));
    if (sp.length) chart('rpSp', { grid: { left: 130, right: 30, top: 8, bottom: 24 }, tooltip: { trigger: 'item' }, xAxis: { type: 'value', minInterval: 1 }, yAxis: { type: 'category', data: sp.map((x) => x[0].replace(/_/g, ' ')) },
      series: [{ type: 'bar', barMaxWidth: 14, data: sp.map((x) => x[1]), itemStyle: { color: css('--s-1'), borderRadius: [0, 4, 4, 0] }, label: { show: true, position: 'right', color: css('--ink-2') } }] });
  }
  await load();
}
