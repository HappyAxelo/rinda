import { api, card, kpi, fmt, icon, chart, css, series, catTag, catOf, toast, esc } from '../ui.js';
import { params } from '../app.js';

export async function render(el, st) {
  const h = st.h;
  const fc = await api.get(`/api/forecast?h=${h}&date=${st.date}`);
  const opts = fc.rows.slice(0, 80);
  let site = params().get('site') || opts[0]?.site_id;
  const date = fc.summary.issue_date;
  if (!fc.rows.some((r) => r.site_id === site)) site = opts[0]?.site_id;

  el.innerHTML = `
  <div class="page-head">
    <div><h1>Scenario lab</h1><p>Query the live forecast model directly. Rescale the rainfall a site has received, shift temperature, and watch the formation probability and the 90%-confidence call respond. Radar state is held fixed, so this isolates the weather pathway.</p></div>
  </div>
  <div class="grid g-main-l">
    <div class="grid" style="align-content:start">
      ${card('Query', `
        <div class="form">
          <div class="field"><label for="scSite">Site <span class="u">${h}-day model · issue ${date}</span></label>
            <select id="scSite">${opts.map((r) => `<option value="${r.site_id}" ${r.site_id === site ? 'selected' : ''}>${r.site_id} · priority ${(r.priority_score ?? 0).toFixed(3)} · ${r.conformal_decision.replace('_', ' ')}</option>`).join('')}
            ${opts.some((r) => r.site_id === site) ? '' : `<option selected value="${esc(site)}">${esc(site)}</option>`}</select></div>
          <div class="field"><label>Antecedent rainfall <span class="u" id="rV">1.00 ×</span></label><input type="range" id="rM" min="0" max="3" step="0.05" value="1"></div>
          <div class="field"><label>Temperature shift <span class="u" id="tV">+0.0 °C</span></label><input type="range" id="tD" min="-4" max="4" step="0.5" value="0"></div>
          <div class="actions"><button class="btn sm" data-p="0,0">Dry spell</button><button class="btn sm" data-p="1,0">Observed</button><button class="btn sm" data-p="2,0">Twice the rain</button><button class="btn sm" data-p="3,1.5">Extreme wet, warm</button></div>
        </div>`)}
      ${card('Observed inputs', '<div id="scObs"></div>')}
      ${card('Batch scoring', `<p style="margin:0 0 10px;font-size:12.5px;color:var(--ink-2)">Score any table of engineered features with the ${h}-day model. Missing columns are reported and median-imputed exactly as in training.</p>
        <div class="actions"><a class="btn" href="/api/v1/score/template?h=${h}">${icon('download')}Feature template</a>${st.can('score:batch') ? `<label class="btn primary" for="scFile">${icon('upload')}Score a CSV</label><input type="file" id="scFile" accept=".csv" hidden>` : '<span class="hint">Batch scoring needs an analyst account.</span>'}</div>`)}
    </div>
    <div class="grid" style="align-content:start">
      <div class="grid g-3" id="scKpis"></div>
      ${card('Response to rainfall', '<div class="chart lg" id="scCurve"></div>', { sub: 'Calibrated probability as antecedent rainfall is rescaled', foot: '<span>Tree models respond in steps: flat stretches mean the site\'s rainfall is already beyond the split points the model learned.</span>' })}
      ${card('Drivers at the observed state', '<div class="chart" id="scDrv" style="height:300px"></div>', { sub: 'Top contributions on the log-odds scale' })}
    </div>
  </div>`;

  const run = async () => {
    const rain = +document.getElementById('rM').value, temp = +document.getElementById('tD').value;
    document.getElementById('rV').textContent = `${rain.toFixed(2)} ×`;
    document.getElementById('tV').textContent = `${temp >= 0 ? '+' : ''}${temp.toFixed(1)} °C`;
    const s = await api.get(`/api/scenario?site=${site}&date=${date}&h=${h}&rain=${rain}&temp=${temp}`);
    if (!s.available) { document.getElementById('scKpis').innerHTML = '<div class="note warn span-all">This site has no model row on this issue date.</div>'; return; }
    const d = s.scenario.probability - s.baseline.probability;
    document.getElementById('scKpis').innerHTML = [
      kpi('Observed state · live engine', s.baseline.probability.toFixed(3), catTag(catOf({ conformal_decision: s.baseline.decision }))),
      kpi('Scenario', s.scenario.probability.toFixed(3), catTag(catOf({ conformal_decision: s.scenario.decision })), { accent: true }),
      kpi('Change', `<span style="color:${d > 0.0005 ? 'var(--crit)' : d < -0.0005 ? 'var(--good)' : 'var(--ink)'}">${d >= 0 ? '+' : ''}${d.toFixed(3)}</span>`, `${s.perturbed_features} features perturbed`),
    ].join('');
    document.getElementById('scObs').innerHTML = `<dl class="dl"><dt>Rain, 7 days before issue</dt><dd>${fmt.n(s.observed_rain_7d, 1)} mm</dd><dt>Rain, 14 days before issue</dt><dd>${fmt.n(s.observed_rain_14d, 1)} mm</dd><dt>Scenario rain, 14 days</dt><dd>${s.observed_rain_14d == null ? 'missing' : `${fmt.n(s.observed_rain_14d * rain, 1)} mm`}</dd><dt>Weather source</dt><dd>Gikomero AWS</dd></dl>${s.missing_inputs?.length ? `<p style="margin:8px 0 0;font-size:12px;color:var(--warn)">${s.missing_inputs.length} inputs missing on this issue (e.g. ${s.missing_inputs.slice(0, 3).join(', ')}); the model imputes them with training medians.</p>` : ''}`;
    chart('scCurve', {
      grid: { left: 52, right: 20, top: 20, bottom: 40 }, tooltip: { trigger: 'axis', valueFormatter: (v) => (+v).toFixed(3) },
      xAxis: { type: 'value', name: 'rainfall multiplier', nameLocation: 'middle', nameGap: 26, min: 0, max: 3 }, yAxis: { type: 'value', name: 'probability', min: 0 },
      series: [{ type: 'line', step: 'middle', showSymbol: false, data: s.curve.map((c) => [c.rain_multiplier, c.probability]), lineStyle: { width: 2, color: series(1) }, areaStyle: { color: series(1), opacity: .07 },
        markLine: { symbol: 'none', silent: true, label: { color: css('--ink-2'), formatter: '{b}', position: 'insideEndTop' }, lineStyle: { type: 'dashed', color: css('--ink-3') }, data: [{ name: 'operating threshold', yAxis: st.boot.horizons[h].threshold }] },
        markPoint: { symbol: 'circle', symbolSize: 11, label: { show: false }, data: [
          { coord: [1, s.baseline.probability], itemStyle: { color: css('--ink'), borderColor: css('--s1'), borderWidth: 2 } },
          { coord: [rain, s.scenario.probability], itemStyle: { color: css('--signal'), borderColor: css('--s1'), borderWidth: 2 } }] } }],
    });
  };
  const drivers = async () => {
    const ex = await api.get(`/api/explain?site=${site}&date=${date}&h=${h}`);
    if (!ex.available) return;
    const dv = ex.drivers.slice(0, 12).reverse();
    const fam = { 'Radar surface state': 1, Rainfall: 3, Temperature: 2, Humidity: 7, 'Acquisition quality': 4 };
    chart('scDrv', {
      grid: { left: 180, right: 20, top: 8, bottom: 22 },
      tooltip: { trigger: 'item', formatter: (p) => `<b class="mono">${dv[p.dataIndex].feature}</b> · ${dv[p.dataIndex].family}<br>value ${dv[p.dataIndex].value == null ? 'missing' : dv[p.dataIndex].value.toFixed(2)}<br>contribution ${p.value.toFixed(3)}` },
      xAxis: { type: 'value' }, yAxis: { type: 'category', data: dv.map((x) => x.feature), axisLabel: { fontFamily: 'IBM Plex Mono', fontSize: 10.5 } },
      series: [{ type: 'bar', barWidth: 10, data: dv.map((x) => ({ value: x.contribution, itemStyle: { color: series(fam[x.family] || 5), borderRadius: x.contribution >= 0 ? [0, 4, 4, 0] : [4, 0, 0, 4] } })) }],
    });
  };
  let t;
  ['rM', 'tD'].forEach((i) => document.getElementById(i).addEventListener('input', () => { clearTimeout(t); t = setTimeout(run, 120); }));
  el.querySelectorAll('[data-p]').forEach((b) => b.addEventListener('click', () => { const [r, tt] = b.dataset.p.split(','); document.getElementById('rM').value = r; document.getElementById('tD').value = tt; run(); }));
  document.getElementById('scSite').addEventListener('change', (e) => { site = e.target.value; run(); drivers(); });
  document.getElementById('scFile')?.addEventListener('change', async (e) => {
    const f = e.target.files[0]; if (!f) return;
    const fd = new FormData(); fd.append('file', f);
    try {
      const r = await api.upload(`/api/score?h=${h}`, fd);
      const miss = r.headers.get('X-Missing-Features');
      const blob = await r.blob();
      const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = `scored_h${h}_${f.name}`; a.click();
      toast(`Scored ${f.name}${+miss ? ` · ${miss} features missing and imputed` : ''}`);
    } catch (er) { toast('Scoring failed: ' + er.message, true); }
    e.target.value = '';
  });
  await Promise.all([run(), drivers()]);
}
