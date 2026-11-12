// Analyst workspace: historical datasets, reproducible training, model registry,
// data sources. A training run never changes what RINDA displays.
import { api, card, icon, esc, fmt, chart, css, table, toast, modal, empty, pill, auditNote, NODATA } from '../ui.js';
import { params } from '../app.js';

let ST, timer;
export function destroy() { clearTimeout(timer); }

const TABS = [['inventory', 'Data inventory'], ['datasets', 'Datasets'], ['training', 'Training runs'], ['registry', 'Model registry'], ['efficiency', 'Efficiency and edge readiness'], ['sources', 'Data sources and ingestion']];
const DS = { validated: ['st-warn', 'Waiting for confirmation'], confirmed: ['st-good', 'Confirmed'], failed_validation: ['st-crit', 'Failed validation'] };
const MS = { research_only: ['st-warn', 'Research only'], candidate: ['st-info', 'Candidate'], approved_candidate: ['st-good', 'Approved candidate'], rejected: ['st-mute', 'Rejected'], archived: ['st-mute', 'Archived'], synthetic_fixture: ['st-warn', 'Synthetic fixture'] };
const tag = (m, k) => `<span class="status ${(m[k] || ['st-mute'])[0]}">${(m[k] || [0, k])[1]}</span>`;
const prov = (p) => (p === 'SYNTHETIC' ? '<span class="badge" style="background:var(--warn-soft);color:var(--warn)">SYNTHETIC</span>' : p === 'STUDY' ? '<span class="badge">STUDY</span>' : '<span class="badge">REAL</span>');

export async function render(el, st) {
  ST = st;
  const want = params().get('tab') || 'inventory';
  el.innerHTML = `
  <div class="page-head"><div><h1>Data and models</h1><p>Load historical data, check its quality, train and compare models, and see where each data source stands. Historical work here is separate from any future live connection.</p></div></div>
  ${auditNote(st.boot.audit)}
  <div class="tabs" role="tablist" style="margin-top:18px">${TABS.map(([k, l]) => `<button role="tab" data-tab="${k}" aria-selected="${k === want}">${l}</button>`).join('')}</div>
  <div id="mdBody"></div>`;
  const show = (k) => {
    clearTimeout(timer);
    el.querySelectorAll('[data-tab]').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.tab === k)));
    ({ inventory, datasets, training, registry, efficiency, sources })[k](document.getElementById('mdBody')).catch((e) => { if (!e.cancelled) toast(e.message, true); });
  };
  el.querySelectorAll('[data-tab]').forEach((b) => b.addEventListener('click', () => show(b.dataset.tab)));
  show(TABS.some((x) => x[0] === want) ? want : 'inventory');
}

// ------------------------------------------------------------------ datasets
function reportHTML(r) {
  const ic = { pass: 'check', warn: 'alert', fail: 'x' };
  return `<p style="margin:0 0 6px">${fmt.n(r.rows)} rows${r.date_min ? ` · ${r.date_min} to ${r.date_max}` : ''} · ${r.passed ? `<b style="color:var(--good)">passed</b>${r.warnings ? ` with ${r.warnings} warning(s)` : ''}` : '<b style="color:var(--crit)">failed</b>'}</p>
    ${r.checks.map((c) => `<div class="check-row ${c.status}">${icon(ic[c.status])}<div><b>${esc(c.label)}</b><small>${esc(c.detail)}</small></div></div>`).join('')}
    ${r.columns?.length ? `<details class="tech" style="margin-top:10px"><summary>Columns found (${r.columns.length})</summary><div>${r.columns.map((c) => `<span class="badge" title="${esc(c.type)} · ${fmt.pct(c.missing, 0)} missing">${esc(c.name)}</span>`).join(' ')}</div></details>` : ''}`;
}

async function datasets(box) {
  const [k, list] = await Promise.all([api.get('/api/datasets/kinds'), api.get('/api/datasets')]);
  const can = ST.can('manage:data');
  const t = table([
    { h: 'Record', f: (d) => `<span class="mono">D${d.id}</span>` }, { h: 'Type', f: (d) => esc(k.kinds.find((x) => x.key === d.kind)?.label || d.kind) }, { h: 'Version', r: 1, f: (d) => `v${d.version}` },
    { h: 'Label', f: (d) => `<span style="white-space:normal">${esc(d.label || d.filename)}</span>` }, { h: 'Rows', r: 1, f: (d) => fmt.n(d.n_rows) }, { h: 'Source', f: (d) => prov(d.provenance) },
    { h: 'Target', f: (d) => (d.target ? `${d.target}${d.horizon ? ` · ${d.horizon}-day` : ''}` : '–') }, { h: 'Status', f: (d) => tag(DS, d.status) },
    { h: 'Loaded', f: (d) => (d.created_utc || '').slice(0, 10) }, { h: '', f: () => '<button class="btn sm" data-open>Quality report</button>' },
  ], list.rows, { empty: 'No datasets loaded.', onRow: (d) => open(d) });
  box.innerHTML = `<div class="grid g-main">
    ${card(`Dataset registry · ${list.total}`, t.html, { flush: true, sub: 'Original files are kept unchanged. Each confirmation writes a new version.' })}
    ${card('Load historical data', can ? `<form id="upF" class="form">
      <div class="field"><label for="upK">What the file contains</label><select id="upK">${k.kinds.map((x) => `<option value="${x.key}">${esc(x.label)}</option>`).join('')}</select><span class="hint" id="upNeed"></span></div>
      <div class="form cols-2" id="upT" hidden><div class="field"><label for="upTarget">Forecast target</label><select id="upTarget">${Object.entries(k.targets).map(([a, l]) => `<option value="${a}">${a}: ${esc(l)}</option>`).join('')}</select></div>
        <div class="field"><label for="upH">Horizon</label><select id="upH"><option value="7">7 days</option><option value="14">14 days</option></select></div></div>
      <div class="field"><label for="upL">Label</label><input id="upL" maxlength="200" placeholder="e.g. RBC larval surveys, Gasabo, 2025"></div>
      <div class="field"><label for="upP">Origin</label><select id="upP"><option value="REAL">Real records</option><option value="SYNTHETIC">Synthetic test file</option></select><span class="hint">Synthetic files stay labelled SYNTHETIC everywhere and can never produce an approved model.${ST.user.demo ? ' Demonstration accounts can only load synthetic files.' : ''}</span></div>
      <div class="field"><label for="upFile">File <span class="u">CSV, Parquet or GeoJSON points · up to ${k.max_upload_mb} MB</span></label><input id="upFile" type="file" accept=".csv,.parquet,.geojson,.json" required></div>
      <button class="btn primary">${icon('upload')}Check file</button><p class="hint" style="margin:0">Checking a file does not use it. You confirm it after reading the quality report.</p></form>` : '<p class="hint" style="margin:0">Your role can read the registry. Loading data needs an analyst or administrator account.</p>')}
  </div>`;
  t.bind();
  const need = () => { const x = k.kinds.find((y) => y.key === box.querySelector('#upK').value); box.querySelector('#upNeed').textContent = `Needs columns: ${x.required.join(', ')}${x.any_of ? ', and a site id, habitat id or coordinates' : ''}.`; box.querySelector('#upT').hidden = x.key !== 'training_table'; };
  if (can) {
    need(); box.querySelector('#upK').addEventListener('change', need);
    box.querySelector('#upF').addEventListener('submit', async (e) => {
      e.preventDefault();
      const fd = new FormData();
      const kind = box.querySelector('#upK').value;
      fd.append('kind', kind); fd.append('label', box.querySelector('#upL').value); fd.append('provenance', box.querySelector('#upP').value);
      if (kind === 'training_table') { fd.append('target', box.querySelector('#upTarget').value); fd.append('horizon', box.querySelector('#upH').value); }
      fd.append('file', box.querySelector('#upFile').files[0]);
      const btn = e.target.querySelector('button'); btn.disabled = true; btn.textContent = 'Checking…';
      try { const r = await (await api.upload('/api/datasets/upload', fd)).json(); await datasets(box); open({ ...r, kind, report: r.report }); }
      catch (er) { toast(er.message, true); btn.disabled = false; btn.innerHTML = `${icon('upload')}Check file`; }
    });
  }
  function open(d) {
    const dlg = modal(`Data quality report · D${d.id}`, `${d.provenance === 'SYNTHETIC' ? '<div class="synthetic-banner">Synthetic data · not real programme records</div>' : ''}
      ${d.report ? reportHTML(d.report) : '<p class="hint">No report stored.</p>'}
      ${d.status === 'validated' && can ? `<div class="note warn">Confirming makes this dataset available for analysis and training. Duplicates and rows with gaps in required columns are dropped into a new version; the original file is kept.</div><div class="actions"><button class="btn primary" id="dsConfirm">${icon('check')}Confirm and use this dataset</button></div>` : ''}
      ${d.status === 'failed_validation' ? '<div class="note crit">This file cannot be used. Correct the failed checks and load it again.</div>' : ''}`, { wide: true });
    dlg.querySelector('#dsConfirm')?.addEventListener('click', async () => {
      try { const r = await api.post(`/api/datasets/${d.id}/confirm`); toast(`Confirmed: ${fmt.n(r.rows)} rows kept, ${r.dropped} dropped`); dlg.close(); datasets(box); } catch (er) { toast(er.message, true); }
    });
  }
}

// ------------------------------------------------------------------ training
function metricsHTML(m, id) {
  const mo = m.model, b = m.baselines, cm = mo.confusion;
  const rows = [['This run', mo], ...Object.entries(b).map(([k, v]) => [k.replace(/^B\d_/, '').replace(/_/g, ' '), v])];
  return `<div class="kvs" style="margin-bottom:12px"><div><span>Target</span>${esc(m.target)}: ${esc(m.target_label)}</div><div><span>Horizon</span>${m.horizon} days</div><div><span>Features</span>${m.n_features}</div><div><span>Random seed</span>${m.seed}</div>
    <div><span>Trained on</span>to ${m.splits.train_last} (${fmt.n(m.splits.n_train)} rows)</div><div><span>Calibrated on</span>${m.splits.cal_first} to ${m.splits.cal_last}</div><div><span>Tested on</span>${m.splits.test_first} to ${m.splits.test_last}</div><div><span>Gap between windows</span>${m.splits.embargo_days} days</div></div>
    ${table([{ h: 'Forecast', f: (r) => esc(r[0]) }, { h: 'PR-AUC', r: 1, f: (r) => r[1].pr_auc?.toFixed(3) ?? '–' }, { h: 'Precision', r: 1, f: (r) => r[1].precision?.toFixed(2) ?? '–' }, { h: 'Recall', r: 1, f: (r) => r[1].recall?.toFixed(2) ?? '–' },
    { h: 'F1', r: 1, f: (r) => r[1].f1?.toFixed(2) ?? '–' }, { h: 'Brier', r: 1, f: (r) => r[1].brier?.toFixed(4) ?? '–' }, { h: 'Calibration error', r: 1, f: (r) => r[1].ece?.toFixed(4) ?? '–' }], rows, { max: 240 }).html}
    <p class="hint" style="margin:8px 0 14px">Base rate ${fmt.pct(mo.base_rate)} of ${fmt.n(mo.n)} test rows. Accuracy is not reported: predicting "no event" everywhere would already score ${fmt.pct(1 - mo.base_rate, 0)}.</p>
    <div class="grid g-2"><div><b style="font-size:13px">Confusion matrix on the test window</b><div class="cm" style="margin-top:8px"><div class="h"></div><div class="h">Predicted event</div><div class="h">Predicted none</div><div class="h">Event</div><div class="ok">${fmt.n(cm.tp)}</div><div>${fmt.n(cm.fn)}</div><div class="h">None</div><div>${fmt.n(cm.fp)}</div><div class="ok">${fmt.n(cm.tn)}</div></div>
      <p class="hint" style="margin:10px 0 0"><b>Held-out areas:</b> ${m.geographic_holdout.pr_auc == null ? NODATA : `PR-AUC ${m.geographic_holdout.pr_auc.toFixed(3)}`} when whole 500 m blocks are kept out of training.</p></div>
      <div><b style="font-size:13px">Calibration: predicted against observed</b><div class="chart sm" id="cal${id}"></div></div></div>
    <b style="font-size:13px;display:block;margin-top:14px">Most used inputs (share of model gain)</b><div class="chart" id="fi${id}" style="height:${Math.min(12, m.feature_importance.length) * 22 + 40}px"></div>
    ${m.efficiency ? `<b style="font-size:13px;display:block;margin:14px 0 8px">Computational cost</b><div class="kvs"><div><span>Inference</span>${m.efficiency.ms_per_1000_rows} ms per 1,000 rows</div><div><span>Model size</span>${m.efficiency.model_bytes ? `${fmt.n(m.efficiency.model_bytes / 1024, 0)} KB` : NODATA}</div><div><span>Training time</span>${m.efficiency.train_seconds ?? NODATA} s</div><div><span>Peak training memory</span>${m.efficiency.peak_memory_mb ? `${fmt.n(m.efficiency.peak_memory_mb, 0)} MB` : NODATA}</div></div><p class="hint" style="margin:6px 0 12px">${esc(m.efficiency.measured_on)}. ${esc(m.efficiency.note)}</p>` : ''}
    <details class="tech"><summary>Reproducibility</summary><div>Dataset D${m.dataset.id} v${m.dataset.version} (${esc(m.dataset.provenance)}), fingerprint <span class="mono">${esc(String(m.dataset.sha256).slice(0, 16))}</span>.<br>Prediction hash <span class="mono" style="word-break:break-all">${esc(m.predictions_sha256)}</span>.<br>The same dataset, settings and seed give the same hash.</div></details>`;
}
function metricsCharts(m, id) {
  chart(`cal${id}`, { grid: { left: 40, right: 12, top: 12, bottom: 28 }, tooltip: { trigger: 'item' }, xAxis: { type: 'value', min: 0, max: 1, name: 'predicted' }, yAxis: { type: 'value', min: 0, max: 1 },
    series: [{ type: 'line', data: [[0, 0], [1, 1]], showSymbol: false, lineStyle: { color: css('--ink-3'), type: 'dashed', width: 1 }, silent: true },
      { type: 'line', symbolSize: 8, data: m.reliability.map((r) => [r.mean_predicted, r.observed]), lineStyle: { width: 2, color: css('--s-1') }, itemStyle: { color: css('--s-1') } }] });
  const f = m.feature_importance.slice(0, 12).reverse();
  chart(`fi${id}`, { grid: { left: 190, right: 20, top: 6, bottom: 22 }, tooltip: { trigger: 'item', valueFormatter: (v) => fmt.pct(v) }, xAxis: { type: 'value' }, yAxis: { type: 'category', data: f.map((x) => x.feature), axisLabel: { fontSize: 10.5 } },
    series: [{ type: 'bar', barMaxWidth: 12, data: f.map((x) => x.gain_share), itemStyle: { color: css('--s-1'), borderRadius: [0, 4, 4, 0] } }] });
}

async function training(box) {
  const [targets, runs, ds] = await Promise.all([api.get('/api/training/targets'), api.get('/api/training/runs'), api.get('/api/datasets?kind=training_table')]);
  const ready = ds.rows.filter((d) => d.status === 'confirmed');
  const can = ST.can('train:model');
  const RS = { queued: ['st-info', 'Queued'], running: ['st-warn', 'Running'], completed: ['st-good', 'Completed'], failed: ['st-crit', 'Failed'] };
  const t = table([
    { h: 'Run', f: (r) => `<span class="mono">#${r.id}</span>` }, { h: 'Target', f: (r) => `${r.target} · ${r.horizon}-day` }, { h: 'Dataset', f: (r) => `D${r.dataset_version_id}` }, { h: 'Seed', r: 1, k: 'seed' },
    { h: 'Status', f: (r) => tag(RS, r.status) }, { h: 'PR-AUC', r: 1, f: (r) => r.metrics?.model?.pr_auc?.toFixed(3) ?? '–' },
    { h: 'Best baseline', r: 1, f: (r) => (r.metrics ? Math.max(...Object.values(r.metrics.baselines).map((b) => b.pr_auc || 0)).toFixed(3) : '–') },
    { h: 'Started by', f: (r) => esc(r.created_by || '') }, { h: 'Finished', f: (r) => (r.finished_utc || '').replace('T', ' ').slice(0, 16) }, { h: '', f: (r) => (r.metrics ? '<button class="btn sm">Results</button>' : r.status === 'failed' ? `<span class="hint" style="white-space:normal">${esc(r.message || '')}</span>` : '') },
  ], runs, { empty: 'No training runs yet.', onRow: (r) => r.metrics && openRun(r) });
  box.innerHTML = `<div class="grid g-main">
    <div class="grid" style="align-content:start">
      ${card('Forecast targets', `<div class="list">${targets.map((x) => `<div><span><b>${x.target}</b> · ${esc(x.label)}</span><span class="sub" style="text-align:right;white-space:normal;max-width:55%">${x.ready ? '<span class="status st-good">Labels available</span>' : `Awaiting data. ${esc(x.needs)}`}</span></div>`).join('')}</div>`, { flush: true, sub: 'Five separate outcomes. They are never combined.', foot: '<span>The displayed v3 model predicts target A only. A table with labels for one target cannot train another.</span>' })}
      ${card(`Training runs · ${runs.length}`, t.html, { flush: true })}
    </div>
    ${card('Start a training run', can ? (ready.length ? `<form id="trF" class="form">
      <div class="field"><label for="trD">Confirmed training table</label><select id="trD">${ready.map((d) => `<option value="${d.id}" data-t="${d.target}" data-h="${d.horizon || ''}">D${d.id} · target ${d.target}${d.horizon ? ` · ${d.horizon}-day` : ''} · ${esc(d.label || d.filename)}${d.provenance === 'SYNTHETIC' ? ' · SYNTHETIC' : ''}</option>`).join('')}</select></div>
      <div class="form cols-2"><div class="field"><label for="trTe">Training ends <span class="u">optional</span></label><input type="date" id="trTe"></div><div class="field"><label for="trCe">Calibration ends <span class="u">optional</span></label><input type="date" id="trCe"></div></div>
      <div class="form cols-2"><div class="field"><label for="trS">Random seed</label><input type="number" id="trS" value="42" min="0"></div><div class="field"><label for="trN">Trees</label><input type="number" id="trN" value="300" min="20" max="2000"></div></div>
      <button class="btn primary">${icon('play')}Train and evaluate</button>
      <p class="hint" style="margin:0">Left blank, the dates split the record 60 / 20 / 20 in time order. A gap of the horizon plus two days separates the windows, so no training outcome falls inside the test period. The result is a candidate: nothing RINDA displays changes.</p></form>` : empty('No confirmed training table', 'Load a prepared table under Datasets and confirm it.')) : '<p class="hint" style="margin:0">Starting a run needs an analyst or administrator account.</p>')}
  </div>`;
  t.bind();
  box.querySelector('#trF')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const o = box.querySelector('#trD').selectedOptions[0];
    try {
      await api.post('/api/training/runs', { target: o.dataset.t, horizon: +(o.dataset.h || 7), dataset_id: +o.value, seed: +box.querySelector('#trS').value, n_estimators: +box.querySelector('#trN').value,
        train_end: box.querySelector('#trTe').value || null, cal_end: box.querySelector('#trCe').value || null });
      toast('Training started. This takes about a minute.'); training(box);
    } catch (er) { toast(er.message, true); }
  });
  if (runs.some((r) => ['queued', 'running'].includes(r.status))) timer = setTimeout(() => document.getElementById('mdBody') === box && training(box), 5000);
  function openRun(r) {
    modal(`Training run #${r.id}`, `${r.metrics.dataset.provenance === 'SYNTHETIC' ? '<div class="synthetic-banner">Trained on synthetic data · results mean nothing about the field</div>' : ''}${r.metrics.dataset.provenance === 'STUDY' && r.target === 'A' ? auditNote(ST.boot.audit) : ''}${metricsHTML(r.metrics, r.id)}`, { wide: true });
    metricsCharts(r.metrics, r.id);
  }
}

// ------------------------------------------------------------------ registry
async function registry(box) {
  const r = await api.get('/api/models');
  const can = ST.can('decide:model');
  const pr = (m) => m.metrics?.model?.pr_auc;
  const base = (m) => (m.metrics?.baselines ? Math.max(...Object.values(m.metrics.baselines).map((b) => b.pr_auc || 0)) : null);
  const t = table([
    { h: 'Model', f: (m) => `<span class="mono">${esc(m.id)}</span>${m.id.startsWith(r.displayed) ? ' <span class="badge">displayed</span>' : ''}` }, { h: 'Target', f: (m) => `${m.target} · ${m.horizon}-day` },
    { h: 'Status', f: (m) => tag(MS, m.status) }, { h: 'Maturity', f: (m) => esc(m.maturity) }, { h: 'PR-AUC', r: 1, f: (m) => pr(m)?.toFixed(3) ?? '–' }, { h: 'Best baseline', r: 1, f: (m) => base(m)?.toFixed(3) ?? '–' },
    { h: 'Audit warning', f: (m) => (m.audit_warning ? `<span class="status st-crit">${icon('alert')}Applies</span>` : '–') }, { h: 'Notes', f: (m) => `<span style="white-space:normal">${esc(m.notes || '')}</span>` },
    { h: '', f: (m) => `<label class="check"><input type="checkbox" data-cmp="${esc(m.id)}"> compare</label>${can && ['candidate', 'approved_candidate'].includes(m.status) ? ` <button class="btn sm" data-dec="${esc(m.id)}">Decide</button>` : ''}${m.training_run_id ? ` <a class="btn sm ghost" href="#/map" data-show="${esc(m.id)}">Map</a>` : ''}` },
  ], r.models, { empty: 'No models registered.' });
  box.innerHTML = `${card(`Model registry · ${r.models.length}`, t.html, { flush: true, sub: `RINDA displays ${esc(r.displayed)}. Training never replaces it.`, foot: '<span>Promotion needs an administrator decision with a written reason. Evaluation scores alone are not validation.</span>' })}
    <div id="cmp" style="margin-top:18px"></div>
    <div style="margin-top:18px">${card('What the maturity levels mean', `<div class="list">${r.maturity_levels.map((x) => `<div><span><b>${esc(x.level)}</b></span><span class="sub" style="text-align:right;white-space:normal;max-width:70%">${esc(x.meaning)}</span></div>`).join('')}</div>`, { flush: true })}</div>`;
  const cmp = () => {
    const ids = [...box.querySelectorAll('[data-cmp]:checked')].map((x) => x.dataset.cmp).slice(0, 4);
    const ms = r.models.filter((m) => ids.includes(m.id));
    box.querySelector('#cmp').innerHTML = ms.length < 2 ? '' : card('Comparison', table([{ h: 'Measure', f: (x) => x[0] }, ...ms.map((m) => ({ h: esc(m.id), r: 1, f: (x) => x[1](m) }))], [
      ['Target and horizon', (m) => `${m.target} · ${m.horizon}-day`], ['PR-AUC', (m) => pr(m)?.toFixed(3) ?? '–'], ['Best baseline PR-AUC', (m) => base(m)?.toFixed(3) ?? '–'],
      ['Precision', (m) => m.metrics?.model?.precision?.toFixed(2) ?? '–'], ['Recall', (m) => m.metrics?.model?.recall?.toFixed(2) ?? '–'], ['Brier score', (m) => m.metrics?.model?.brier?.toFixed(4) ?? '–'],
      ['Evaluation period', (m) => (m.metrics?.splits ? `${m.metrics.splits.test_first} to ${m.metrics.splits.test_last}` : m.metrics?.window ? m.metrics.window.join(' to ') : '–')], ['Status', (m) => tag(MS, m.status)],
    ]).html, { flush: true, foot: '<span>Models evaluated on different periods or labels are not directly comparable.</span>' });
  };
  box.querySelectorAll('[data-cmp]').forEach((c) => c.addEventListener('change', cmp));
  box.querySelectorAll('[data-show]').forEach((a) => a.addEventListener('click', async () => { const m = await import('./map.js'); m.setModel(a.dataset.show); toast(`The map now shows out-of-sample predictions from ${a.dataset.show}. Reload the page to return to ${r.displayed}.`); }));
  box.querySelectorAll('[data-dec]').forEach((b) => b.addEventListener('click', () => {
    const d = modal(`Decision on ${b.dataset.dec}`, `<form id="dcF" class="form"><div class="field"><label for="dcS">Decision</label><select id="dcS"><option value="approved_candidate">Approve as a candidate for display</option><option value="rejected">Reject</option><option value="archived">Archive</option></select></div>
      <div class="field"><label for="dcN">Reason</label><textarea id="dcN" required minlength="5" maxlength="1000"></textarea></div><button class="btn primary">Record decision</button>
      <p class="hint" style="margin:0">Approving does not change the displayed model. Target A cannot be approved until the 5 November 2026 audit is resolved.</p></form>`);
    d.querySelector('#dcF').addEventListener('submit', async (e) => { e.preventDefault(); try { await api.post(`/api/models/${encodeURIComponent(b.dataset.dec)}/decision`, { status: d.querySelector('#dcS').value, note: d.querySelector('#dcN').value }); toast('Decision recorded'); d.close(); registry(box); } catch (er) { toast(er.message, true); } });
  }));
}

// ------------------------------------------------------------------- sources
async function sources(box) {
  const r = await api.get('/api/connectors');
  const con = Object.fromEntries(r.connectors.map((c) => [c.key, c]));
  const CS = { configured: ['st-good', 'Credentials set'], not_configured: ['st-mute', 'Not connected'], internal: ['st-info', 'Internal'] };
  const t = table([{ h: 'Run', f: (x) => `#${x.id}` }, { h: 'Source', k: 'source_key' }, { h: 'When', f: (x) => (x.finished_utc || '').replace('T', ' ').slice(0, 16) }, { h: 'Result', f: (x) => esc(x.status) },
    { h: 'Received', r: 1, f: (x) => x.n_received ?? 0 }, { h: 'New', r: 1, f: (x) => x.n_accepted ?? 0 }, { h: 'Duplicates', r: 1, f: (x) => x.n_duplicate ?? 0 }, { h: 'Rejected', r: 1, f: (x) => x.n_rejected ?? 0 },
    { h: 'Detail', f: (x) => `<span style="white-space:normal">${esc(x.message || '')}</span>` }], r.runs, { empty: 'No ingestion has run.', max: 320 });
  box.innerHTML = `<div class="note" style="margin-bottom:18px">${icon('info')} <b>No live feed is connected.</b> RINDA works from stored historical data. Each connector below is ready for an approved connection and fetches nothing until one exists.</div>
    <div class="grid g-2">${r.freshness.map((f) => `${card(esc(f.name), `<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px">${pill(f.label)} ${tag(CS, f.connector_state)}</div>
      <div class="kvs"><div><span>Last observation</span>${f.last_observation ? esc(f.last_observation.slice(0, 16).replace('T', ' ')) : NODATA}</div><div><span>Last successful ingestion</span>${f.last_ingestion ? esc(f.last_ingestion.slice(0, 16).replace('T', ' ')) : 'None'}</div>
        <div><span>Expected update</span>${f.expected_update_days >= 1 ? `every ${f.expected_update_days} days` : 'hourly'}</div><div><span>Records held</span>${f.records ? fmt.n(f.records) : NODATA}</div>
        <div><span>Processing</span>${esc(f.processing_status)}</div><div><span>Validation</span>${esc(f.validation_status)}</div></div>
      <p style="margin:10px 0 0;color:var(--ink-2);font-size:13px">${esc(f.notes || '')}</p><p class="hint" style="margin:6px 0 0"><b>To connect:</b> ${esc(con[f.key].needs)}</p>`,
    { tools: ST.can('manage:data') && f.connector_state !== 'internal' ? `<button class="btn sm" data-run="${f.key}">${icon('sync')}Check connection</button>` : '' })}`).join('')}</div>
    <div style="margin-top:18px">${card('Ingestion log', t.html, { flush: true })}</div>`;
  box.querySelectorAll('[data-run]').forEach((b) => b.addEventListener('click', async () => {
    try { const x = await api.post(`/api/connectors/${b.dataset.run}/run`); toast(x.status === 'unavailable' ? `Not connected. ${x.message}` : x.message, x.status === 'unavailable'); sources(box); } catch (er) { toast(er.message, true); }
  }));
}

// ---------------------------------------------------------------- inventory
async function inventory(box) {
  const r = await api.get('/api/earth/inventory');
  const years = [...new Set(r.collections.flatMap((c) => Object.keys(c.by_year)))].sort();
  const dyn = r.collections.filter((c) => Object.keys(c.by_year).length);
  const t = table([
    { h: 'Dataset', f: (c) => `<b>${esc(c.label)}</b><br><span class="hint">${esc(c.kind)} · ${esc(c.source)}</span>` },
    { h: 'Scenes', r: 1, f: (c) => (c.scenes == null ? NODATA : fmt.n(c.scenes)) }, { h: 'Period', f: (c) => (c.first ? `${c.first} to ${c.last}` : 'Static layer') },
    { h: 'Resolution', r: 1, f: (c) => `${c.resolution_m} m` }, { h: 'Median cloud', r: 1, f: (c) => (c.median_cloud_pct == null ? '–' : `${c.median_cloud_pct}%`) },
    { h: 'Scenes under 20% cloud', r: 1, f: (c) => c.clear_scenes ?? '–' },
    { h: 'What RINDA holds', f: (c) => `<span style="white-space:normal">${esc(c.held)}</span>` }, { h: 'Quality', f: (c) => `<span style="white-space:normal">${esc(c.quality)}</span>` },
  ], r.collections, { max: 620 });
  box.innerHTML = `<div class="note" style="margin-bottom:18px">${icon('info')} ${esc(r.note)}</div>
    ${card('Earth-observation and reference data · ' + esc(r.period), t.html, { flush: true })}
    <div class="grid g-main" style="margin-top:18px">
      ${card('Scenes catalogued per year', '<div class="chart lg" id="invY"></div>', { sub: 'One line per collection', foot: '<span>2026 is a part year.</span>' })}
      ${card(esc(r.weather.label), `<p style="margin:0 0 8px">${esc(r.weather.held)}</p><p class="hint" style="margin:0">${esc(r.weather.quality)}</p>`, { sub: 'Weather record', foot: '<a href="#/network">Station record</a>' })}
    </div>`;
  chart('invY', { grid: { left: 44, right: 16, top: 40, bottom: 28 }, legend: { data: dyn.map((c) => c.label) }, xAxis: { type: 'category', data: years }, yAxis: { type: 'value' },
    series: dyn.map((c, i) => ({ name: c.label, type: 'line', symbolSize: 7, data: years.map((y) => c.by_year[y] ?? null), lineStyle: { width: 2, color: css(`--s-${i + 1}`) }, itemStyle: { color: css(`--s-${i + 1}`) } })) });
}

// --------------------------------------------------------------- efficiency
async function efficiency(box) {
  const r = await api.get('/api/models/efficiency');
  const e = r.edge;
  box.innerHTML = `<div class="grid g-2">
    ${card('Forecast models on this server', table([
      { h: 'Model', f: (m) => `<span class="mono">${esc(m.model)}</span>` }, { h: 'Inputs', r: 1, f: (m) => m.n_features ?? '–' },
      { h: 'One forecast issue', r: 1, f: (m) => (m.issue_latency_ms == null ? NODATA : `${m.issue_latency_ms} ms for ${fmt.n(m.rows_timed)} locations`) },
      { h: 'Per 1,000 rows', r: 1, f: (m) => (m.ms_per_1000_rows == null ? NODATA : `${m.ms_per_1000_rows} ms`) },
      { h: 'Model file', r: 1, f: (m) => (m.model_bytes ? `${fmt.n(m.model_bytes / 1e6, 1)} MB` : NODATA) },
    ], r.served).html + `<div class="card-b"><div class="kvs"><div><span>Server memory in use</span>${r.process_rss_mb ? `${fmt.n(r.process_rss_mb, 0)} MB` : NODATA}</div><div><span>Measured on</span>${esc(r.hardware)}</div></div><p class="hint" style="margin:10px 0 0">${esc(r.scope)} Each trained candidate also records its own inference time, size and training memory under Training runs.</p></div>`, { flush: true, sub: 'Measured just now' })}
    ${card('Edge deployment readiness', `<div style="margin-bottom:10px"><span class="status st-mute">${esc(e.status)}</span></div>
      <div class="kvs" style="grid-template-columns:1fr"><div><span>Target hardware</span>${esc(e.target)}</div><div><span>What it applies to</span>${esc(e.applies_to)}</div></div>
      <p style="margin:12px 0 6px;font-weight:600;font-size:13px">Measurements still needed</p><ul style="margin:0;padding-left:18px;color:var(--ink-2)">${e.required.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>
      <p class="hint" style="margin:10px 0 0">${esc(e.where)} ${e.flight_logs.note ? esc(e.flight_logs.note) : e.flight_logs.available ? '' : 'No flight log has been loaded.'}</p>`, { sub: 'Objective 3', foot: '<a href="#/flight">Flight performance</a>' })}
  </div>`;
}
