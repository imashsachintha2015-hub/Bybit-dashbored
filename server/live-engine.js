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
const { PositionManager, EXIT } = require('../agents/position-manager.js');

const ALL_30_COINS = [
  'BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'DOGEUSDT', 'ADAUSDT', 'AVAXUSDT', 'LINKUSDT',
  'DOTUSDT', 'LTCUSDT', 'BCHUSDT', 'ATOMUSDT', 'NEARUSDT', 'APTUSDT', 'ARBUSDT', 'OPUSDT',
  'SUIUSDT', 'TONUSDT', 'TRXUSDT', 'SHIB1000USDT', 'UNIUSDT', 'FILUSDT', 'ETCUSDT', 'XLMUSDT',
  'ICPUSDT', 'HBARUSDT', 'INJUSDT', 'SEIUSDT', 'AAVEUSDT', 'ALGOUSDT'
];

const API_BASE = process.env.API_BASE || 'https://bybit-dashbored.vercel.app';
const SYMBOLS = (process.env.SYMBOLS ? process.env.SYMBOLS.split(',') : ALL_30_COINS);
const WS_URL = 'wss://stream.bybit.com/v5/public/linear';
const ANALYSIS_INTERVALS = ['5', '15', '60'];
const TF_OF = { '5': 'ltf', '15': 'mtf', '60': 'htf' };
const DECISION_MS = Number(process.env.DECISION_MS || 15000);
const ENABLE_TRADING = process.env.ENABLE_TRADING === 'true';
const SCALP_MODE = process.env.SCALP_MODE === 'true';

const engines = {};
for (const s of SYMBOLS) engines[s] = new MasisEngine({ symbol: s, swarmMode: 'observe', scalpMode: SCALP_MODE });
const positionManager = new PositionManager();

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

function postJSON(path, body) {
  return new Promise((resolve) => {
    const data = JSON.stringify(body);
    const req = https.request(`${API_BASE}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(data) },
      timeout: 10000,
    }, (res) => {
      let b = '';
      res.on('data', (c) => { b += c; });
      res.on('end', () => {
        try { resolve({ status: res.statusCode, data: JSON.parse(b) }); }
        catch { resolve({ status: res.statusCode, data: null }); }
      });
    });
    req.on('error', (e) => { log('postJSON failed', path, e.message); resolve({ status: 500, data: null }); });
    req.on('timeout', () => { req.destroy(); resolve({ status: 408, data: null }); });
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

// ── Bybit Lot Specifications & Min Sizing (Full 30-Coin Specs) ─────────────
const COIN_SPECS = {
  BTCUSDT: { qtyStep: 0.001, minQty: 0.001, minNotional: 5.0 },
  ETHUSDT: { qtyStep: 0.01, minQty: 0.01, minNotional: 5.0 },
  SOLUSDT: { qtyStep: 0.1, minQty: 0.1, minNotional: 5.0 },
  XRPUSDT: { qtyStep: 1, minQty: 1, minNotional: 5.0 },
  DOGEUSDT: { qtyStep: 1, minQty: 1, minNotional: 5.0 },
  ADAUSDT: { qtyStep: 1, minQty: 1, minNotional: 5.0 },
  AVAXUSDT: { qtyStep: 0.1, minQty: 0.1, minNotional: 5.0 },
  LINKUSDT: { qtyStep: 0.1, minQty: 0.1, minNotional: 5.0 },
  DOTUSDT: { qtyStep: 0.1, minQty: 0.1, minNotional: 5.0 },
  LTCUSDT: { qtyStep: 0.01, minQty: 0.01, minNotional: 5.0 },
  BCHUSDT: { qtyStep: 0.01, minQty: 0.01, minNotional: 5.0 },
  ATOMUSDT: { qtyStep: 0.1, minQty: 0.1, minNotional: 5.0 },
  NEARUSDT: { qtyStep: 0.1, minQty: 0.1, minNotional: 5.0 },
  APTUSDT: { qtyStep: 0.1, minQty: 0.1, minNotional: 5.0 },
  ARBUSDT: { qtyStep: 1, minQty: 1, minNotional: 5.0 },
  OPUSDT: { qtyStep: 1, minQty: 1, minNotional: 5.0 },
  SUIUSDT: { qtyStep: 1, minQty: 1, minNotional: 5.0 },
  TONUSDT: { qtyStep: 0.1, minQty: 0.1, minNotional: 5.0 },
  TRXUSDT: { qtyStep: 1, minQty: 1, minNotional: 5.0 },
  SHIB1000USDT: { qtyStep: 1000, minQty: 1000, minNotional: 5.0 },
  UNIUSDT: { qtyStep: 0.1, minQty: 0.1, minNotional: 5.0 },
  FILUSDT: { qtyStep: 0.1, minQty: 0.1, minNotional: 5.0 },
  ETCUSDT: { qtyStep: 0.01, minQty: 0.01, minNotional: 5.0 },
  XLMUSDT: { qtyStep: 1, minQty: 1, minNotional: 5.0 },
  ICPUSDT: { qtyStep: 0.1, minQty: 0.1, minNotional: 5.0 },
  HBARUSDT: { qtyStep: 1, minQty: 1, minNotional: 5.0 },
  INJUSDT: { qtyStep: 0.1, minQty: 0.1, minNotional: 5.0 },
  SEIUSDT: { qtyStep: 1, minQty: 1, minNotional: 5.0 },
  AAVEUSDT: { qtyStep: 0.01, minQty: 0.01, minNotional: 5.0 },
  ALGOUSDT: { qtyStep: 1, minQty: 1, minNotional: 5.0 }
};

function calculateOrderQty(sym, price, autoState) {
  const spec = COIN_SPECS[sym] || { qtyStep: 0.001, minQty: 0.001, minNotional: 5.0 };
  const step = spec.qtyStep;
  const minQ = spec.minQty || step;
  const minNotional = spec.minNotional || 5.0;

  if (autoState && autoState.sizingMode === 'usdt' && autoState.fixedUsdtSize > 0) {
    let q = autoState.fixedUsdtSize / price;
    q = Math.floor(q / step) * step;
    if (q * price < minNotional) q = Math.ceil(minNotional / price / step) * step;
    if (q < minQ) q = minQ;
    return +q.toFixed(8);
  }

  // Default: 'min' sizing mode (smallest valid legal order on Bybit)
  let q = Math.max(minQ, Math.ceil(minNotional / price / step) * step);
  q = Math.floor(q / step) * step;
  return +q.toFixed(8);
}

// ── Cross-Device Arm State & Open Positions Cache ─────────────────────────
let currentAutoState = {
  armed: false,
  sizingMode: 'min',
  fixedUsdtSize: 10,
  leverage: 10,
  marginMode: 'cross',
  maxConcurrentPositions: 5
};
let openPositionsCache = [];
let lastStateSync = 0;
const activeOrders = {}; // sym -> { orderId, placedAt, side, price, qty }

async function syncAutoTradeState() {
  const now = Date.now();
  if (now - lastStateSync < 12000) return;
  lastStateSync = now;

  try {
    const st = await getJSON(`${API_BASE}/api/auto-trade/state`);
    if (st && typeof st.armed === 'boolean') {
      if (st.armed !== currentAutoState.armed) {
        log(`[STATE SYNC] 24/7 cloud execution ${st.armed ? 'ARMED' : 'STOPPED'} via dashboard.`);
      }
      currentAutoState = Object.assign(currentAutoState, st);
    }
  } catch (e) {}

  try {
    const pos = await getJSON(`${API_BASE}/api/positions`);
    if (pos && pos.result && Array.isArray(pos.result.list)) {
      openPositionsCache = pos.result.list.filter(p => parseFloat(p.size || 0) > 0);
      if (currentAutoState && currentAutoState.theses) {
        const openKeys = new Set(openPositionsCache.map(p => `${p.symbol}-${p.side}`));
        const toPrune = {};
        let pruneCount = 0;
        for (const [k, th] of Object.entries(currentAutoState.theses)) {
          if (!th) continue;
          const age = now - (th.openedAt || 0);
          const hasActiveOrder = activeOrders[th.symbol] && (now - activeOrders[th.symbol].placedAt < 600000);
          if (age > 120000 && !openKeys.has(k) && !hasActiveOrder) {
            toPrune[k] = null;
            delete currentAutoState.theses[k];
            pruneCount++;
          }
        }
        if (pruneCount > 0) {
          await postJSON('/api/auto-trade/state', { theses: toPrune });
          log(`[STATE SYNC] Pruned ${pruneCount} closed trade thesis(es) from server.`);
        }
      }
    }
  } catch (e) {}
}

// ── Position Guardian & TP/SL Automation ──────────────────────────────────
async function manageOpenPositions() {
  if (!openPositionsCache.length) return;
  const now = Date.now();
  for (const pos of openPositionsCache) {
    const sym = pos.symbol;
    const side = pos.side;
    const key = `${sym}-${side}`;
    let trade = (currentAutoState.theses && currentAutoState.theses[key]) || positionManager.trades[key];
    const mark = parseFloat(pos.markPrice || pos.avgPrice);
    const entry = parseFloat(pos.avgPrice);
    const stop = parseFloat(pos.stopLoss);
    const size = parseFloat(pos.size);

    const eng = engines[sym];

    if (!trade) {
      const riskDist = Math.abs(entry - stop) || (entry * 0.015);
      const isLong = side.toLowerCase() === 'buy';
      let tp1 = null, tp2 = null, nextRes = null, nextSup = null;

      // Extract unique S/R levels from symbol's engine if ready
      if (eng && eng.getSupportResistance) {
        try {
          const sr = eng.getSupportResistance(entry);
          nextRes = sr.nextResistance;
          nextSup = sr.nextSupport;
          const atr = sr.atr || (entry * 0.01);
          const shy = atr * 0.15;
          if (isLong && nextRes && nextRes > entry + atr * 0.5) {
            tp1 = +(nextRes - shy).toFixed(6);
            tp2 = +(entry + riskDist * 3.0).toFixed(6);
          } else if (!isLong && nextSup && nextSup < entry - atr * 0.5) {
            tp1 = +(nextSup + shy).toFixed(6);
            tp2 = +(entry - riskDist * 3.0).toFixed(6);
          }
        } catch (e) {}
      }
      if (!tp1) {
        tp1 = isLong ? +(entry + riskDist * 1.5).toFixed(6) : +(entry - riskDist * 1.5).toFixed(6);
        tp2 = isLong ? +(entry + riskDist * 3.0).toFixed(6) : +(entry - riskDist * 3.0).toFixed(6);
      }

      trade = {
        symbol: sym,
        side,
        entryPrice: entry,
        stopLoss: stop,
        invalidation: stop,
        targets: [tp1, tp2, tp2 ? (isLong ? +(entry + riskDist * 4.0).toFixed(6) : +(entry - riskDist * 4.0).toFixed(6)) : tp2],
        riskDist,
        qty: size,
        originalQty: size,
        nextResistance: nextRes,
        nextSupport: nextSup,
        setupName: s && s.setupType ? s.setupType : 'BYBIT_LIVE',
        grade: 'A',
        isScalp: s ? !!s.isScalp : SCALP_MODE,
        horizon: s && s.horizon ? s.horizon : (SCALP_MODE ? '5m-10m' : '15m-1h'),
        openedAt: parseInt(pos.createdTime || now),
        tpFilled: [false, false, false],
        trailStage: 0,
        stopMovedToBreakEven: false
      };
      if (!currentAutoState.theses) currentAutoState.theses = {};
      currentAutoState.theses[key] = trade;
      positionManager.trades[key] = trade;
      await postJSON('/api/auto-trade/state', { theses: { [key]: trade } });
      log(`[POSITION GUARDIAN] Adopted and synced ${sym} ${side.toUpperCase()} thesis (TP1: ${tp1}, S/R: ${nextRes || nextSup || 'n/a'}) to server.`);
    } else {
      positionManager.trades[key] = trade;
    }

    let market = { price: mark, mark, confirmedCandle: null };
    if (eng) {
      try {
        const st = eng.getState();
        if (st) {
          market.atr = st.atr;
          market.confirmedCandle = st.lastConfirmedCandle;
          market.nextResistance = st.nextResistance;
          market.nextSupport = st.nextSupport;
          market.opposingSignal = st.decision && st.decision !== (side.toUpperCase() === 'BUY' ? 'BUY' : 'SELL') ? st : null;
          // Add RSI for momentum exit detection
          if (st.atr && eng.confirmed && eng.confirmed('mtf')) {
            try {
              const I = require('../agents/indicators.js');
              const mtfCandles = eng.confirmed('mtf');
              if (mtfCandles.length >= 15) {
                market.rsi = I.rsi(mtfCandles.map(c => c.close), 14);
              }
            } catch (e) {}
          }
        }
        if (eng.getSupportResistance && (!market.nextResistance || !market.nextSupport)) {
          const sr = eng.getSupportResistance(mark);
          if (!market.nextResistance) market.nextResistance = sr.nextResistance;
          if (!market.nextSupport) market.nextSupport = sr.nextSupport;
          if (!market.atr) market.atr = sr.atr;
        }
      } catch (e) {}
    }

    const action = positionManager.evaluate(trade, market);
    if (!action) continue;

    if (action.action === 'SCALE_OUT') {
      const fraction = action.fraction || 0.5;
      const sliceQty = +(trade.originalQty * fraction).toFixed(6);
      const tpLabel = isSrBank ? 'S/R Structural Wall Tested' : `TP${action.tpIndex + 1} reached`;
      log(`[POSITION GUARDIAN] ${sym} ${tpLabel}! Banking ${fraction * 100}% slice (${sliceQty})...`);
      positionManager.markTpFilled(trade, action.tpIndex, mark);

      if (currentAutoState.armed) {
        // 1. Partial close on Bybit
        await postJSON('/api/order/close', {
          category: 'linear',
          symbol: sym,
          side,
          qty: sliceQty
        });

        // 2. Immediately move Stop to Break-Even (entry price) upon TP1 or S/R bank
        if (action.tpIndex === 0 && !trade.stopMovedToBreakEven) {
          log(`[POSITION GUARDIAN] Profit coverage secured for ${sym} -> Moving Stop Loss to Entry Point (${trade.entryPrice})`);
          await postJSON('/api/position/stop', {
            category: 'linear',
            symbol: sym,
            stopLoss: trade.entryPrice
          });
          positionManager.markStopMoved(trade, trade.entryPrice, 'BREAK_EVEN');
        }
      }

      // Sync updated thesis to server
      await postJSON('/api/auto-trade/state', {
        theses: { [key]: trade }
      });
    } else if (action.action === 'MOVE_STOP') {
      log(`[POSITION GUARDIAN] Moving stop for ${sym} to ${action.newStop} (${action.reason})`);
      positionManager.markStopMoved(trade, action.newStop, action.reason);
      if (currentAutoState.armed) {
        await postJSON('/api/position/stop', {
          category: 'linear',
          symbol: sym,
          stopLoss: action.newStop
        });
      }
      await postJSON('/api/auto-trade/state', {
        theses: { [key]: trade }
      });
    } else if (action.action === 'CLOSE_ALL') {
      const isMomentumExit = action.reason === 'MOMENTUM_EXIT';
      log(`[POSITION GUARDIAN] ${isMomentumExit ? 'Momentum exit' : 'Closing entire'} ${sym} position: ${action.reason} — ${action.detail}`);
      if (currentAutoState.armed) {
        await postJSON('/api/order/close', {
          category: 'linear',
          symbol: sym,
          side,
          qty: size
        });
      }
    }
  }

  // ── Stale Limit Order Cleanup ──
  // Cancel unfilled limit orders older than ~45 min (or ~10 min for 5m scalps).
  // Prevents stale entries from filling at prices no longer valid.
  const now2 = Date.now();
  for (const [sym, ord] of Object.entries(activeOrders)) {
    const isScalpOrder = ord.isScalp || SCALP_MODE;
    const staleMs = isScalpOrder ? (10 * 60 * 1000) : (45 * 60 * 1000);
    if (now2 - ord.placedAt > staleMs) {
      log(`[STALE ORDER] Cancelling ${sym} ${isScalpOrder ? 'scalp ' : ''}limit order ${ord.orderId} (placed ${Math.round((now2 - ord.placedAt) / 60000)}min ago)`);
      if (currentAutoState.armed) {
        try {
          await postJSON('/api/order/cancel', {
            category: 'linear',
            symbol: sym,
            orderId: ord.orderId
          });
        } catch (e) { log(`[STALE ORDER] Cancel failed for ${sym}: ${e.message}`); }
      }
      delete activeOrders[sym];
    }
  }
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
  await syncAutoTradeState();
  await manageOpenPositions();
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

      const isGradeA = s.grade === 'A' || s.grade === 'A+';
      const isArmed = currentAutoState.armed;

      if (isArmed && isGradeA) {
        // Max concurrent orders / positions check
        const maxOrders = Number(process.env.MAX_CONCURRENT_POSITIONS || currentAutoState.maxConcurrentPositions || 5);
        const activeCount = openPositionsCache.length + Object.keys(activeOrders).filter(k => now - activeOrders[k].placedAt < 600000).length;
        if (activeCount >= maxOrders) {
          log(`[ORDER SKIPPED] Maximum concurrent order/position limit reached (${activeCount}/${maxOrders}).`);
          continue;
        }

        // 1 trade per coin rule:
        const hasOpenPos = openPositionsCache.some((p) => p.symbol === sym);
        const lastOrd = activeOrders[sym];
        const hasRecentOrder = lastOrd && (now - lastOrd.placedAt < 600000); // 10m

        if (hasOpenPos) {
          log(`[ORDER SKIPPED] ${sym} already has an open position on Bybit (1 trade per coin rule).`);
        } else if (hasRecentOrder) {
          log(`[ORDER SKIPPED] ${sym} already has an active order placed recently.`);
        } else {
          const qty = calculateOrderQty(sym, s.entry, currentAutoState);
          const side = s.decision === 'BUY' ? 'Buy' : 'Sell';
          log(`[24/7 CLOUD EXECUTION] Placing ${side.toUpperCase()} ${sym} qty=${qty} @ ${s.entry} (SL: ${s.stopLoss}, sizing: ${currentAutoState.sizingMode})...`);

          try {
            const ordRes = await postJSON('/api/order/place', {
              category: 'linear',
              symbol: sym,
              side,
              orderType: 'Limit',
              price: s.entry,
              qty,
              stopLoss: s.stopLoss,
              leverage: currentAutoState.leverage || 10,
              marginMode: currentAutoState.marginMode || 'cross'
            });

            if (ordRes && ordRes.data && ordRes.data.retCode === 0 && ordRes.data.result) {
              const orderId = ordRes.data.result.orderId;
              activeOrders[sym] = { orderId, placedAt: now, side, price: s.entry, qty, isScalp: !!s.isScalp };
              log(`[24/7 CLOUD SUCCESS] ${sym} limit order placed on Bybit! Order ID: ${orderId}`);

              // Persist thesis to auto_trade_state so Position Guardian displays it everywhere!
              const thesisKey = `${sym}-${side}`;
              const newThesis = {
                symbol: sym, side, entryPrice: s.entry,
                stopLoss: s.stopLoss, invalidation: s.invalidation || s.stopLoss,
                targets: s.takeProfit || [], riskDist: Math.abs(s.entry - s.stopLoss),
                qty, originalQty: qty, setupName: s.setupType, grade: s.grade,
                score: s.score, regime: s.regime, narrative: s.narrative || '',
                isScalp: !!s.isScalp,
                horizon: s.horizon || (s.isScalp ? '5m-10m' : '15m-1h'),
                nextResistance: s.nextResistance || null,
                nextSupport: s.nextSupport || null,
                openedAt: now
              };
              currentAutoState.theses = currentAutoState.theses || {};
              currentAutoState.theses[thesisKey] = newThesis;
              await postJSON('/api/auto-trade/state', {
                armed: true,
                theses: { [thesisKey]: newThesis }
              });

              // Record signal
              await post('/api/trades/record?_action=signal', {
                fingerprint: fp, symbol: sym,
                direction: s.direction || (s.decision === 'BUY' ? 'LONG' : 'SHORT'),
                setup_type: s.setupType, grade: s.grade, score: s.score,
                regime: s.regime, bias: s.bias,
                entry: s.entry, stop: s.stopLoss, targets: s.takeProfit, risk_reward: s.riskReward,
                outcome: 'ORDER_PLACED',
                reject_reason: '',
                flow: s.flow || null,
                evidence: (s.components || []).map((c) => `${c.label}: ${c.detail}`)
              });
            } else {
              log(`[24/7 ORDER REJECTED] ${sym}: ${ordRes && ordRes.data ? ordRes.data.retMsg : 'unknown'}`);
            }
          } catch (err) {
            log(`[24/7 ORDER ERROR] ${sym}: ${err.message}`);
          }
        }
      } else {
        log('SIGNAL', sym, s.decision, s.setupType, `grade ${s.grade}`,
          isArmed ? '(grade below A threshold)' : '(monitoring only — auto-trading STOPPED in dashboard)');
      }
    }
  }
}

// ── Boot ───────────────────────────────────────────────────────────────────
(async () => {
  log('starting:', SYMBOLS.length, 'symbols, decision every', DECISION_MS, 'ms');
  await seed();
  await syncAutoTradeState();
  connect();
  setInterval(() => { tick().catch((e) => log('tick error', e.message)); }, DECISION_MS);
  setInterval(() => {
    const mb = Math.round(process.memoryUsage().rss / 1048576);
    let ready = 0, graded = 0, statusCounts = {};
    for (const sym of SYMBOLS) {
      let st; try { st = engines[sym].getState(); } catch { continue; }
      if (!st) continue;
      statusCounts[st.dataStatus || '?'] = (statusCounts[st.dataStatus || '?'] || 0) + 1;
      if (st.dataStatus && st.dataStatus !== 'INVALID') ready++;
      if (st.grade) graded++;
    }
    log(`heartbeat rss=${mb}MB ready=${ready}/${SYMBOLS.length} withCandidate=${graded} armed=${currentAutoState.armed} data=${JSON.stringify(statusCounts)}`);
  }, Number(process.env.HEARTBEAT_MS || 300000));
})();
