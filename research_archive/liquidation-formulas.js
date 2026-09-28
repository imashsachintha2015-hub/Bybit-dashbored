#!/usr/bin/env node
/**
 * STUDY 16 — Testing the "Forced-Flow / Candle-Rejection / Trend-Fragility"
 * indicator family proposed against the liquidation + OHLCV dataset bundle.
 *
 * Five indicators were proposed:
 *   1. Forced-Flow Absorption Residual   (needs liquidation event feed)
 *   2. Candle Rejection w/ Volume        (OHLCV only)
 *   3. Liquidation Concentration Shock   (needs liquidation event feed)
 *   4. Cross-Asset Stress Breadth        (needs liquidation event feed, 3+ coins)
 *   5. Trend Efficiency & Fragility      (OHLCV only)
 *
 * BEFORE testing anything, the liquidation feed itself was audited: OKX's
 * public liquidation-orders endpoint (the only one reachable here) returns
 * only its most recent ~100 records per instrument, which in this fetch
 * span 3-7 hours of wall-clock time per coin. That is a hard ceiling of this
 * data source, not a truncation artifact — fetching again gets a different
 * recent 100, never a longer history. 100 events over a few hours is far
 * below this project's n>=300 floor for any HAC/FDR test, so indicators
 * 1/3/4 are reported descriptively only, clearly labeled as untested, rather
 * than dressed up with a t-statistic that would misrepresent an n this
 * small as if it meant something.
 *
 * Indicators 2 and 5 need only OHLCV, which this project already has at
 * 15-minute resolution over 418 days for BTC/ETH/SOL/DOGE — enough for a
 * real test at all four requested horizons (15m/30m/1h/1d = 1/2/4/96 bars).
 * Those get the full treatment: causal (expanding-window) decile thresholds,
 * HAC-corrected significance, split-half stability, cost-floor filtering,
 * and FDR across every test.
 */
const fs = require('fs');
const path = require('path');
const S = require('./lib-stats.js');

const DATA = path.join(__dirname, '..', 'backtest', 'data');
const RDATA = path.join(__dirname, 'data');
const COINS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'DOGEUSDT'];
const HORIZONS = [1, 2, 4, 96];       // 15m bars -> 15min, 30min, 1h, 1day
const HLABEL = { 1: '15min', 2: '30min', 4: '1h', 96: '1day' };
const COST_BPS = 4;

function mean(a) { return a.length ? a.reduce((x, y) => x + y, 0) / a.length : 0; }
function median(a) { const s = a.slice().sort((x, y) => x - y); return s.length ? s[Math.floor(s.length / 2)] : 0; }
function tanh(x) { return Math.tanh(x); }

// ─── Part A: OHLCV-only indicators (2 and 5) ───
function buildOhlcvSignals(bars) {
  const n = bars.length;
  const closes = bars.map(c => c.close);
  const rets = new Array(n).fill(0);
  for (let i = 1; i < n; i++) rets[i] = Math.log(closes[i] / closes[i - 1]);

  const B = new Array(n).fill(null), Wm = new Array(n).fill(null), Wp = new Array(n).fill(null);
  for (let i = 0; i < n; i++) {
    const c = bars[i];
    const range = c.high - c.low;
    if (range <= 0) continue;
    B[i] = (c.close - c.open) / range;
    Wm[i] = (Math.min(c.open, c.close) - c.low) / range;
    Wp[i] = (c.high - Math.max(c.open, c.close)) / range;
  }
  const D = B.map((b, i) => (b != null && Wm[i] != null && Wp[i] != null) ? b + Wm[i] - Wp[i] : null);

  // Indicator 2: candle rejection with volume confirmation
  const R = new Array(n).fill(null);
  const VOL_WIN = 40;
  for (let i = VOL_WIN; i < n; i++) {
    if (D[i] == null) continue;
    const trailingVol = bars.slice(i - VOL_WIN, i).map(c => c.volume);
    const medVol = median(trailingVol);
    const U = bars[i].volume / (medVol + 1e-9);
    R[i] = tanh(D[i] * Math.log(1 + Math.max(0, U)));
  }

  // Indicator 5: trend efficiency + fragility, at three lookback windows
  const effWindows = [16, 32, 64];
  const E = {}, G = {};
  for (const W of effWindows) {
    E[W] = new Array(n).fill(null);
    G[W] = new Array(n).fill(null);
    const k = 3;
    for (let i = W; i < n; i++) {
      const pathLen = rets.slice(i - W + 1, i + 1).reduce((a, b) => a + Math.abs(b), 0);
      if (pathLen <= 0) continue;
      const net = Math.log(closes[i] / closes[i - W]);
      E[W][i] = net / pathLen;
      if (i >= k - 1) {
        const dWin = D.slice(i - k + 1, i + 1).filter(v => v != null);
        if (dWin.length === k) {
          const meanD = mean(dWin);
          G[W][i] = Math.abs(E[W][i]) * Math.max(0, -Math.sign(E[W][i]) * meanD);
        }
      }
    }
  }
  return { R, E, G };
}

// Refreshes the sort every REFRESH bars instead of every single bar --
// re-sorting the full "seen so far" array at every bar is O(n^2 log n) over
// a 40,000-bar series, which is what made the first run of this never finish.
function causalDecileMask(scores, frac, minHistory) {
  const n = scores.length;
  const top = new Array(n).fill(false), bot = new Array(n).fill(false);
  const REFRESH = 200;
  const seen = [];
  let hi = null, lo = null, sinceRefresh = 0;
  for (let i = 0; i < n; i++) {
    if (scores[i] != null && seen.length >= minHistory) {
      if (hi == null || sinceRefresh >= REFRESH) {
        const sorted = seen.slice().sort((a, b) => a - b);
        hi = sorted[Math.floor(sorted.length * (1 - frac))];
        lo = sorted[Math.floor(sorted.length * frac)];
        sinceRefresh = 0;
      }
      if (scores[i] >= hi) top[i] = true;
      if (scores[i] <= lo) bot[i] = true;
    }
    if (scores[i] != null) { seen.push(scores[i]); sinceRefresh++; }
  }
  return { top, bot };
}

function testSignal(bars, scores, horizon, label, coin) {
  const n = bars.length;
  const closes = bars.map(c => c.close);
  const fwd = new Array(n).fill(null);
  for (let i = 0; i < n - horizon; i++) {
    if (scores[i] == null) continue;
    fwd[i] = Math.log(closes[i + horizon] / closes[i]) * 10000; // bps
  }
  const { top, bot } = causalDecileMask(scores, 0.10, 200);
  const results = [];
  for (const [tag, mask] of [['top', top], ['bot', bot]]) {
    const idx = [];
    for (let i = 0; i < n; i++) if (mask[i] && fwd[i] != null) idx.push(i);
    if (idx.length < 60) continue;
    const vals = idx.map(i => fwd[i]);
    const t = S.hacT(vals, Math.max(1, horizon - 1));
    if (!t) continue;
    const mid = Math.floor(idx.length / 2);
    const firstH = S.mean(vals.slice(0, mid)), secondH = S.mean(vals.slice(mid));
    results.push({ coin, indicator: label, horizon, tail: tag, n: idx.length, meanBps: t.mean, t: t.t, p: t.p, firstH, secondH,
      verdict: S.verdict(t, firstH, secondH, COST_BPS) });
  }
  return results;
}

function main() {
  const line = '─'.repeat(110);
  console.log(`\n${line}\n  STUDY 16 — FORCED-FLOW / CANDLE-REJECTION / TREND-FRAGILITY INDICATOR FAMILY\n${line}`);

  // ── Audit the liquidation feed before promising anything from it ──
  console.log(`\n  LIQUIDATION FEED AUDIT (indicators 1, 3, 4 depend on this)\n`);
  const liqCoverage = {};
  for (const ccy of ['BTC', 'ETH', 'SOL']) {
    const f = path.join(RDATA, `${ccy}-liquidations.json`);
    if (!fs.existsSync(f)) { console.log(`  ${ccy}: no liquidation file`); continue; }
    const d = JSON.parse(fs.readFileSync(f, 'utf8'));
    const rows = d.rows || [];
    const ts = rows.map(r => r.ts);
    const spanMin = rows.length ? (Math.max(...ts) - Math.min(...ts)) / 60000 : 0;
    liqCoverage[ccy] = { rows, spanMin };
    console.log(`  ${ccy}: ${rows.length} events, spanning ${spanMin.toFixed(0)} minutes (${(spanMin/60).toFixed(1)}h) — ` +
      `${rows.length < 300 ? 'BELOW the n>=300 floor this project requires for HAC/FDR; descriptive only, no significance test run' : 'enough to test'}`);
  }
  console.log(`
  This is OKX's public liquidation-orders endpoint returning only its most
  recent ~100 records per instrument — re-fetching gets a different recent
  100, not a longer history. Indicators 1 (Forced-Flow Absorption Residual),
  3 (Liquidation Concentration Shock) and 4 (Cross-Asset Stress Breadth) are
  therefore shown below as descriptive snapshots of the one ~3-7h window
  captured, not as tested hypotheses — reporting a t-statistic or win rate
  from n=100/n=29-per-tail events spanning a few hours would repeat exactly
  the small-sample mistake this project's own Part II caught and fixed.`);

  for (const ccy of Object.keys(liqCoverage)) {
    const { rows, spanMin } = liqCoverage[ccy];
    if (!rows.length) continue;
    const longLiq = rows.filter(r => r.posSide === 'long');
    const shortLiq = rows.filter(r => r.posSide === 'short');
    const qL = longLiq.reduce((a, r) => a + r.sz, 0), qS = shortLiq.reduce((a, r) => a + r.sz, 0);
    const F = (qS - qL) / (qS + qL + 1e-9);
    // concentration (indicator 3): Herfindahl of event sizes
    const total = rows.reduce((a, r) => a + r.sz, 0) || 1;
    const K = rows.reduce((a, r) => a + (r.sz / total) ** 2, 0);
    console.log(`\n  ${ccy}: ${longLiq.length} long-liq events (${qL.toFixed(2)} units), ${shortLiq.length} short-liq events (${qS.toFixed(2)} units)`);
    console.log(`         signed forced-flow F=${F.toFixed(3)} (${F < 0 ? 'net forced SELLING' : 'net forced BUYING'} in this window)`);
    console.log(`         concentration K=${K.toFixed(3)} (1/n=${(1/rows.length).toFixed(3)} would mean perfectly even sizes; higher = a few large prints dominate)`);
  }
  console.log(`\n  Cross-asset breadth (indicator 4) needs simultaneous windows across coins; the three coins'`);
  console.log(`  capture windows above only partially overlap in time, so a same-instant breadth reading isn't`);
  console.log(`  available from this fetch either — noted as a data-availability gap, not computed.`);

  // ── Part 2: OHLCV indicators, properly tested ──
  console.log(`\n${line}\n  INDICATORS 2 & 5 — OHLCV-ONLY, TESTED AT ALL 4 REQUESTED HORIZONS\n${line}`);
  const allTests = [];
  for (const coin of COINS) {
    const bars = JSON.parse(fs.readFileSync(path.join(DATA, `${coin}.json`), 'utf8')).series['15'];
    const { R, E, G } = buildOhlcvSignals(bars);
    for (const h of HORIZONS) {
      allTests.push(...testSignal(bars, R, h, 'R_CandleRejection', coin));
      for (const W of [16, 32, 64]) {
        allTests.push(...testSignal(bars, E[W], h, `E${W}_TrendEfficiency`, coin));
        allTests.push(...testSignal(bars, G[W], h, `G${W}_Fragility`, coin));
      }
    }
    process.stdout.write(`  ${coin} done\n`);
  }

  const { survivors, tested, threshold } = S.fdr(allTests, 0.10);
  console.log(`\n  ${tested} tests run (coin x indicator x horizon x tail). FDR q=0.10 threshold: ` +
    `${threshold != null ? threshold.toExponential(2) : 'none passed'}`);
  console.log(`  ${survivors.length} survive FDR.\n`);

  if (survivors.length) {
    console.log(`  ${'coin'.padEnd(10)}${'indicator'.padEnd(20)}${'horizon'.padEnd(9)}${'tail'.padEnd(6)}${'n'.padStart(6)}${'meanBps'.padStart(10)}${'t(HAC)'.padStart(9)}${'verdict'.padStart(20)}`);
    for (const s of survivors) {
      console.log(`  ${s.coin.padEnd(10)}${s.indicator.padEnd(20)}${HLABEL[s.horizon].padEnd(9)}${s.tail.padEnd(6)}${String(s.n).padStart(6)}${s.meanBps.toFixed(2).padStart(10)}${s.t.toFixed(2).padStart(9)}${s.verdict.padStart(20)}`);
    }
    const solid = survivors.filter(s => s.verdict === 'SURVIVES');
    console.log(`\n  Of those, ${solid.length} also clear the ${COST_BPS}bps cost floor with a stable split-half sign.`);
  } else {
    console.log(`  Same pattern as every prior part: zero of ${tested} candle/trend-based signals survive FDR at any of the four requested horizons.`);
  }

  fs.mkdirSync(path.join(__dirname, 'results'), { recursive: true });
  fs.writeFileSync(path.join(__dirname, 'results', 'liquidation-formulas.json'),
    JSON.stringify({ generatedAt: new Date().toISOString(), liqCoverage: Object.fromEntries(Object.entries(liqCoverage).map(([k,v]) => [k, { n: v.rows.length, spanMin: v.spanMin }])), tested, survivorsCount: survivors.length, allTests }, null, 2));
  console.log(`\n${line}\n`);
}

main();
