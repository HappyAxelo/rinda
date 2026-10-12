import { api, card, fmt, icon, table } from '../ui.js';

export async function render(el, st) {
  const h = st.h;
  const ver = await api.get(`/api/verification?h=${h}`);
  const rows = [...ver].reverse();
  el.innerHTML = `
  <div class="page-head">
    <div><h1>Bulletins</h1><p>One-page A4 bulletins for district vector control and the national programme: forecast map, priority targets with coordinates, the sortie plan and a plain-language reliability statement, with sign-off lines. Print or save as PDF from the preview.</p></div>
    <div class="actions"><a class="btn primary" href="/api/v1/bulletin?h=${h}&date=${st.date}" target="_blank" rel="noopener">${icon('print')}Open ${fmt.date(st.date)} bulletin</a></div>
  </div>
  <div class="grid g-main">
    ${card('Preview', `<iframe title="Bulletin preview" src="/api/v1/bulletin?h=${h}&date=${st.date}" style="width:100%;height:calc(100vh - 230px);min-height:600px;border:0;border-radius:0 0 6px 6px;background:#e9ecef"></iframe>`, { flush: true, sub: `${h}-day outlook · issue ${st.date}` })}
    ${card('Archive', table([
      { h: 'Issue', f: (r) => `<span class="mono">${r.date}</span>` }, { h: 'High calls', r: 1, f: (r) => r.formation_calls },
      { h: 'Verify', r: 1, f: (r) => r.uncertain }, { h: 'Confirmed', r: 1, f: (r) => r.calls_confirmed },
      { h: '', f: (r) => `<a class="btn sm" href="/api/v1/bulletin?h=${h}&date=${r.date}" target="_blank" rel="noopener">Open</a>` },
    ], rows, { max: 900 }).html, { flush: true, sub: `${rows.length} issues`, foot: '<span>Confirmed = formation calls the next radar pass found wet.</span>' })}
  </div>`;
}
