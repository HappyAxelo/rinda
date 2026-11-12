// Research alignment: the gap matrix, how each objective maps to a module, and the
// assumptions the evidence so far has challenged. Content comes from
// config/research_alignment.json; nothing here is computed or invented.
import { api, card, icon, esc, empty } from '../ui.js';

const ST = { Preliminary: 'st-warn', 'Awaiting data': 'st-mute', Built: 'st-info', Interface: 'st-info', Awaiting: 'st-mute', 'Project finding': 'st-warn', Mission: 'st-info' };
const tone = (s) => ST[Object.keys(ST).find((k) => s.startsWith(k))] || 'st-mute';

export async function render(el) {
  const a = await api.get('/api/research/alignment');
  const ref = (k) => { const r = a.references[k]; return r ? `<a href="https://doi.org/${esc(r.doi)}" target="_blank" rel="noopener">${esc(r.cite)}</a>` : esc(k); };
  el.innerHTML = `
  <div class="page-head"><div><h1>Research alignment</h1><p>Published evidence, open gaps and the research objective each module serves. Prepared ${esc(a.prepared)}.</p></div>
    <div class="actions"><a class="btn" href="#/evidence">${icon('alignment')}Evidence chain</a><a class="btn" href="#/assurance">${icon('assurance')}Model assurance</a></div></div>
  <div class="note" style="margin-bottom:18px">Published evidence, project findings that are not yet peer reviewed, and untested proposals are labelled separately. ${esc(a.sources.not_done)}</div>

  <h2 style="font-size:18px;margin:0 0 12px">Objectives and the modules that serve them</h2>
  <div class="grid g-3" style="margin-bottom:26px">${a.objectives.map((o) => card(`${esc(o.id)} · ${esc(o.questions)}`, `
    <p style="margin:0 0 10px">${esc(o.text)}</p>
    <div class="pill-list" style="margin-bottom:12px">${o.modules.map(([l, r]) => `<a class="btn sm" href="#/${r}">${esc(l)}</a>`).join('')}</div>
    <div class="kvs" style="grid-template-columns:1fr"><div><span>Delivered in RINDA</span>${esc(o.delivered)}</div><div><span>Still open</span>${esc(o.open)}</div></div>`, { sub: esc(o.paper) })).join('')}</div>

  <h2 style="font-size:18px;margin:0 0 12px">Research-gap matrix</h2>
  <div class="grid" style="margin-bottom:26px">${a.gaps.map((g) => `<section class="card"><div class="card-h"><h3>${esc(g.id)} · ${esc(g.topic)}</h3><span class="sub">${esc(g.objective)}</span></div><div class="card-b">
    <div class="kvs" style="grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px 22px">
      <div><span>What existing studies have done</span>${esc(g.accomplished)}</div>
      <div><span>Their limitations</span>${esc(g.limitations)}</div>
      <div><span>The unresolved gap</span><b>${esc(g.gap)}</b></div>
      <div><span>How RINDA addresses it</span>${esc(g.rinda)}</div>
      <div><span>How the contribution will be evaluated</span>${esc(g.evaluation)}</div>
      <div><span>Where it stands</span><span class="status ${tone(g.status)}" style="display:inline-flex;margin-bottom:4px">${esc(g.status.split('.')[0])}</span><br>${esc(g.status.split('.').slice(1).join('.').trim())}</div>
    </div>
    ${g.refs.length ? `<p class="hint" style="margin:12px 0 0">Sources: ${g.refs.map(ref).join(' · ')}</p>` : '<p class="hint" style="margin:12px 0 0">Source: this project\'s 5 November 2026 audit.</p>'}</div></section>`).join('')}</div>

  <h2 style="font-size:18px;margin:0 0 12px">Assumptions the evidence has challenged</h2>
  <div class="grid g-2">${a.challenges.length ? a.challenges.map((c) => card(esc(c.title), `<p style="margin:0 0 10px;color:var(--ink-2)">${esc(c.detail)}</p><p style="margin:0"><b>Recommended:</b> ${esc(c.recommendation)}</p>`)).join('') : empty('None recorded')}</div>`;
}
