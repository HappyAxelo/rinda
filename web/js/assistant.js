// Ask the map: a task pane beside the map that answers questions from RINDA's live records and links each answer to a
// map view. Server: server/assistant.py. The conversation survives map re-renders (following an answer's link
// re-renders the map), so its state lives in this module. Only links into RINDA (#/...) are rendered as links.
import { api, esc, icon, toast } from './ui.js';

const S = { open: false, cid: null, items: [], busy: false, status: null, settings: null, draft: '' };
const SUGGEST = ['What should field teams inspect this week?', 'Which places inside the sector may get new water in the next 7 days?',
  'How old is the radar data, and what did the latest pass show?', 'How reliable are these forecasts?'];

// minimal, safe Markdown: escape everything, then allow **bold**, lists, line breaks and internal links only
function md(text) {
  let h = esc(text);
  const links = [];
  const keep = (label, url) => { links.push(`<a href="${url.replace(/"/g, '%22')}" class="ask-link">${label}</a>`); return `\u0000${links.length - 1}\u0000`; };
  h = h.replace(/\[([^\]]{1,120})\]\((#\/[^)\s]{1,600})\)/g, (_, t, u) => keep(t, u));
  // a bare address the model wrote as text, with or without brackets, still opens the map
  h = h.replace(/\[?(#\/(?:map|dossier|research|assurance)\?[^\s<\]]{1,600}|#\/(?:research|assurance))\]?/g, (_, u) => keep(u.startsWith('#/map') ? 'Open on the map' : 'Open', u));
  h = h.replace(/\u0000(\d+)\u0000/g, (_, i) => links[+i]);
  h = h.replace(/\*\*([^*]{1,200})\*\*/g, '<b>$1</b>');
  const lines = h.split('\n');
  let out = '', inList = false, table = [];
  const flushTable = () => {           // a Markdown table, if the model writes one anyway
    if (!table.length) return;
    const rows = table.filter((r) => !/^\|?\s*:?-{2,}/.test(r)).map((r) => r.replace(/^\||\|$/g, '').split('|').map((c) => c.trim()));
    out += `<div class="ask-tbl"><table>${rows.map((r, i) => `<tr>${r.map((c) => (i ? `<td>${c}</td>` : `<th>${c}</th>`)).join('')}</tr>`).join('')}</table></div>`;
    table = [];
  };
  for (const ln of lines) {
    if (/^\s*\|.*\|\s*$/.test(ln)) { if (inList) { out += '</ul>'; inList = false; } table.push(ln.trim()); continue; }
    flushTable();
    const m = ln.match(/^\s*(?:[-*]|\d+\.)\s+(.*)$/);
    if (m) { if (!inList) { out += '<ul>'; inList = true; } out += `<li>${m[1]}</li>`; continue; }
    if (inList) { out += '</ul>'; inList = false; }
    out += ln.trim() ? `<p>${ln}</p>` : '';
  }
  flushTable();
  return out + (inList ? '</ul>' : '');
}

function paint(start) {
  const root = document.getElementById('askPane') || start;   // the map may have re-rendered the pane mid-answer
  const log = root.querySelector('#askLog');
  if (!log) return;
  log.innerHTML = S.items.length ? S.items.map((m) => m.role === 'user'
    ? `<div class="ask-q">${esc(m.text)}</div>`
    : `<div class="ask-a">${md(m.text)}${m.error ? `<p class="ask-err">${esc(m.error)}</p>` : ''}</div>`).join('')
    : `<div class="ask-empty"><p>Ask about the forecasts, the latest radar pass, field records or a location. Answers come from RINDA's own records and link to the map.</p>
       <div class="ask-sugg">${SUGGEST.map((q) => `<button class="btn sm ghost" data-q="${esc(q)}">${esc(q)}</button>`).join('')}</div></div>`;
  if (S.status) log.insertAdjacentHTML('beforeend', `<div class="ask-status">${esc(S.status)}…</div>`);
  log.scrollTop = log.scrollHeight;
  log.querySelectorAll('[data-q]').forEach((b) => b.addEventListener('click', () => ask(root, b.dataset.q)));
  root.querySelector('#askSend').disabled = S.busy;
}

async function ask(root, q) {
  q = (q || '').trim();
  if (!q || S.busy) return;
  S.busy = true; S.status = 'Thinking'; S.draft = '';
  S.items.push({ role: 'user', text: q });
  const a = { role: 'assistant', text: '' };
  S.items.push(a);
  const input = (document.getElementById('askPane') || root).querySelector('#askIn'); if (input) input.value = '';
  paint(root);
  try {
    const r = await fetch('/api/v1/assistant/ask', { method: 'POST', credentials: 'same-origin',
      headers: { 'X-RINDA': '1', 'content-type': 'application/json' }, body: JSON.stringify({ question: q, conversation_id: S.cid }) });
    if (!r.ok) { let m = `Request failed (${r.status})`; try { const j = await r.json(); m = j.error?.message || j.detail || m; } catch { /* not JSON */ } throw new Error(m); }
    const reader = r.body.getReader(); const dec = new TextDecoder(); let buf = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf('\n\n')) >= 0) {
        const line = buf.slice(0, i); buf = buf.slice(i + 2);
        if (!line.startsWith('data: ')) continue;
        const ev = JSON.parse(line.slice(6));
        if (ev.type === 'start') S.cid = ev.conversation_id;
        else if (ev.type === 'status') S.status = ev.text;
        else if (ev.type === 'text') { a.text += ev.text; S.status = null; }
        else if (ev.type === 'error') a.error = ev.message;
        else if (ev.type === 'done') { S.cid = ev.turns_left > 0 ? ev.conversation_id : null; }
        paint(root);
      }
    }
    if (!a.text && !a.error) a.error = 'No answer was returned.';
  } catch (e) { a.error = e.message; S.cid = null; }
  S.busy = false; S.status = null;
  paint(root);
}

// Mount the toggle button and the pane inside the map workspace. Called on every map render.
export async function mountAssistant(ws, toolsEl) {
  toolsEl.insertAdjacentHTML('afterbegin', `<button class="icon-btn ask-toggle" id="askToggle" aria-label="Ask the map" title="Ask the map" aria-expanded="${S.open}">${icon('search')}</button>`);
  ws.insertAdjacentHTML('beforeend', `<aside class="askpane" id="askPane" ${S.open ? '' : 'hidden'} aria-label="Ask the map">
    <header><div><h3>Ask the map</h3><span class="hint" id="askNote"></span></div>
      <div style="display:flex;gap:4px"><button class="icon-btn" id="askNew" title="New conversation" aria-label="New conversation">${icon('reset')}</button>
      <button class="icon-btn" id="askClose" aria-label="Close">${icon('x')}</button></div></header>
    <div class="ask-log" id="askLog" aria-live="polite"></div>
    <form class="ask-form" id="askForm"><textarea id="askIn" rows="2" maxlength="1000" placeholder="Ask about the map…" aria-label="Your question"></textarea>
      <button class="btn primary" id="askSend" type="submit">Ask</button></form></aside>`);
  const pane = ws.querySelector('#askPane');
  const setOpen = (v) => { S.open = v; pane.hidden = !v; ws.querySelector('#askToggle').setAttribute('aria-expanded', String(v)); if (v) pane.querySelector('#askIn').focus(); };
  ws.querySelector('#askToggle').addEventListener('click', () => setOpen(!S.open));
  pane.querySelector('#askClose').addEventListener('click', () => setOpen(false));
  pane.querySelector('#askNew').addEventListener('click', () => { if (!S.busy) { S.items = []; S.cid = null; paint(pane); } });
  pane.querySelector('#askForm').addEventListener('submit', (e) => { e.preventDefault(); ask(pane, pane.querySelector('#askIn').value); });
  pane.querySelector('#askIn').addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(pane, e.target.value); } });
  pane.querySelector('#askIn').addEventListener('input', (e) => { S.draft = e.target.value; });
  pane.querySelector('#askIn').value = S.draft;
  paint(pane);
  try {
    S.settings = await api.get('/api/assistant/status');
    const note = pane.querySelector('#askNote');
    if (!S.settings.enabled) { note.textContent = S.settings.reason; pane.querySelector('#askSend').disabled = true; }
    else if (!S.settings.signed_in) { note.textContent = 'Sign in to ask questions.'; pane.querySelector('#askSend').disabled = true; }
    else note.textContent = `Answers from RINDA's records · questions are sent to ${S.settings.sends_to}`;
  } catch (e) { toast(e.message, true); }
}
