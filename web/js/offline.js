// Offline capture for field observations. Each record carries a client_uuid, so
// resending after a dropped connection can never create a duplicate: the server
// returns the stored record instead.
import { api } from './ui.js';

const KEY = 'rinda.queue.v1';

function read() {
  try { return JSON.parse(localStorage.getItem(KEY) || '[]'); } catch (e) { return []; }
}
function write(q) {
  try { localStorage.setItem(KEY, JSON.stringify(q)); } catch (e) { /* storage full or blocked */ }
  window.dispatchEvent(new Event('rinda:queue'));
}

export const queued = () => read().length;
export const pending = () => read();

export function enqueue(obs) {
  const q = read();
  if (!q.some((o) => o.client_uuid === obs.client_uuid)) q.push(obs);
  write(q);
}

export async function flushQueue() {
  let sent = 0;
  const q = read();
  const keep = [];
  for (const o of q) {
    try { await api.post('/api/observations', o); sent++; }
    catch (e) {
      // keep only what failed for network reasons; validation errors are surfaced and dropped
      if (e.offline || e.timeout || (e.status && e.status >= 500)) keep.push(o);
      else keep.push({ ...o, _error: e.message });
    }
  }
  write(keep);
  return sent;
}

export function discard(uuid) { write(read().filter((o) => o.client_uuid !== uuid)); }
