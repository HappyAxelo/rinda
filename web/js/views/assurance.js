import { api, card, kpi, fmt, icon, chart, css, series, table } from '../ui.js';

const REF_LABEL = { model: 'RINDA model', B0_climatology: 'Climatology (base rate)', B1_recent_water_freq_30d: 'Persistence: 30-day water frequency', B2_recent_water_7d: 'Persistence: wet in last 7 days', B3_recent_formation_30d: 'Persistence: formed in last 30 days' };
const SCHEME = { V1_random: 'Random split (optimistic)', V2_spatial_block: 'Spatial blocks', V3_temporal_block: 'Temporal blocks', V4_forward_chain: 'Forward chaining', V3_leave_block_out: 'Leave-block-out' };
const ARM = { A_meteo_only: 'A · weather only', B_s1_only: 'B · radar only', D_iot_meteo: 'D · IoT + weather', E_s1_s2: 'E · radar + optical', F_eo_meteo: 'F · radar + weather', G_eo_meteo_terrain: 'G · + terrain', H_all_sources: 'H · all sources', I_full_temporal: 'I · + sequence model' };

export async function render(el, st) {
  const h = st.h;
  const a = await api.get('/api/assurance');
  const H = a.horizons[h], ho = H.holdout, sc = ho.scores, eng = a.engine.horizons[h];
  const refs = a.references.filter((r) => r.horizon === h);
  const best = refs.filter((r) => r.reference !== 'model').sort((x, y) => y.pr_auc - x.pr_auc)[0];
  const model = refs.find((r) => r.reference === 'model');
  const beaten = best.pr_auc > model.pr_auc;

  const v3 = a.v3 ? v3Section(a.v3, h) : '';
  const v1win = ho.test_window || [eng.test_window[0], eng.test_window[1]];

  el.innerHTML = `${v3}
  <div class="page-head" ${a.v3 ? 'style="margin-top:28px"' : ''}>
    <div><h1>${a.v3 ? `Study run v1 (archived Paper 1 analysis) · ${h}-day` : `Model assurance · ${h}-day`}</h1><p>${a.v3 ? 'The first published model, kept as the reference the newer versions are measured against. ' : ''}Every figure below comes from one untouched temporal test window the model never saw during fitting, calibration or threshold choice. Accuracy is deliberately absent: predicting "no formation" everywhere scores ${fmt.pct(sc.majority_accuracy)}.</p></div>
    ${a.v3 ? '' : `<div class="actions"><a class="btn" href="/api/v1/assurance" target="_blank">${icon('file')}Raw assurance record</a></div>`}
  </div>
  <div class="grid g-4">
    ${kpi('PR-AUC', sc.pr_auc.toFixed(3), `95% block-bootstrap CI ${ho.pr_auc_ci.lo.toFixed(3)} – ${ho.pr_auc_ci.hi.toFixed(3)} · base rate ${sc.base_rate.toFixed(3)}`, { accent: true })}
    ${kpi('Recall / precision', `${sc.recall.toFixed(2)} <small>/</small> ${sc.precision.toFixed(2)}`, `At threshold ${ho.threshold.toFixed(3)} · MCC ${sc.mcc.toFixed(3)}`)}
    ${kpi('Calibration', sc.ece.toFixed(4), `Expected calibration error · Brier ${sc.brier.toFixed(4)}`, { unit: 'ECE' })}
    ${kpi('Conformal coverage', fmt.pct(ho.conformal.empirical_coverage), `Target ${fmt.pct(ho.conformal.target_coverage, 0)} · ${fmt.pct(ho.conformal.pct_uncertain)} of sites sent to verify-first`)}
  </div>
  ${beaten ? `<div class="note warn" style="margin-top:14px"><b>A simple reference beats the model at this horizon.</b> ${REF_LABEL[best.reference]} reaches PR-AUC ${best.pr_auc.toFixed(3)} against the model's ${model.pr_auc.toFixed(3)}. ${h === 14 ? 'Without a rainfall forecast the model cannot see rain falling between issue and t+14; persistence carries more of the signal. Operational guidance: treat 14-day output as an early outlook and act on the 7-day issue.' : 'Recent water history at a site is the strongest single signal of where water will reappear. What the model adds is a calibrated probability, a 90%-confidence call that routes doubtful sites to verification, and sites with no recent water history. Next study iteration: feed the persistence rule in as a feature and test the gain on the same holdout.'}</div>` :
    `<div class="note good" style="margin-top:14px"><b>The model beats every simple reference at this horizon,</b> though the best persistence rule (${REF_LABEL[best.reference]}, PR-AUC ${best.pr_auc.toFixed(3)}) is close. The margin, not the headline, is the honest measure of what the radar-plus-weather model adds.</div>`}
  <div class="grid g-2" style="margin-top:14px">
    ${card('Against reference forecasts', '<div class="chart" id="asRef"></div>', { sub: 'PR-AUC on the same test rows' })}
    ${card('How optimistic would a random split have been?', '<div class="chart" id="asCv"></div>', { sub: 'Gradient-boosted trees, mean ± SD across folds', foot: '<span>Random splits leak neighbouring sites and dates into training. Reported skill always uses blocked or forward-chained validation.</span>' })}
    ${card('Reliability', '<div class="chart" id="asRel"></div>', { sub: 'Predicted probability vs observed frequency', foot: `<span>Brier ${sc.brier.toFixed(4)} = reliability ${ho.brier_decomposition.reliability_MCB.toFixed(5)} − resolution ${ho.brier_decomposition.resolution_DSC.toFixed(4)} + uncertainty ${ho.brier_decomposition.uncertainty_UNC.toFixed(4)}</span>` })}
    ${card('Source ablation', '<div class="chart" id="asAbl"></div>', { sub: 'PR-AUC by input set, spatial-block folds', foot: '<span>Arms that need Sentinel-2 or terrain currently equal their parents; they separate once those exports are connected.</span>' })}
    ${card('What drives the forecast', '<div class="chart" id="asShap"></div>', { sub: 'Share of mean |SHAP| and permutation loss by source' })}
    ${card('Rainfall memory', '<div class="chart" id="asLag"></div>', { sub: 'Mean |SHAP| of rainfall terms by look-back window' })}
  </div>
  <div class="grid g-2" style="margin-top:14px">
    ${card('By season and rainfall regime', table([
      { h: 'Season', f: (r) => r.season.replace('_', ' ') }, { h: 'Regime', k: 'rainfall_regime' }, { h: 'Rows', r: 1, f: (r) => fmt.n(r.n) }, { h: 'Formations', r: 1, f: (r) => r.n_positive },
      { h: 'Base rate', r: 1, f: (r) => r.base_rate.toFixed(3) }, { h: 'PR-AUC', r: 1, f: (r) => r.pr_auc.toFixed(3) }, { h: 'Recall', r: 1, f: (r) => r.recall.toFixed(2) }, { h: 'MCC', r: 1, f: (r) => r.mcc.toFixed(3) },
    ], H.stratified, { max: 300 }).html, { flush: true, sub: 'Generalisation across conditions (RQ5)' })}
    ${card('Fusion strategy', table([
      { h: 'Strategy', f: (r) => ({ early: 'Early (concatenate features)', late: 'Late (average branch outputs)' }[r.strategy] || r.strategy) }, { h: 'PR-AUC', r: 1, f: (r) => r.pr_auc.toFixed(3) }, { h: 'ROC-AUC', r: 1, f: (r) => r.roc_auc.toFixed(3) }, { h: 'MCC', r: 1, f: (r) => r.mcc.toFixed(3) }, { h: 'Brier', r: 1, f: (r) => r.brier.toFixed(4) },
    ], H.fusion).html + `<div class="card-b" style="font-size:12px;color:var(--ink-3)">Intermediate (neural) fusion is gated off until the effective sample size supports a deep model.</div>`, { flush: true })}
    ${card('Engine integrity', `
      <div class="kv-row"><span>Algorithm</span><span>${eng.algorithm}</span></div>
      <div class="kv-row"><span>Features</span><span>${eng.n_features}</span></div>
      <div class="kv-row"><span>Train / calibrate / test</span><span class="mono">${eng.train_window[0].slice(0, 7)} → ${eng.train_window[1].slice(0, 7)} / → ${eng.val_window[1].slice(0, 7)} / ${eng.test_window[0].slice(0, 7)} →</span></div>
      <div class="kv-row"><span>Training rows (formations)</span><span>${fmt.n(eng.n_train)} (${fmt.n(eng.n_pos_train)})</span></div>
      ${eng.reproduction.published_pr_auc != null ? `<div class="kv-row"><span>Published PR-AUC / live engine</span><span>${eng.reproduction.published_pr_auc.toFixed(4)} / ${eng.reproduction.engine_pr_auc.toFixed(4)}</span></div>` : `<div class="kv-row"><span>Refit cadence</span><span>every ${eng.refit_every_days} days</span></div>`}
      <div class="kv-row"><span>Fingerprint</span><span class="mono">${eng.fingerprint}</span></div>
      <p style="font-size:12px;color:var(--ink-3);margin:10px 0 0">${a.v3 ? 'Served model (v3). ' : ''}The console refits the published configuration from the study table. Small differences come from multithreaded tree construction and stay well inside the bootstrap interval (±${(ho.pr_auc_ci.sd).toFixed(3)} SD).</p>`)}
    ${card('Leakage guard', `${Object.entries(H.leakage || {}).filter(([k]) => typeof H.leakage[k] === 'boolean').map(([k, v]) => `<div class="kv-row"><span>${k.replaceAll('_', ' ')}</span><span>${v ? `<span class="status st-good">${icon('check')}passed</span>` : `<span class="status st-crit">failed</span>`}</span></div>`).join('')}
      <p style="font-size:12px;color:var(--ink-3);margin:10px 0 0">${H.label.notes.join(' ')}</p>`, { sub: `table ${H.fingerprint}` })}
  </div>`;

  if (a.v3) v3Charts(a.v3, h);
  chart('asRef', {
    grid: { left: 210, right: 40, top: 6, bottom: 24 }, tooltip: { trigger: 'item', valueFormatter: (v) => v.toFixed(3) },
    xAxis: { type: 'value', max: 1 }, yAxis: { type: 'category', data: refs.map((r) => REF_LABEL[r.reference]).reverse() },
    series: [{ type: 'bar', barWidth: 12, label: { show: true, position: 'right', color: css('--ink-2'), fontSize: 11, formatter: (p) => p.value.toFixed(3) },
      data: refs.map((r) => ({ value: r.pr_auc, itemStyle: { color: r.reference === 'model' ? css('--brand') : css('--ink-3'), borderRadius: [0, 4, 4, 0] } })).reverse() }],
  });
  const cv = H.cv.filter((r) => r.model === 'xgboost');
  chart('asCv', {
    grid: { left: 170, right: 40, top: 6, bottom: 24 }, tooltip: { trigger: 'item', valueFormatter: (v) => v.toFixed(3) },
    xAxis: { type: 'value', max: 1 }, yAxis: { type: 'category', data: cv.map((r) => SCHEME[r.scheme] || r.scheme).reverse() },
    series: [{ type: 'bar', barWidth: 12, label: { show: true, position: 'right', color: css('--ink-2'), fontSize: 11, formatter: (p) => p.value.toFixed(3) },
      data: cv.map((r) => ({ value: r.pr_auc_mean, itemStyle: { color: r.scheme === 'V1_random' ? css('--warn') : series(1), borderRadius: [0, 4, 4, 0] } })).reverse() },
      { type: 'custom', renderItem: (p, api2) => { const lo = api2.coord([api2.value(1), api2.value(0)]), hi = api2.coord([api2.value(2), api2.value(0)]); return { type: 'line', shape: { x1: lo[0], y1: lo[1], x2: hi[0], y2: hi[1] }, style: { stroke: css('--ink'), lineWidth: 1.5 } }; },
        data: cv.map((r, i) => [cv.length - 1 - i, r.pr_auc_mean - r.pr_auc_std, r.pr_auc_mean + r.pr_auc_std]), tooltip: { show: false } }],
  });
  chart('asRel', {
    grid: { left: 48, right: 16, top: 16, bottom: 36 }, tooltip: { trigger: 'item', formatter: (p) => p.seriesIndex === 1 ? `predicted ${p.value[0].toFixed(3)}<br>observed ${p.value[1].toFixed(3)}<br>n = ${H.reliability[p.dataIndex].n}` : '' },
    xAxis: { type: 'value', min: 0, max: 1, name: 'mean predicted', nameLocation: 'middle', nameGap: 24 }, yAxis: { type: 'value', min: 0, max: 1, name: 'observed' },
    series: [{ type: 'line', data: [[0, 0], [1, 1]], showSymbol: false, lineStyle: { type: 'dashed', color: css('--ink-3'), width: 1 }, silent: true },
      { type: 'line', data: H.reliability.map((r) => [r.mean_predicted, r.observed_frequency]), symbolSize: (v, p) => 6 + Math.log10(H.reliability[p.dataIndex].n + 1) * 3, lineStyle: { width: 2, color: series(1) }, itemStyle: { color: series(1), borderColor: css('--s1'), borderWidth: 2 } }],
  });
  chart('asAbl', {
    grid: { left: 150, right: 40, top: 6, bottom: 24 }, tooltip: { trigger: 'item', valueFormatter: (v) => v.toFixed(3) },
    xAxis: { type: 'value', max: 1 }, yAxis: { type: 'category', data: H.ablation.map((r) => ARM[r.arm] || r.arm).reverse() },
    series: [{ type: 'bar', barWidth: 11, label: { show: true, position: 'right', color: css('--ink-2'), fontSize: 11, formatter: (p) => p.value.toFixed(3) },
      data: H.ablation.map((r) => ({ value: r.pr_auc_mean, itemStyle: { color: series(1), borderRadius: [0, 4, 4, 0] } })).reverse() }],
  });
  const grp = { sar: 'Radar (Sentinel-1)', meteo: 'Weather station', iot: 'Acquisition / IoT flags' };
  chart('asShap', {
    grid: { left: 150, right: 20, top: 30, bottom: 24 }, legend: { data: ['Share of |SHAP|', 'PR-AUC lost when shuffled'] }, tooltip: { trigger: 'axis', valueFormatter: (v) => (+v).toFixed(3) },
    xAxis: { type: 'value' }, yAxis: { type: 'category', data: H.shap_grouped.map((r) => grp[r.group] || r.group).reverse() },
    series: [{ name: 'Share of |SHAP|', type: 'bar', barWidth: 9, data: H.shap_grouped.map((r) => r.share).reverse(), itemStyle: { color: series(1), borderRadius: [0, 4, 4, 0] } },
      { name: 'PR-AUC lost when shuffled', type: 'bar', barWidth: 9, data: H.shap_grouped.map((r) => H.permutation.find((p) => p.group === r.group)?.importance_drop_pr_auc ?? 0).reverse(), itemStyle: { color: series(2), borderRadius: [0, 4, 4, 0] } }],
  });
  const lag = {}; H.rain_lag.forEach((r) => { lag[r.window_days] = (lag[r.window_days] || 0) + r.mean_abs_shap; });
  chart('asLag', {
    grid: { left: 48, right: 16, top: 16, bottom: 36 }, tooltip: { trigger: 'axis', valueFormatter: (v) => (+v).toFixed(3) },
    xAxis: { type: 'category', data: Object.keys(lag).map((d) => `${d} d`), name: 'look-back', nameLocation: 'middle', nameGap: 24 }, yAxis: { type: 'value', name: 'Σ mean |SHAP|' },
    series: [{ type: 'bar', barWidth: 22, data: Object.values(lag), itemStyle: { color: series(3), borderRadius: [4, 4, 0, 0] } }],
  });
}

const CAND = { R0_static_v2: 'Static fit (v2 design)', R1_refit28_v2: 'Refit every 28 d', R2_refit14_v2: 'Refit every 14 d', R3_refit28_v2_3seeds: 'Refit 28 d, 3 seeds', R4_refit28_altfeatures: 'Refit 28 d, other inputs' };

function v3Section(v, h) {
  const E = v.evaluation[h], f = E.full, hz = v.horizons[h], s = v.selection[h];
  const pt = f.precision_threshold, ft = f.f1_threshold, v2 = f.v2_same_rows;
  const cands = Object.entries(s.candidates).map(([k, c]) => ({ k, ...c }));
  return `
  <div class="page-head">
    <div><h1>Model assurance · served model v3 · ${h}-day</h1><p>Rolling out-of-sample forecasts ${fmt.date(f.date_from)} to ${fmt.date(f.date_to)}: the model was refitted every ${hz.refit_every_days} days on outcomes observable at the time, and its calibration and alert line came from earlier forecasts only. The Feb–Sep 2026 window was blind for v2 but not for v3.</p></div>
    <div class="actions"><a class="btn" href="/api/v1/assurance" target="_blank">${icon('file')}Raw assurance record</a></div>
  </div>
  <div class="grid g-4">
    ${kpi('PR-AUC', f.pr_auc.toFixed(3), `95% block-bootstrap CI ${f.ci.lo.toFixed(3)} – ${f.ci.hi.toFixed(3)} · base rate ${f.base_rate.toFixed(3)}`, { accent: true })}
    ${kpi('Alert precision / recall', `${pt.precision.toFixed(2)} <small>/</small> ${pt.recall.toFixed(2)}`, `High-precision alert line (target 0.75) · ${fmt.n(pt.alerts)} alerts`)}
    ${kpi('Best-F1 line', `${ft.precision.toFixed(2)} <small>/</small> ${ft.recall.toFixed(2)}`, `precision / recall · frozen v2 on same rows ${v2.v2_op.precision.toFixed(2)} / ${v2.v2_op.recall.toFixed(2)}`)}
    ${kpi('Against persistence', `+${(f.pr_auc - f.B1_pr_auc).toFixed(3)}`, `PR-AUC over the 30-day water-frequency rule (${f.B1_pr_auc.toFixed(3)}) · over v2 ${(v2.v3_minus_v2.delta >= 0 ? '+' : '') + v2.v3_minus_v2.delta.toFixed(3)} (CI ${v2.v3_minus_v2.lo.toFixed(3)} to ${v2.v3_minus_v2.hi.toFixed(3)})`)}
  </div>
  <div class="grid g-2" style="margin-top:14px">
    ${card('v3 against v2 and persistence', '<div class="chart" id="v3Cmp"></div>', { sub: 'PR-AUC on identical rows, by window' })}
    ${card('How v3 was chosen', table([
      { h: 'Candidate', f: (r) => CAND[r.k] || r.k }, { h: 'Inputs', r: 1, f: (r) => r.n_features },
      { h: 'Validation PR-AUC', r: 1, f: (r) => `${r.val_pr_auc.toFixed(3)}${r.k === s.chosen ? ' ✓' : ''}` },
    ], cands).html + `<div class="card-b" style="font-size:12px;color:var(--ink-3)">Rolling forecasts on Jan–Aug 2025 only (persistence ${cands[0].val_B1.toFixed(3)}). Rule fixed before scoring: highest validation PR-AUC.</div>`, { flush: true, sub: 'Selection used the validation window only' })}
  </div>`;
}

function v3Charts(v, h) {
  const E = v.evaluation[h];
  const wins = [['full', 'Sep 2025 – Sep 2026'], ['test', 'Sep 2025 – Jan 2026'], ['forward', 'Feb – Sep 2026']];
  const ser = (name, f, color) => ({ name, type: 'bar', barWidth: 10, data: wins.map(([k]) => f(E[k])), itemStyle: { color, borderRadius: [0, 4, 4, 0] } });
  chart('v3Cmp', {
    grid: { left: 140, right: 30, top: 30, bottom: 24 }, legend: { data: ['v3 (served)', 'v2 frozen', 'Persistence (B1)'] },
    tooltip: { trigger: 'axis', valueFormatter: (x) => (+x).toFixed(3) }, xAxis: { type: 'value', max: 1 },
    yAxis: { type: 'category', data: wins.map(([, l]) => l), inverse: true },
    series: [ser('v3 (served)', (w) => w.v2_same_rows.v3_pr_auc, css('--brand')), ser('v2 frozen', (w) => w.v2_same_rows.v2_pr_auc, series(2)), ser('Persistence (B1)', (w) => w.B1_pr_auc, css('--ink-3'))],
  });
}
