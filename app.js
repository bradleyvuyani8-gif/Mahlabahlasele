'use strict';
const $ = (s) => document.querySelector(s);
const state = { token: localStorage.getItem('mahla_token') || '', cfg: null, status: null };

/* ------------------------------------------------------------ helpers */
function el(tag, props = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === 'class') n.className = v; else if (k in n) n[k] = v; else n.setAttribute(k, v);
  }
  for (const c of kids.flat()) n.append(c instanceof Node ? c : document.createTextNode(c ?? ''));
  return n;
}
const fmt = (x, d = 2) => (x === null || x === undefined || Number.isNaN(x)) ? '-' : Number(x).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
const sign = (x, d = 2) => (x > 0 ? '+' : '') + fmt(x, d);
const when = (ms) => new Date(ms).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });

async function api(path, method = 'GET', body) {
  const r = await fetch('/api' + path, {
    method, headers: { 'X-Token': state.token, 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (r.status === 401) { showLogin(); throw new Error('Not signed in'); }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || r.statusText);
  return data;
}
function flash(node, text, ok) { node.textContent = text; node.className = 'msg ' + (ok ? 'ok' : 'bad'); }

/* ------------------------------------------------------------ login */
function showLogin() { $('#login').hidden = false; }
$('#loginForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  state.token = $('#tokenInput').value.trim();
  try {
    await api('/status');
    localStorage.setItem('mahla_token', state.token);
    $('#login').hidden = true; $('#loginErr').textContent = '';
    init();
  } catch { $('#loginErr').textContent = 'That token was not accepted.'; }
});

/* ------------------------------------------------------------ charts */
function drawLine(canvas, ys, opts = {}) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.height;
  canvas.width = w * dpr; canvas.height = h * dpr; canvas.style.height = h + 'px';
  const g = canvas.getContext('2d'); g.scale(dpr, dpr);
  const css = getComputedStyle(document.documentElement);
  const teal = css.getPropertyValue('--teal').trim(), loss = css.getPropertyValue('--loss').trim(), line = css.getPropertyValue('--line').trim();
  g.clearRect(0, 0, w, h);
  if (!ys.length) { g.fillStyle = css.getPropertyValue('--muted'); g.fillText('Waiting for data...', 8, 20); return; }
  const all = opts.halt != null ? [...ys, opts.halt] : ys;
  let lo = Math.min(...all), hi = Math.max(...all);
  if (opts.zero) { lo = Math.min(lo, 0); hi = Math.max(hi, 0); }
  const pad = (hi - lo) * 0.08 || 1; lo -= pad; hi += pad;
  const X = (i) => 4 + (ys.length < 2 ? 0 : i * (w - 8) / (ys.length - 1));
  const Y = (v) => h - 4 - (v - lo) / (hi - lo) * (h - 8);
  if (opts.zero) { g.strokeStyle = line; g.beginPath(); g.moveTo(0, Y(0)); g.lineTo(w, Y(0)); g.stroke(); }
  if (opts.halt != null) {
    g.strokeStyle = loss; g.setLineDash([5, 4]); g.beginPath(); g.moveTo(0, Y(opts.halt)); g.lineTo(w, Y(opts.halt)); g.stroke(); g.setLineDash([]);
  }
  g.strokeStyle = teal; g.lineWidth = 2; g.beginPath();
  ys.forEach((v, i) => i ? g.lineTo(X(i), Y(v)) : g.moveTo(X(i), Y(v))); g.stroke();
}

/* ------------------------------------------------------------ render */
function renderStatus(s) {
  state.status = s;
  const pill = $('#pill');
  const halted = s.breaker.tripped;
  pill.className = 'pill ' + (halted ? 'halt' : s.running ? 'on' : 'off');
  pill.textContent = halted ? 'Halted' : s.running ? `Running - ${s.mode}` : 'Stopped';
  $('#equity').textContent = fmt(s.equity);
  const pnl = s.equity - s.initial_balance;
  $('#pnl').textContent = `${sign(pnl)} (${sign(pnl / s.initial_balance * 100)}%) since start - ${s.symbol} ${s.timeframe}`;
  $('#pnl').className = 'sub ' + (pnl >= 0 ? 'up' : 'down');
  const stats = [['Price', fmt(s.price)], ['Drawdown', fmt(s.breaker.drawdown * 100) + '%'],
    ['Win rate', s.win_rate == null ? '-' : fmt(s.win_rate * 100, 0) + '%'], ['Trades', s.trades],
    ['API latency', s.latency_ms == null ? '-' : fmt(s.latency_ms, 0) + ' ms']];
  $('#stats').replaceChildren(...stats.map(([k, v]) => el('div', {}, el('dt', {}, k), el('dd', {}, String(v)))));

  const b = $('#breaker'); b.className = 'breaker' + (halted ? ' tripped' : '');
  b.replaceChildren(halted ? `Circuit breaker open: ${s.breaker.reason}. ` : `Circuit breaker closed. Peak ${fmt(s.breaker.peak)}, ${s.breaker.consecutive_losses} losses in a row.`);
  if (halted) b.append(el('button', { onclick: resetBreaker }, 'Reset breaker'));

  const L = s.last || {}, p = s.position, a = L.analysis, d = L.decision;
  const rows = [];
  rows.push(['Position', p ? `${p.side.toUpperCase()} ${fmt(p.qty, 6)} @ ${fmt(p.entry)} (stop ${fmt(p.sl)}, target ${fmt(p.tp)}, ${p.strategy}/${p.profile})` : 'Flat']);
  if (a) rows.push(['Market', `${a.trend} trend, ${a.volatility} volatility - RSI ${fmt(a.rsi, 0)}, volatility ${fmt(a.atr_pct * 100)}% per candle`]);
  if (d) rows.push(['Last decision', `${d.action.toUpperCase()} - ${d.reason}`]);
  if (L.explain) rows.push(['Crew review', L.explain]);
  $('#now').replaceChildren(...rows.flatMap(([k, v]) => [el('span', {}, k), el('span', {}, v)]));
  $('#btnStart').disabled = s.running; $('#btnStop').disabled = !s.running;
}

function renderEquity(points, s) {
  const ys = points.map((p) => p.equity);
  const halt = s && s.breaker.peak ? s.breaker.peak * (1 - (state.cfg?.max_drawdown ?? 0.1)) : null;
  drawLine($('#chart'), ys, { halt });
}

function renderLearning(l) {
  const imp = l.improving === null ? 'not enough trades yet' : l.improving ? 'improving' : 'not improving';
  const rows = [['Trades learned from', l.trades_learned], ['Exploration rate', fmt(l.epsilon * 100, 1) + '%'],
    ['Recent avg reward', fmt(l.avg_reward_recent, 3)], ['Trend', imp]];
  $('#learnStats').replaceChildren(...rows.flatMap(([k, v]) => [el('span', {}, k), el('span', {}, String(v))]));
  drawLine($('#rewardChart'), l.reward_curve, { zero: true });
  $('#armsTable tbody').replaceChildren(...l.top_arms.map((a) => {
    const [trend, vol, strat, prof] = a.arm.split('|');
    return el('tr', {}, el('td', {}, `${trend}/${vol} market: ${strat}, ${prof} stops`), el('td', { class: 'num' }, String(a.n)), el('td', { class: 'num' }, sign(a.q, 3)));
  }));
}

function renderTrades(list) {
  $('#tradesTable tbody').replaceChildren(...list.map((t) => el('tr', {},
    el('td', {}, when(t.t_close)), el('td', {}, t.side), el('td', {}, `${t.strategy}/${t.profile}`), el('td', {}, t.reason.replace('_', ' ')),
    el('td', { class: 'num ' + (t.net_pnl >= 0 ? 'up' : 'down') }, sign(t.net_pnl)),
    el('td', { class: 'num' }, sign(t.r_multiple)), el('td', { class: 'num' }, sign(t.reward ?? 0)))));
}

function renderLogs(list) {
  $('#logs').replaceChildren(...list.map((e) => el('li', { class: e.level }, `${e.ts.slice(11)}  ${e.msg}`)));
}

/* ------------------------------------------------------------ parameters + keys */
const FIELDS = [
  ['Market', [['symbol', 'Symbol', 'text'], ['timeframe', 'Candle size', ['1m', '3m', '5m', '15m', '30m', '1h', '4h', '1d']],
    ['data_source', 'Data source', ['exchange', 'synthetic', 'csv']], ['mode', 'Trading mode', ['paper', 'live']], ['poll_seconds', 'Check every (seconds)', 'number']]],
  ['Signals', [['sma_fast', 'Fast average (candles)', 'number'], ['sma_slow', 'Slow average (candles)', 'number'], ['rsi_period', 'RSI length', 'number'],
    ['rsi_oversold', 'RSI oversold below', 'number'], ['rsi_overbought', 'RSI overbought above', 'number'], ['signal_threshold', 'Minimum signal strength (0-1)', 'number', { step: 0.05 }]]],
  ['Risk', [['risk_per_trade', 'Risk per trade (% of equity)', 'number', { pct: 1, step: 0.1 }], ['min_rr', 'Minimum reward : risk', 'number', { step: 0.1 }],
    ['max_hold_bars', 'Close after (candles)', 'number'], ['max_drawdown', 'Halt at drawdown (%)', 'number', { pct: 1, step: 0.5 }],
    ['daily_loss_limit', 'Halt at daily loss (%)', 'number', { pct: 1, step: 0.5 }], ['max_consecutive_losses', 'Halt after losses in a row', 'number'],
    ['max_latency_ms', 'Pause entries if API slower than (ms)', 'number']]],
  ['Behaviour', [['allow_short', 'Allow short selling (turn off for spot)', 'check'], ['learning_enabled', 'Learn from trade rewards', 'check'], ['crew_enabled', 'CrewAI trade commentary', 'check']]],
];

function buildParams(cfg) {
  const form = $('#params');
  form.replaceChildren(...FIELDS.map(([group, items]) => el('fieldset', {}, el('legend', {}, group), ...items.map(([k, label, type, o = {}]) => {
    let input;
    if (Array.isArray(type)) { input = el('select', { id: 'f_' + k }, ...type.map((v) => el('option', { value: v }, v))); input.value = cfg[k]; }
    else if (type === 'check') { input = el('input', { id: 'f_' + k, type: 'checkbox', checked: !!cfg[k] }); return el('label', { class: 'check' }, input, label); }
    else { input = el('input', { id: 'f_' + k, type, step: o.step ?? 'any', value: o.pct ? +(cfg[k] * 100).toFixed(3) : cfg[k] }); }
    input.dataset.pct = o.pct ? '1' : '';
    return el('label', {}, label, input);
  }))), el('button', { class: 'primary', type: 'submit' }, 'Save parameters'));
}

$('#params').addEventListener('submit', async (e) => {
  e.preventDefault();
  const body = {};
  document.querySelectorAll('#params [id^=f_]').forEach((i) => {
    const k = i.id.slice(2);
    body[k] = i.type === 'checkbox' ? i.checked : i.type === 'number' ? (+i.value / (i.dataset.pct ? 100 : 1)) : i.value;
  });
  try { state.cfg = await api('/config', 'POST', body); buildParams(state.cfg); flash($('#paramsMsg'), 'Saved. Changes apply on the next check.', true); }
  catch (err) { flash($('#paramsMsg'), err.message, false); }
});

const KEY_FIELDS = [['exchange_api_key', 'Exchange API key'], ['exchange_api_secret', 'Exchange API secret'], ['telegram_bot_token', 'Telegram bot token (alerts)'],
  ['telegram_chat_id', 'Telegram chat ID'], ['llm_api_key', 'LLM API key (CrewAI commentary)']];
function buildKeys(status) {
  $('#keys').replaceChildren(...KEY_FIELDS.map(([k, label]) => el('label', {}, `${label}${status[k] ? ' - saved' : ''}`,
    el('input', { id: 'k_' + k, type: 'password', autocomplete: 'off', placeholder: status[k] ? 'Leave empty to keep' : '' }))),
    el('button', { class: 'primary', type: 'submit' }, 'Save connections'));
}
$('#keys').addEventListener('submit', async (e) => {
  e.preventDefault();
  const body = {};
  KEY_FIELDS.forEach(([k]) => { body[k] = $('#k_' + k).value; });
  try { buildKeys(await api('/keys', 'POST', body)); flash($('#keysMsg'), 'Saved.', true); }
  catch (err) { flash($('#keysMsg'), err.message, false); }
});

/* ------------------------------------------------------------ backtest */
function resultTable(title, r) {
  const rows = [['Return', sign(r.return_pct) + '%'], ['Buy and hold', sign(r.buy_hold_pct) + '%'], ['Trades', r.trades],
    ['Win rate', r.win_rate == null ? '-' : fmt(r.win_rate * 100, 0) + '%'], ['Profit factor', fmt(r.profit_factor)],
    ['Average R', sign(r.avg_r, 3)], ['Max drawdown', fmt(r.max_drawdown_pct) + '%']];
  const box = el('div', {}, el('h2', {}, title), el('div', { class: 'kv' }, ...rows.flatMap(([k, v]) => [el('span', {}, k), el('span', {}, String(v))])));
  const c = el('canvas', { height: 110 }); box.append(c);
  requestAnimationFrame(() => drawLine(c, r.equity_curve));
  return box;
}
$('#btRun').addEventListener('click', async () => {
  const btn = $('#btRun'); btn.disabled = true; flash($('#btMsg'), 'Running...', true);
  try {
    const r = await api('/backtest', 'POST', { source: $('#btSource').value, candles: +$('#btCandles').value, walk_forward: $('#btWalk').checked });
    const out = $('#btOut');
    out.replaceChildren(...(r.train ? [resultTable('Training period', r.train), resultTable('Unseen test period', r.test_out_of_sample)] : [resultTable('Result', r)]));
    const few = (r.train ? r.test_out_of_sample.trades : r.trades) < 30;
    flash($('#btMsg'), few ? 'Fewer than 30 trades: treat these numbers as noise, not evidence.' : 'Done.', !few);
  } catch (err) { flash($('#btMsg'), err.message, false); }
  btn.disabled = false;
});

/* ------------------------------------------------------------ actions + loop */
async function resetBreaker() { try { renderStatus(await api('/breaker/reset', 'POST')); } catch (e) { alert(e.message); } }
$('#btnStart').addEventListener('click', async () => { try { renderStatus(await api('/bot/start', 'POST')); } catch (e) { alert(e.message); } });
$('#btnStop').addEventListener('click', async () => { try { renderStatus(await api('/bot/stop', 'POST')); } catch (e) { alert(e.message); } });

async function refresh() {
  if (document.hidden) return;
  try {
    const [s, eq, tr, lg, ln] = await Promise.all([api('/status'), api('/equity'), api('/trades'), api('/logs'), api('/learning')]);
    renderStatus(s); renderEquity(eq, s); renderTrades(tr); renderLogs(lg); renderLearning(ln);
  } catch { /* shown via login or retried next cycle */ }
}
let timer;
async function init() {
  try {
    state.cfg = await api('/config'); buildParams(state.cfg); buildKeys(await api('/keys'));
    await refresh(); clearInterval(timer); timer = setInterval(refresh, 5000);
  } catch { /* login overlay handles auth */ }
}
if (state.token) init(); else showLogin();
