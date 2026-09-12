#!/usr/bin/env node
/**
 * STUDY 19 — Does the ensemble (Part XI) + persistence filter (Part XII)
 * generalize across the whole market, or was it a BTC/ETH-specific fluke?
 *
 * Every prior part of this project that survived every gate did so on only
 * 2 of the 4-6 coins tested (BTC, ETH). This runs the exact same,
 * already-frozen formula -- no new tuning, no new formulas invented for
 * this run -- against as broad a coin set as this session could fetch
 * (~30 liquid USDT perpetuals spanning majors, L1s, L2s, and memecoins),
 * at the same h=2 (30min) horizon, same symmetric 0.5%/0.5% barrier.
 *
 * This is the cleanest generalization test this project can run: the
 * formula was fixed BEFORE this script ran (copied verbatim from Part XI/
 * XII), so any result here is out-of-sample by construction, not fitted to
 * whichever coins happen to be in this batch.
 *
 * Reports three things:
 *   1. Per-coin win rate, split-half stability, and whether it survives
 *      FDR across the whole coin set (not just BTC/ETH's own two tests).
 *   2. How many of the coins individually clear a meaningful bar (WR>52%),
 *      as a plain count -- the generalization headline.
 *   3. A pooled test: every coin's top-decile trades concatenated (with
 *      block-bootstrap blocks that never cross a coin boundary, so each
 *      coin's own autocorrelation stays respected) into one sample, to ask
 *      "is there a market-wide edge" rather than "did 2 of 30 coins get
 *      lucky."
 */
const fs = require('fs');
const path = require('path');
const S = require('./lib-stats.js');

const DATA = path.join(__dirname, '..', 'backtest', 'data');
const HORIZON = 2;
const COST_BPS = 4;

function mean(a) { return a.length ? a.reduce((x, y) => x + y, 0) / a.length : 0; }
function sd(a) {
  if (a.length < 2) return 0;
  const m = mean(a);
  return Math.sqrt(a.reduce((x, y) => x + (y - m) ** 2, 0) / (a.length - 1));
}
function median(a) { const s = a.slice().sort((x, y) => x - y); return s[Math.floor(s.length / 2)]; }
function tanh(x) { return Math.tanh(x); }

function buildBaseSeries(bars) {
  const n = bars.length;
  const closes = bars.map(c => c.close);
  const rets = new Array(n).fill(0);
  for (let i = 1; i < n; i++) rets[i] = Math.log(closes[i] / closes[i - 1]);
  const logVol = bars.map(c => Math.log(Math.max(c.volume, 1e-9)));

  const out = { m4: [], m16: [], m32: [], m64: [], pos: [], vz: [], vr: [], eff: [], dev: [], accel: [], curv: [], body: [], wick: [] };
  for (let i = 0; i < n; i++) {
    const mAt = lag => (i >= lag ? Math.log(closes[i] / closes[i - lag]) : null);
    out.m4.push(mAt(4)); out.m16.push(mAt(16)); out.m32.push(mAt(32)); out.m64.push(mAt(64));

    if (i >= 64) {
      const win = closes.slice(i - 63, i + 1);
      const hi = Math.max(...win), lo = Math.min(...win);
      out.pos.push(hi > lo ? ((closes[i] - lo) / (hi - lo) - 0.5) * 2 : 0);
      const m = mean(win), s = sd(win);
      out.dev.push(s ? (closes[i] - m) / s : 0);
    } else { out.pos.push(null); out.dev.push(null); }

    if (i >= 40) {
      const vwin = logVol.slice(i - 39, i + 1);
      const vm = median(vwin), vs = median(vwin.map(v => Math.abs(v - vm))) * 1.4826;
      out.vz.push(vs ? (logVol[i] - vm) / vs : 0);
    } else out.vz.push(null);

    if (i >= 64) {
      const r16 = sd(rets.slice(i - 15, i + 1));
      const r64 = sd(rets.slice(i - 63, i + 1));
      out.vr.push(r64 ? r16 / r64 : null);
    } else out.vr.push(null);

    if (i >= 16) {
      const w = rets.slice(i - 15, i + 1);
      const net = Math.abs(w.reduce((a, b) => a + b, 0));
      const pathLen = w.reduce((a, b) => a + Math.abs(b), 0);
      out.eff.push(pathLen ? net / pathLen : 0);
    } else out.eff.push(null);

    if (i >= 8) out.accel.push(out.m4[i] != null && out.m4[i - 4] != null ? out.m4[i] - out.m4[i - 4] : null);
    else out.accel.push(null);
    if (i >= 12 && out.accel[i] != null && out.accel[i - 4] != null) out.curv.push(out.accel[i] - out.accel[i - 4]);
    else out.curv.push(null);

    const c = bars[i];
    const range = c.high - c.low;
    out.body.push(range > 0 ? (c.close - c.open) / range : 0);
    const upper = c.high - Math.max(c.open, c.close), lower = Math.min(c.open, c.close) - c.low;
    out.wick.push(range > 0 ? (upper - lower) / range : 0);
  }
  return out;
}

const FORMULAS = {
  F1_NonlinearPressure: b => tanh((2 * b.m4 + 1.2 * b.m16 + 0.7 * b.m32) * (1 + 0.8 * b.vz) / (b.vr + 0.25)),
  F3_TrendEfficiency: b => tanh((b.m16 + 0.7 * b.m32) * (0.4 + 1.8 * b.eff) * (1 + 0.5 * b.vz)),
  F6_Interaction: b => tanh((b.m4 * (1 + 1.5 * b.pos) + b.m16 * (1 - b.pos) + 0.5 * b.m32) * (1 + b.vz) / (0.5 + b.vr)),
  F7_Asymmetry: b => tanh((b.m4 + 0.5 * b.m16) * (1 + 0.8 * b.vz) + 0.8 * b.body - 0.4 * b.wick),
  F8_MultiScale: b => tanh((b.m4 + 0.8 * b.m16 + 0.5 * b.m32 + 0.25 * b.m64) * (1 + 0.6 * b.eff) * (1 + 0.5 * Math.abs(b.vz)))
};
const REQS = {
  F1_NonlinearPressure: ['m4', 'm16', 'm32', 'vz', 'vr'], F3_TrendEfficiency: ['m16', 'm32', 'eff', 'vz'],
  F6_Interaction: ['m4', 'pos', 'm16', 'm32', 'vz', 'vr'], F7_Asymmetry: ['m4', 'm16', 'vz', 'body', 'wick'],
  F8_MultiScale: ['m4', 'm16', 'm32', 'm64', 'eff', 'vz']
};

function barrierOutcome(bars, i, horizon, barrier = 0.005) {
  const entry = bars[i].close;
  const up = entry * (1 + barrier), dn = entry * (1 - barrier);
  for (let k = i + 1; k <= Math.min(i + horizon, bars.length - 1); k++) {
    const hitUp = bars[k].high >= up, hitDn = bars[k].low <= dn;
    if (hitUp && hitDn) return null;
    if (hitUp) return 1;
    if (hitDn) return 0;
  }
  return null;
}

function blockBootstrapP(outcomes, blockLen, iters = 300) {
  const n = outcomes.length;
  if (n < 30) return null;
  const observed = mean(outcomes) - 0.5;
  let seed = 424242;
  const rand = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; };
  const nBlocks = Math.ceil(n / blockLen);
  let extreme = 0;
  for (let it = 0; it < iters; it++) {
    const resample = [];
    for (let b = 0; b < nBlocks; b++) {
      const start = Math.floor(rand() * (n - blockLen));
      for (let k = 0; k < blockLen && resample.length < n; k++) resample.push(outcomes[start + k]);
    }
    const stat = mean(resample) - mean(outcomes);
    if (Math.abs(stat) >= Math.abs(observed)) extreme++;
  }
  return (extreme + 1) / (iters + 1);
}

function causalTopMask(scores, frac, minHistory) {
  const n = scores.length;
  const mask = new Array(n).fill(false);
  const REFRESH = 200;
  const seen = [];
  let hi = null, sinceRefresh = 0;
  for (let i = 0; i < n; i++) {
    if (scores[i] != null && seen.length >= minHistory) {
      if (hi == null || sinceRefresh >= REFRESH) {
        const sorted = seen.slice().sort((a, b) => a - b);
        hi = sorted[Math.floor(sorted.length * (1 - frac))];
        sinceRefresh = 0;
      }
      if (scores[i] >= hi) mask[i] = true;
    }
    if (scores[i] != null) { seen.push(scores[i]); sinceRefresh++; }
  }
  return mask;
}

function buildEnsemble(bars) {
  const b = buildBaseSeries(bars);
  const n = bars.length;
  const scores = {};
  for (const [name, fn] of Object.entries(FORMULAS)) {
    const reqs = REQS[name];
    const s = new Array(n).fill(null);
    for (let i = 0; i < n; i++) {
      if (!reqs.every(r => b[r][i] != null)) continue;
      const row = {}; for (const r of reqs) row[r] = b[r][i];
      const v = fn(row);
      if (isFinite(v)) s[i] = v;
    }
    scores[name] = s;
  }
  const ensemble = new Array(n).fill(null);
  for (let i = 0; i < n; i++) {
    const vals = Object.values(scores).map(s => s[i]).filter(v => v != null);
    if (vals.length === 5) ensemble[i] = mean(vals);
  }
  return ensemble;
}

function evalCoin(bars, ensemble, outcomes, mask) {
  const n = bars.length;
  const idx = [];
  for (let i = 0; i < n; i++) if (mask[i] && outcomes[i] != null) idx.push(i);
  if (idx.length < 30) return null;
  const vals = idx.map(i => outcomes[i]);
  const wr = mean(vals);
  const blockLen = Math.max(8, HORIZON * 3);
  const p = blockBootstrapP(vals, blockLen);
  const mid = Math.floor(idx.length / 2);
  const first = mean(vals.slice(0, mid)), second = mean(vals.slice(mid));
  const costR = COST_BPS / 50;
  const expR = (2 * wr - 1) - costR;
  const breakevenBps = (2 * wr - 1) * 50;
  return { n: idx.length, wr, p, first, second, expR, breakevenBps, outcomes: vals };
}

function main() {
  const line = '─'.repeat(112);
  console.log(`\n${line}\n  STUDY 19 — DOES THE ENSEMBLE + PERSISTENCE FORMULA GENERALIZE ACROSS THE WHOLE MARKET?\n${line}`);

  const files = fs.readdirSync(DATA).filter(f => f.endsWith('USDT.json'));
  console.log(`\n  Coins with cached 15m data: ${files.length}\n`);

  const baselineResults = [], persistResults = [];

  for (const file of files) {
    const coin = file.replace('.json', '');
    let bundle;
    try { bundle = JSON.parse(fs.readFileSync(path.join(DATA, file), 'utf8')); } catch (e) { continue; }
    const bars = bundle.series && bundle.series['15'];
    if (!bars || bars.length < 500) { console.log(`  ${coin}: skipped (${bars ? bars.length : 0} bars, too little history)`); continue; }

    const ensemble = buildEnsemble(bars);
    const n = bars.length;
    const outcomes = new Array(n).fill(null);
    for (let i = 0; i < n - HORIZON; i++) outcomes[i] = barrierOutcome(bars, i, HORIZON);

    const baseMask = causalTopMask(ensemble, 0.10, 200);
    const base = evalCoin(bars, ensemble, outcomes, baseMask);
    if (base) baselineResults.push({ coin, days: ((bars[bars.length-1].start - bars[0].start) / 86400000).toFixed(0), ...base });

    const sustainedMask = baseMask.map((m, i) => m && i > 0 && baseMask[i - 1]);
    const persist = evalCoin(bars, ensemble, outcomes, sustainedMask);
    if (persist) persistResults.push({ coin, ...persist });
  }

  console.log(`  ${'coin'.padEnd(10)}${'days'.padStart(6)}${'n'.padStart(7)}${'WR'.padStart(8)}${'p(boot)'.padStart(10)}${'1st/2nd'.padStart(14)}${'breakeven'.padStart(11)}`);
  for (const r of baselineResults.sort((a,b) => b.wr - a.wr)) {
    const stable = Math.sign(r.first - 0.5) === Math.sign(r.second - 0.5) ? '' : '  <- FLIPS';
    console.log(`  ${r.coin.padEnd(10)}${String(r.days).padStart(6)}${String(r.n).padStart(7)}${(r.wr*100).toFixed(1).padStart(7)}%${(r.p!=null?r.p.toFixed(3):'—').padStart(10)}${((r.first*100).toFixed(0)+'%/'+(r.second*100).toFixed(0)+'%').padStart(14)}${r.breakevenBps.toFixed(1).padStart(10)}bps${stable}`);
  }

  const clearing52 = baselineResults.filter(r => r.wr > 0.52);
  const stable52 = clearing52.filter(r => Math.sign(r.first - 0.5) === Math.sign(r.second - 0.5));
  console.log(`\n  ${clearing52.length} of ${baselineResults.length} coins clear WR>52%; ${stable52.length} of those are also split-half stable.`);

  const { survivors, tested, threshold } = S.fdr(baselineResults.filter(r=>r.p!=null).map(r => ({...r, p: r.p})), 0.10);
  console.log(`  FDR across all ${tested} coins (q=0.10): ${survivors.length} survive (threshold ${threshold!=null?threshold.toExponential(2):'none'}).`);
  if (survivors.length) console.log(`    Survivors: ${survivors.map(s=>s.coin).join(', ')}`);

  // ── Pooled test: concatenate every coin's top-decile trades, block-bootstrap respecting coin boundaries ──
  console.log(`\n${line}\n  POOLED MARKET-WIDE TEST (every coin's top-decile trades combined)\n${line}`);
  const pooledOutcomes = [];
  const coinBlocks = []; // [start, end) index ranges per coin, so bootstrap blocks never cross a coin boundary
  for (const r of baselineResults) {
    const start = pooledOutcomes.length;
    pooledOutcomes.push(...r.outcomes);
    coinBlocks.push([start, pooledOutcomes.length]);
  }
  const pooledWR = mean(pooledOutcomes);
  // Block bootstrap: resample WITHIN each coin's own range only.
  let seed = 13371337;
  const rand = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; };
  const blockLen = HORIZON * 3;
  let extreme = 0;
  const iters = 500;
  const observed = pooledWR - 0.5;
  for (let it = 0; it < iters; it++) {
    const resample = [];
    while (resample.length < pooledOutcomes.length) {
      const [s, e] = coinBlocks[Math.floor(rand() * coinBlocks.length)];
      const len = e - s;
      if (len <= blockLen) { resample.push(...pooledOutcomes.slice(s, e)); continue; }
      const start = s + Math.floor(rand() * (len - blockLen));
      resample.push(...pooledOutcomes.slice(start, start + blockLen));
    }
    const stat = mean(resample.slice(0, pooledOutcomes.length)) - pooledWR;
    if (Math.abs(stat) >= Math.abs(observed)) extreme++;
  }
  const pooledP = (extreme + 1) / (iters + 1);
  const pooledBreakeven = (2 * pooledWR - 1) * 50;
  console.log(`\n  Pooled across ${baselineResults.length} coins: n=${pooledOutcomes.length} trades, WR=${(pooledWR*100).toFixed(2)}%, p(boot)=${pooledP.toFixed(4)}, breakeven cost=${pooledBreakeven.toFixed(1)}bps`);
  console.log(`  (this is the single cleanest answer to "does it generalize": one win rate across the whole fetched market, not per-coin cherry-picking)`);

  console.log(`\n${line}\n  WITH PERSISTENCE FILTER (>=2 consecutive bars in own top decile)\n${line}`);
  console.log(`  ${'coin'.padEnd(10)}${'n'.padStart(7)}${'WR'.padStart(8)}${'p(boot)'.padStart(10)}${'1st/2nd'.padStart(14)}`);
  for (const r of persistResults.sort((a,b) => b.wr - a.wr)) {
    const stable = Math.sign(r.first - 0.5) === Math.sign(r.second - 0.5) ? '' : '  <- FLIPS';
    console.log(`  ${r.coin.padEnd(10)}${String(r.n).padStart(7)}${(r.wr*100).toFixed(1).padStart(7)}%${(r.p!=null?r.p.toFixed(3):'—').padStart(10)}${((r.first*100).toFixed(0)+'%/'+(r.second*100).toFixed(0)+'%').padStart(14)}${stable}`);
  }

  fs.mkdirSync(path.join(__dirname, 'results'), { recursive: true });
  fs.writeFileSync(path.join(__dirname, 'results', 'market-wide-test.json'),
    JSON.stringify({ generatedAt: new Date().toISOString(), costBps: COST_BPS, horizon: HORIZON,
      baselineResults: baselineResults.map(({outcomes, ...r}) => r), persistResults: persistResults.map(({outcomes, ...r}) => r),
      pooledWR, pooledP, pooledN: pooledOutcomes.length, pooledBreakeven }, null, 2));
  console.log(`\n${line}\n`);
}

main();
