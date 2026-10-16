import { api, card, icon, esc, toast } from '../ui.js';
import { showOnboarding } from '../app.js';

const GLOSSARY = [
  ['Monitoring point', 'One of 600 fixed sites. Each is a 50 m-radius circle sampled by Sentinel-1 radar. Forecasts apply to that circle only.'],
  ['New surface-water formation', 'What RINDA forecasts: a point that is dry on the issue date (and the 3 days before) becomes wet by the target date, as seen by radar. Not larvae, not mosquitoes, not malaria risk.'],
  ['Issue date', 'The day a forecast is made. Only data dated on or before it may be used.'],
  ['Horizon', 'How far ahead the forecast looks: 7 or 14 days. Longer horizons are less reliable.'],
  ['Valid window', 'Target date ± 2 days, matching how the radar outcome is checked. After it ends the forecast has expired.'],
  ['Stale', 'A forecast older than the configured limit (7 days). Stale forecasts describe the past and must not guide current work.'],
  ['Hindcast', 'Forecasts produced after the fact from archived inputs, as they would have been issued. Used to test the model before it runs live.'],
  ['Calibrated probability', 'A probability adjusted so that, across many points, a value of 0.2 corresponds to water forming about 20% of the time.'],
  ['Conformal call (90%)', 'A statistical guarantee: the set of possible outcomes the model returns contains the true outcome at least 90% of the time. If the set contains both outcomes, the model cannot decide.'],
  ['High priority for verification', 'The 90% set contains only "formation". Inspect before the target date.'],
  ['Insufficient evidence to classify', 'The 90% set contains both outcomes. Only a field visit can classify these points.'],
  ['Lower current priority', 'The 90% set contains only "no formation". Not evidence that the point is free of larvae.'],
  ['PR-AUC', 'Area under the precision–recall curve: how well the model ranks points that will form water above those that will not. Compare it with the base rate, which is what random guessing scores.'],
  ['Base rate', 'The share of points that actually formed water (about 3%). Rare events make accuracy misleading, which is why RINDA never reports accuracy.'],
  ['Persistence baseline', 'A simple rule: points that were often wet in the last 30 days will be wet again. RINDA\'s model does not yet beat it, which is why it is labelled research stage.'],
  ['Precision of calls', 'Of the points called High priority, the share where water did form.'],
  ['Detection rate', 'Of the points where water formed, the share the model called High priority.'],
  ['Research stage', 'Validated against radar outcomes only, not yet against field findings. The v3 model beats the persistence baseline on rolling out-of-sample forecasts but still needs an untouched test. Suitable for prioritising inspections, never for authorising treatment.'],
  ['Verified observation', 'A field record checked by a second person. Only verified observations can support an intervention.'],
  ['Not sampled', 'No dipping was done. Never treated as "no larvae found".'],
  ['Intervention', 'A treatment such as Bti larviciding, authorised by a supervisor from a verified observation and closed by a follow-up inspection.'],
  ['Demonstration record', 'Created by a demonstration account. Labelled DEMO everywhere and in exports.'],
];

const GUIDE = [
  ['Read the situation', 'Open Situation overview. Check the status strip first: issue date, valid window, freshness and model status. If it says Stale, the forecast is history.'],
  ['Find points to inspect', 'Habitat Forecast lists every point with its verification priority, probability and uncertainty. Click a point for its dossier: radar history, drivers, inputs, model version and any field records.'],
  ['Assign inspections (supervisor)', 'Field Surveillance → Alerts lists High-priority points. Assign an inspection to a field officer with a due date before the target date, acknowledge, or dismiss with a reason.'],
  ['Record a finding (field officer)', 'Field Surveillance → Record observation. Capture GPS, water, habitat, larvae with the number of dips, species and stages. Choose "Not sampled" when you did not dip. Offline records are queued and sent automatically.'],
  ['Review (supervisor)', 'Review queue: verify, return for correction or reject. You cannot review your own record.'],
  ['Authorise and record treatment', 'From a verified observation with larvae, a supervisor authorises an intervention with a protocol reference. The person treating records quantity and time. A later verified observation closes it with an outcome.'],
  ['Check the model', 'Replay & Validation shows, for any past issue, what was forecast, which inputs were used and what radar later saw. Model Assurance has the full statistics.'],
  ['Report', 'Reports & Bulletins produce a printable A4 bulletin. Field Surveillance exports tasks, observations and interventions as CSV for authorised roles.'],
];

export async function render(el, st) {
  el.innerHTML = `
  <div class="page-head">
    <div><h1>Help & glossary</h1><p>How to use RINDA, what its terms mean, and how to tell us what is not working.</p></div>
    <div class="actions"><button class="btn" id="tour">${icon('play')}Replay the introduction</button></div>
  </div>
  <div class="field" style="max-width:420px;margin-bottom:14px"><label for="hq">Search the guide and glossary</label><input id="hq" type="search" placeholder="e.g. stale, conformal, dips"></div>
  <div class="grid g-2">
    ${card('User guide', `<ol class="steps" style="margin:0;padding:0" id="guide">${GUIDE.map(([t, d]) => `<li data-q="${esc((t + ' ' + d).toLowerCase())}"><div><b>${t}.</b> ${d}</div></li>`).join('')}</ol>`)}
    ${card('Glossary', `<dl class="glossary" style="margin:0" id="gloss">${GLOSSARY.map(([t, d]) => `<div data-q="${esc((t + ' ' + d).toLowerCase())}"><dt>${t}</dt><dd>${d}</dd></div>`).join('')}</dl>`)}
  </div>
  <div class="grid g-2" style="margin-top:14px">
    ${card('Send feedback', `<form id="fbf" class="form">
      <div class="field"><label for="fbM">What happened, or what would help?</label><textarea id="fbM" required minlength="3" maxlength="3000"></textarea><span class="hint">Do not include personal data about patients or residents.</span></div>
      <button class="btn primary">Send</button></form>`)}
    ${card('Language and support', `<p style="margin:0 0 8px;font-size:12.5px;color:var(--ink-2)">The interface is in English. A Kinyarwanda version will be prepared after usability sessions with district teams confirm the terms they use; the glossary above is the starting list for translation.</p>
      <p style="margin:0;font-size:12.5px;color:var(--ink-2)">Technical documentation: <span class="mono">docs/</span> in the repository (user guide, administrator guide, API, model validation, security, deployment). API reference: <a href="/api/docs" target="_blank" rel="noopener">/api/docs</a>.</p>`)}
  </div>`;
  el.querySelector('#tour').addEventListener('click', showOnboarding);
  el.querySelector('#hq').addEventListener('input', (e) => {
    const q = e.target.value.trim().toLowerCase();
    el.querySelectorAll('[data-q]').forEach((n) => { n.hidden = q && !n.dataset.q.includes(q); });
  });
  el.querySelector('#fbf').addEventListener('submit', async (e) => {
    e.preventDefault();
    try { await api.post('/api/feedback', { page: location.hash, message: el.querySelector('#fbM').value }); toast('Thank you. Your feedback was recorded.'); el.querySelector('#fbM').value = ''; }
    catch (er) { toast(er.message, true); }
  });
}
