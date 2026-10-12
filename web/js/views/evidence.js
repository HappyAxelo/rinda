import { api, card, fmt, icon, chart, css, series, table, statusTag } from '../ui.js';

const ROUTE = { 'Habitat Forecast': 'forecast', 'Mission Planning': 'missions', 'Flight Operations': 'flight', 'Scenario Lab': 'scenario', 'Model Assurance': 'assurance', 'Field Network': 'network', 'Impact & Economics': 'impact', Bulletins: 'bulletins', 'Data & Governance': 'governance' };
const link = (m) => `<a href="#/${ROUTE[m] || 'overview'}">${m}</a>`;

export async function render(el) {
  const ev = await api.get('/api/evidence');
  const r = ev.review, ch = ev.chain;
  const max = Math.max(...ch.map((c) => c.studies || 0));
  const s = r.strength || {};
  el.innerHTML = `
  <div class="page-head">
    <div><h1>Programme evidence</h1><p>Where the published evidence stops, and where this platform picks up. The systematic scoping review mapped ${r.n} studies on Earth observation, machine learning and sensing for malaria vector habitats against the steps from observing the environment to measuring an intervention's outcome.</p></div>
  </div>
  ${card('The evidence chain', `<div class="chain">${ch.map((c) => `
      <div class="chain-step ${c.status}">
        <span class="code">${c.code}</span><span class="nm">${c.name}</span>
        <span class="cnt">${c.studies ?? 0}<small>of ${r.n} studies</small></span>
        <div class="barv" style="width:${Math.max(3, (c.studies || 0) / max * 100)}%;${c.studies ? '' : 'background:var(--crit)'}"></div>
        ${statusTag(c.status)}
        <span style="font-size:11.5px;color:var(--ink-2)">${c.platform}</span>
        <span style="font-size:11.5px">${c.modules.map(link).join(' · ')}</span>
      </div>`).join('')}</div>`, { sub: 'Count = studies reaching that step', foot: `<span>Only ${r.target_categories['H Prospective forecasting'] ?? 3} studies forecast prospectively, ${r.target_categories['I Target verification'] ?? 2} verified predicted targets in the field and ${r.target_categories['J Targeted intervention'] ?? 1} used predictions to direct an intervention. ${r.intervention_linkage_field_trap ? `${r.intervention_linkage_field_trap.extraction_field_says_yes} studies claimed an intervention link; ${r.intervention_linkage_field_trap.actually_delivered_intervention} actually delivered one.` : ''}</span>` })}
  <div class="grid g-3" style="margin-top:14px">
    ${ev.objectives.map((o) => card(`${o.id} · ${o.paper}`, `<p style="margin:0 0 10px;font-weight:500">${o.title}</p>${statusTag(o.status)}
      <div class="kv-row" style="margin-top:10px"><span>Evidence now</span><span style="text-align:right;max-width:65%">${o.evidence}</span></div>
      <div class="kv-row"><span>Modules</span><span>${o.modules.map(link).join(' · ')}</span></div>
      <div class="kv-row"><span>Still needed</span><span style="text-align:right;max-width:65%">${o.open}</span></div>`)).join('')}
  </div>
  <div class="grid g-2" style="margin-top:14px">
    ${card('Research questions', table([
      { h: 'RQ', f: (q) => `<span class="mono">${q.id}</span>` }, { h: 'Question', k: 'question' }, { h: 'Answered in', f: (q) => link(q.module) }, { h: 'Status', f: (q) => statusTag(q.status) },
    ], ev.rqs).html, { flush: true })}
    ${card('How the reviewed studies validated their models', '<div class="chart lg" id="evVal"></div>', { sub: 'Share of studies', foot: '<span>The platform\'s own validation: forward temporal holdout, spatial blocks, reference-forecast comparison and radar-checked outcomes on every issue.</span>' })}
  </div>`;

  const dims = [['Temporal validation', s['Temporal validation']], ['Spatial validity', s['Spatial validity']], ['Field validation', s['Field validation']], ['Intervention linkage strength', s['Intervention linkage strength']]].filter((d) => d[1]);
  const keys = [...new Set(dims.flatMap(([, o]) => Object.keys(o)))];
  const weak = /Unclear|None|Random|Proposed|Same-location|reported only|Environmental/;
  chart('evVal', {
    grid: { left: 170, right: 16, top: 10, bottom: 24 },
    tooltip: { trigger: 'item', formatter: (p) => `${p.seriesName}<br>${dims[p.dataIndex][0]}: ${p.value} studies` },
    xAxis: { type: 'value', max: r.n, name: 'studies' }, yAxis: { type: 'category', data: dims.map((d) => d[0]) },
    series: keys.map((k) => ({ name: k, type: 'bar', stack: 's', barWidth: 18, data: dims.map(([, o]) => o[k] || 0),
      itemStyle: { color: weak.test(k) ? css('--ink-3') : css('--brand'), opacity: weak.test(k) ? (/Unclear/.test(k) ? .35 : .65) : 1, borderColor: css('--s1'), borderWidth: 1 } })),
  });
}
