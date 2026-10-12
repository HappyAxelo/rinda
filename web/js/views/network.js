import { api, card, kpi, fmt, icon, chart, css, series, table, makeMap } from '../ui.js';

let map;
export function destroy() { map?.remove(); map = null; }

const BOM = [
  ['Temperature / humidity', 'SHT31/SHT35 or BME280', 15000], ['Rain gauge', 'Tipping bucket, 0.2–0.5 mm', 60000],
  ['Wind speed and direction', 'Cup anemometer + vane', 80000], ['Water level', 'Waterproof ultrasonic or contact', 30000],
  ['GNSS', 'u-blox NEO-M8N / M10', 35000], ['Microcontroller', 'ESP32-WROOM', 15000],
  ['Power', '10–20 W solar, LiFePO4, controller', 70000], ['Uplink', '4G LTE modem + antenna', 60000],
];

export async function render(el, st) {
  const [sum, met, tel] = await Promise.all([api.get('/api/meteo/summary'), api.get('/api/meteo'), api.get('/api/telemetry')]);
  const host = location.origin;
  el.innerHTML = `
  <div class="page-head">
    <div><h1>Field network</h1><p>Ground observations that anchor the forecast between satellite passes: the Gikomero automatic weather station today, and solar ESP32 nodes reporting rainfall, water level and wind over LTE as they are commissioned.</p></div>
  </div>
  <div class="grid g-4">
    ${kpi('Weather record', fmt.n(sum.days), `${fmt.date(sum.first)} to ${fmt.date(sum.last)}`, { unit: 'days', accent: true })}
    ${kpi('Day coverage', fmt.pct(sum.coverage), 'Days with a daily summary over the record span')}
    ${kpi('Humidity usable', fmt.pct(sum.humidity_complete_share), 'Incomplete days are excluded, never imputed: they read ~13 points low')}
    ${kpi('Field nodes reporting', `${tel.filter((t) => t.packets > 0).length} <small>of ${tel.length}</small>`, 'Registered ESP32 stations with at least one packet')}
  </div>
  <div class="grid g-main" style="margin-top:14px">
    <div class="grid" style="align-content:start">
      ${card('Daily rainfall', '<div class="chart" id="nwRain"></div>', { sub: 'mm per day · Gikomero AWS' })}
      ${card('Air temperature', '<div class="chart" id="nwTemp"></div>', { sub: 'Daily minimum–maximum band and mean, °C' })}
      ${card('Relative humidity', '<div class="chart sm" id="nwHum"></div>', { sub: 'Daily mean; hollow points failed the completeness gate' })}
    </div>
    <div class="grid" style="align-content:start">
      ${card('Station positions', '<div class="map" id="nwMap" style="height:300px"></div>', { flush: true })}
      ${card('Field nodes', table([
        { h: 'Node', f: (t) => `<span class="mono">${t.station_id}</span>` }, { h: 'Packets', r: 1, f: (t) => fmt.n(t.packets) },
        { h: 'Delivery', r: 1, f: (t) => (t.pdr == null ? '–' : fmt.pct(t.pdr)) },
        { h: 'Status', f: (t) => (t.packets ? '<span class="status st-good">Reporting</span>' : '<span class="status st-mute">Pending</span>') },
      ], tel).html, { flush: true, sub: 'Packet delivery ratio from sequence gaps (RQ-D)' })}
      ${card('Node bill of materials', table([{ h: 'Component', k: 0, f: (r) => r[0] }, { h: 'Specification', f: (r) => r[1] }, { h: 'RWF', r: 1, f: (r) => fmt.n(r[2]) }], BOM).html, { flush: true, foot: `<span>Per node</span><b class="num">RWF ${fmt.n(BOM.reduce((a, r) => a + r[2], 0))}</b>` })}
    </div>
  </div>
  <div class="grid g-2" style="margin-top:14px">
    ${card('Node uplink contract', `<p style="margin:0 0 8px;font-size:12.5px;color:var(--ink-2)">Each node posts one JSON record per sampling interval. The sequence number lets the server measure delivery ratio and outage length without clock trust.</p>
<div class="code-block">POST ${host}/api/telemetry/ESP32-GKM-01
X-Station-Key: &lt;per-node key from var/station_keys.json&gt;
Content-Type: application/json

{
  "seq": 18234,
  "ts_utc": "2026-01-19T06:00:00Z",
  "rain_mm": 0.4,
  "temp_c": 18.6,
  "rh_pct": 82.1,
  "water_level_cm": 7.5,
  "wind_ms": 1.8,
  "wind_dir_deg": 140,
  "batt_v": 13.21,
  "rssi_dbm": -87
}</div>`)}
    ${card('Monthly rainfall', '<div class="chart" id="nwMonth"></div>', { sub: 'Total mm per month' })}
  </div>`;

  const dates = met.map((r) => r.date);
  const dz = [{ type: 'inside', start: 70, end: 100 }];
  chart('nwRain', { grid: { left: 40, right: 12, top: 12, bottom: 24 }, dataZoom: dz, xAxis: { type: 'category', data: dates }, yAxis: { type: 'value', name: 'mm' },
    series: [{ type: 'bar', data: met.map((r) => r.rainfall_mm), itemStyle: { color: series(1), borderRadius: [2, 2, 0, 0] }, barMaxWidth: 6 }] });
  chart('nwTemp', { grid: { left: 40, right: 12, top: 12, bottom: 24 }, dataZoom: dz, xAxis: { type: 'category', data: dates }, yAxis: { type: 'value', name: '°C', scale: true },
    tooltip: { trigger: 'axis', valueFormatter: (v) => `${(+v).toFixed(1)} °C` },
    series: [
      { name: 'Minimum', type: 'line', stack: 'band', showSymbol: false, data: met.map((r) => r.temp_min_c), lineStyle: { opacity: 0 }, itemStyle: { color: series(2) } },
      { name: 'Range', type: 'line', stack: 'band', showSymbol: false, data: met.map((r) => (r.temp_max_c ?? 0) - (r.temp_min_c ?? 0)), lineStyle: { opacity: 0 }, areaStyle: { color: series(2), opacity: .15 }, tooltip: { show: false } },
      { name: 'Mean', type: 'line', showSymbol: false, data: met.map((r) => r.temp_mean_c), lineStyle: { width: 1.6, color: series(2) }, itemStyle: { color: series(2) } }] });
  chart('nwHum', { grid: { left: 40, right: 12, top: 12, bottom: 24 }, dataZoom: dz, xAxis: { type: 'category', data: dates }, yAxis: { type: 'value', name: '%', min: 0, max: 100 },
    series: [{ type: 'scatter', symbolSize: 4, data: met.map((r) => ({ value: r.humidity_mean_pct, itemStyle: r.humidity_complete ? { color: series(7) } : { color: 'transparent', borderColor: css('--warn'), borderWidth: 1.2 } })) }] });
  chart('nwMonth', { grid: { left: 44, right: 12, top: 12, bottom: 30 }, xAxis: { type: 'category', data: sum.monthly.map((m) => m.month) }, yAxis: { type: 'value', name: 'mm' },
    tooltip: { trigger: 'axis', valueFormatter: (v) => `${fmt.n(v, 1)} mm` },
    series: [{ type: 'bar', data: sum.monthly.map((m) => m.rain), itemStyle: { color: series(1), borderRadius: [3, 3, 0, 0] }, barMaxWidth: 16 }] });

  map = makeMap('nwMap', { zoom: 12 });
  const sites = st.boot.sites;
  sites.forEach((s) => L.circleMarker([s.latitude, s.longitude], { radius: 2.4, color: 'transparent', fillColor: css('--ink-3'), fillOpacity: .7 }).addTo(map));
  L.marker([sum.station.lat, sum.station.lon], { icon: L.divIcon({ className: '', html: '<div class="station-pin"></div>', iconSize: [16, 16] }) }).bindTooltip(`${sum.station.name}<br>${sum.station.elevation_m} m`, { permanent: false }).addTo(map);
  map.fitBounds(sites.map((s) => [s.latitude, s.longitude]), { padding: [10, 10] });
}
