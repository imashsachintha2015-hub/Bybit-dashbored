#!/usr/bin/env node
/**
 * Server-side MASIS runner — generates and records signals without a browser.
 *
 * The dashboard engine runs in the page, so it stops the moment the tab is
 * backgrounded. On a phone that is nearly always: mobile browsers suspend
 * background tabs, so signals stop being produced and shadow outcomes stop
 * being settled exactly when nobody is looking. This runs the same engine in
 * Node, continuously.
 *
 * WHAT THIS DOES AND DOES NOT DO
 *
 * It generates candidates and records them. It does NOT place orders. That
 * split is deliberate for a first deployment: the valuable and reversible half
 * is continuous data collection, while an unattended process that can open
 * positions deserves to be turned on separately, after this one has been
 * watched. ENABLE_TRADING exists but defaults off and is not wired to the
 * order endpoint yet.
 *
 * CREDENTIALS
 *
 * None here. Signals are POSTed to the existing Vercel API, which already holds
 * the Bybit keys. An always-on box with trading credentials on it is a
 * materially different risk from a browser tab, and there is no reason to
 * accept that risk to collect data.
 *
 * MEMORY
 *
 * Railway bills by GB-minute, so footprint is cost. The symbol list is
 * deliberately short and configurable: order-book state dominates memory, and
 * Part XVIII found edge concentrated in the most liquid names anyway, so a
 * lean universe is both cheaper and better supported. Run with
 * --max-old-space-size to cap the heap.
 */
const WebSocket = require('ws');
const https = require('https');
const { MasisEngine } = require('../masis-engine.js');

const API_BASE = process.env.API_BASE || 'https://bybit-dashbored.vercel.app';
const SYMBOLS = (process.env.SYMBOLS ||
  'BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT,ADAUSDT,LINKUSDT,AVAXUSDT').split(',');
const WS_URL = 'wss://stream.bybit.com/v5/public/linear';
const ANALYSIS_INTERVALS = ['5', '15', '60'];
const TF_OF = { '5': 'ltf', '15': 'mtf', '60': 'htf' };
const DECISION_MS = Number(process.env.DECISION_MS || 15000);
const ENABLE_TRADING = process.env.ENABLE_TRADING === 'true';

const engines = {};
for (const s of SYMBOLS) engines[s] = new MasisEngine({ symbol: s, swarmMode: 'observe' });

const log = (...a) => console.log(new Date().toISOString(), ...a);

// ── HTTP helpers ───────────────────────────────────────────────────────────
function post(path, body) {
  return new Promise((resolve) => {
    const data = JSON.stringify(body);
    const req = https.request(`${API_BASE}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(data) },
      timeout: 10000,
    }, (res) => { res.resume(); res.on('end', () => resolve(res.statusCode)); });
    // Recording must never be able to take the process down.
    req.on('error', (e) => { log('post failed', path, e.message); resolve(null); });
    req.on('timeout', () => { req.destroy(); resolve(null); });
    req.write(data); req.end();
  });
}

function getJSON(url) {
  return new Promise((resolve) => {
    https.get(url, { timeout: 15000 }, (res) => {
      let b = '';
      res.on('data', (c) => { b += c; });
      res.on('end', () => { try { resolve(JSON.parse(b)); } catch { resolve(null); } });
    }).on('error', () => resolve(null)).on('timeout', function () { this.destroy(); resolve(null); });
  });
}

// ── Seed history, so the engine is not blind until enough live bars arrive ──
const OKX_BAR = { '5': '5m', '15': '15m', '60': '1H' };

async function fetchSeed(sym, iv) {
  // Bybit's REST endpoint refuses cloud IPs (the same reason
  // backtest/fetch-klines.js falls back to OKX), while its WEBSOCKET accepts
  // them. So the live feed works from a host that cannot fetch history, and
  // without a fallback the engine would start blind and stay that way for a
  // long time -- the 60m series needs 200 bars, which is over a week of waiting.
  const b = await getJSON(
    `https://api.bybit.com/v5/market/kline?category=linear&symbol=${sym}&interval=${iv}&limit=200`);
  const rows = b && b.result && b.result.list;
  if (rows && rows.length) {
    return rows.slice().reverse().map((r) => ({
      start: Number(r[0]), open: +r[1], high: +r[2], low: +r[3], close: +r[4], volume: +r[5],
    }));
  }
  const inst = sym.replace('USDT', '-USDT-SWAP');
  const o = await getJSON(
    `https://www.okx.com/api/v5/market/candles?instId=${inst}&bar=${OKX_BAR[iv]}&limit=200`);
  const orows = o && o.data;
  if (orows && orows.length) {
    return orows.slice().reverse().map((r) => ({
      start: Number(r[0]), open: +r[1], high: +r[2], low: +r[3], close: +r[4], volume: +r[5],
    }));
  }
  return null;
}

async function seed() {
  let ok = 0, miss = 0;
  for (const sym of SYMBOLS) {
    for (const iv of ANALYSIS_INTERVALS) {
      const candles = await fetchSeed(sym, iv);
      if (!candles) { log('seed miss', sym, iv); miss++; continue; }
      engines[sym].seedCandles(TF_OF[iv], candles);
      ok++;
    }
    await new Promise((r) => setTimeout(r, 150));   // stay well inside rate limits
  }
  log('seeded', ok, 'series,', miss, 'missing, across', SYMBOLS.length, 'symbols');
}

// ── Live feed ──────────────────────────────────────────────────────────────
let ws = null, reconnectDelay = 1000;

function topics() {
  const t = [];
  for (const s of SYMBOLS) {
    t.push(`tickers.${s}`, `publicTrade.${s}`, `orderbook.50.${s}`);
    for (const iv of ANALYSIS_INTERVALS) t.push(`kline.${iv}.${s}`);
  }
  return t;
}

function connect() {
  ws = new WebSocket(WS_URL);
  ws.on('open', () => {
    reconnectDelay = 1000;
    const all = topics();
    // Bybit rejects an entire subscribe batch if any one topic is malformed or
    // already subscribed, so send in small chunks rather than one large args array.
    for (let i = 0; i < all.length; i += 10) {
      ws.send(JSON.stringify({ op: 'subscribe', args: all.slice(i, i + 10) }));
    }
    log('ws open,', all.length, 'topics');
  });
  ws.on('message', (raw) => {
    let m; try { m = JSON.parse(raw); } catch { return; }
    if (!m.topic) return;
    const parts = m.topic.split('.');
    const sym = parts[parts.length - 1];
    const eng = engines[sym];
    if (!eng) return;
    try {
      if (m.topic.startsWith('kline.')) {
        const tf = TF_OF[parts[1]];
        if (tf && Array.isArray(m.data)) for (const k of m.data) eng.processKline(tf, k);
      } else if (m.topic.startsWith('tickers.')) {
        eng.processTicker(m.data);
      } else if (m.topic.startsWith('publicTrade.')) {
        eng.processTrades(m.data);
      } else if (m.topic.startsWith('orderbook.')) {
        eng.processOrderBook(m.data);
      }
    } catch (e) { /* one bad frame must not kill the feed */ }
  });
  ws.on('close', () => { log('ws closed, reconnecting in', reconnectDelay, 'ms'); schedule(); });
  ws.on('error', (e) => { log('ws error', e.message); try { ws.close(); } catch {} });
  // Bybit drops idle connections; a periodic ping keeps it alive.
  const ping = setInterval(() => {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ op: 'ping' }));
    else clearInterval(ping);
  }, 20000);
}

function schedule() {
  setTimeout(connect, reconnectDelay);
  reconnectDelay = Math.min(reconnectDelay * 2, 60000);   // back off, do not hammer
}

// ── Decision loop ──────────────────────────────────────────────────────────
const lastFingerprint = {};
const lastRecordedState = {};
const lastRecordedTime = {};

async function tick() {
  const now = Date.now();
  for (const sym of SYMBOLS) {
    let s;
    try { s = engines[sym].getState(); } catch (e) { continue; }
    if (!s || !s.setupType || !s.grade) continue;

    const fp = [sym, s.setupType, s.direction || ''].join('|');
    const decided = s.decision === 'BUY' || s.decision === 'SELL';
    const stateKey = `${fp}|${decided}|${s.grade}`;

    // De-duplicate: only post to Vercel when state changes or after 10m keepalive.
    // This reduces Vercel Serverless Function invocations and KV writes by >99%.
    const isNew = lastRecordedState[sym] !== stateKey;
    const isStale = (now - (lastRecordedTime[sym] || 0)) > 600000;
    if (!isNew && !isStale) continue;

    lastRecordedState[sym] = stateKey;
    lastRecordedTime[sym] = now;

    const why = (s.blockers || []).length
      ? s.blockers.join(' | ')
      : 'engine declined — no blocker reported (decision was not BUY/SELL)';

    await post('/api/trades/record?_action=signal', {
      fingerprint: fp, symbol: sym,
      direction: s.direction || (s.decision === 'BUY' ? 'LONG' : s.decision === 'SELL' ? 'SHORT' : ''),
      setup_type: s.setupType, grade: s.grade, score: s.score,
      regime: s.regime, bias: s.bias,
      entry: s.entry, stop: s.stopLoss, targets: s.takeProfit, risk_reward: s.riskReward,
      outcome: decided ? 'WOULD_TRADE' : 'NOT_TRADED',
      reject_reason: decided ? '' : why,
      flow: s.flow || null,
      evidence: (s.components || []).map((c) => `${c.label}: ${c.detail}`),
      observed_vetoes: s.observedVetoes || [],
      blocked_gates: Object.entries(s.gateChecks || {})
        .filter(([, passed]) => passed === false).map(([name]) => name),
    });

    if (decided && lastFingerprint[sym] !== fp) {
      lastFingerprint[sym] = fp;
      log('SIGNAL', sym, s.decision, s.setupType, `grade ${s.grade}`,
        ENABLE_TRADING ? '(trading enabled — not yet wired)' : '(recording only)');
    }
  }
}

// ── Boot ───────────────────────────────────────────────────────────────────
(async () => {
  log('starting:', SYMBOLS.length, 'symbols, decision every', DECISION_MS, 'ms,',
    'trading', ENABLE_TRADING ? 'ENABLED' : 'disabled');
  await seed();
  connect();
  setInterval(() => { tick().catch((e) => log('tick error', e.message)); }, DECISION_MS);
  setInterval(() => {
    const mb = Math.round(process.memoryUsage().rss / 1048576);
    // Engine readiness, not just liveness. "No setup present" is the normal
    // state, so a quiet log is indistinguishable from a broken engine unless
    // the heartbeat says whether the engines actually have data and are
    // forming candidates.
    let ready = 0, graded = 0, statusCounts = {};
    for (const sym of SYMBOLS) {
      let st; try { st = engines[sym].getState(); } catch { continue; }
      if (!st) continue;
      statusCounts[st.dataStatus || '?'] = (statusCounts[st.dataStatus || '?'] || 0) + 1;
      if (st.dataStatus && st.dataStatus !== 'INVALID') ready++;
      if (st.grade) graded++;
    }
    log(`heartbeat rss=${mb}MB ready=${ready}/${SYMBOLS.length} withCandidate=${graded} data=${JSON.stringify(statusCounts)}`);
  }, Number(process.env.HEARTBEAT_MS || 300000));
})();
