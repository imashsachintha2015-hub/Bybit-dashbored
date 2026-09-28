#!/usr/bin/env node
/**
 * STUDY 18 — New features/gates on top of the Part XI ensemble: do any of
 * them add real information, or just re-slice the same signal?
 *
 * The Part XI ensemble (mean of F1/F3/F6/F7/F8) is the best-performing,
 * properly-verified signal this project has found: 58.9-59.1% WR on BTC,
 * 56.1-56.3% on ETH, at h=2 (30min). This asks: can genuinely NEW
 * information -- not reused from the same 8 base variables -- push it
 * further? Four candidates, each with an actual mechanism, not a tuning
 * knob:
 *
 *   1. VOLUME CONFIRMATION. Elevated participation (vz, already in the base
 *      series but never used as a standalone GATE rather than a formula
 *      ingredient) alongside the ensemble signal -- does a move confirmed
 *      by volume win more than one that isn't?
 *   2. CROSS-ASSET AGREEMENT. BTC and ETH's ensembles are computed
 *      independently. Does BTC's signal win more often when ETH's ensemble
 *      *at the same timestamp* also agrees? This is real new information --
 *      it cannot be derived from BTC's own price series.
 *   3. SIGNAL PERSISTENCE. Has the ensemble been in its own top decile for
 *      at least 2 consecutive bars, rather than just crossing in on this
 *      bar? A brand-new feature (first-crossing vs sustained), not a
 *      re-threshold of an existing one.
 *   4. SESSION TIMING. Asia (00-08 UTC) / Europe (08-16 UTC) / US (16-24
 *      UTC) -- does the signal work better in one trading session?
 *
 * Same discipline throughout: causal (walk-forward, refreshed-every-200-bar)
 * decile thresholds, moving-block bootstrap significance, split-half
 * stability, FDR across every test run here, cost-adjusted breakeven.
 */
const fs = require('fs');
const path = require('path');
const S = require('./lib-stats.js');

const DATA = path.join(__dirname, '..', 'backtest', 'data');
const COINS = ['BTCUSDT', 'ETHUSDT'];
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

function evalMask(bars, outcomes, mask, label, extra) {
  const n = bars.length;
  const idx = [];
  for (let i = 0; i < n; i++) if (mask[i] && outcomes[i] != null) idx.push(i);
  if (idx.length < 30) return null;
  const vals = idx.map(i => outcomes[i]);
  const wr = mean(vals);
  const blockLen = Math.max(8, HORIZON * 3);
  const p = idx.length >= 30 ? blockBootstrapP(vals, blockLen) : null;
  const mid = Math.floor(idx.length / 2);
  const first = mean(vals.slice(0, mid)), second = mean(vals.slice(mid));
  const costR = COST_BPS / 50;
  const expR = (2 * wr - 1) - costR;
  const breakevenBps = (2 * wr - 1) * 50;
  return { label, n: idx.length, wr, p, first, second, expR, breakevenBps, ...extra };
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
  return { b, ensemble };
}

function main() {
  const line = '─'.repeat(112);
  console.log(`\n${line}\n  STUDY 18 — NEW FEATURES/GATES ON TOP OF THE PART XI ENSEMBLE\n${line}`);

  const perCoin = {};
  for (const coin of COINS) {
    const bars = JSON.parse(fs.readFileSync(path.join(DATA, `${coin}.json`), 'utf8')).series['15'];
    const { b, ensemble } = buildEnsemble(bars);
    const n = bars.length;
    const outcomes = new Array(n).fill(null);
    for (let i = 0; i < n - HORIZON; i++) outcomes[i] = barrierOutcome(bars, i, HORIZON);
    perCoin[coin] = { bars, b, ensemble, outcomes };
  }

  const allTests = [];

  for (const coin of COINS) {
    const { bars, b, ensemble, outcomes } = perCoin[coin];
    const n = bars.length;
    const baseMask = causalTopMask(ensemble, 0.10, 200);

    const baseline = evalMask(bars, outcomes, baseMask, 'Ensemble top10% (baseline)', { coin });
    if (baseline) allTests.push(baseline);

    // ── 1. Volume confirmation: elevated participation (vz > 0) alongside the signal ──
    const volHigh = baseMask.map((m, i) => m && b.vz[i] != null && b.vz[i] > 0);
    const volLow = baseMask.map((m, i) => m && b.vz[i] != null && b.vz[i] <= 0);
    const t1 = evalMask(bars, outcomes, volHigh, 'AND volume-confirmed (vz>0)', { coin });
    const t2 = evalMask(bars, outcomes, volLow, 'AND volume NOT confirmed (vz<=0)', { coin });
    if (t1) allTests.push(t1); if (t2) allTests.push(t2);

    // ── 3. Persistence: signal held for >=2 consecutive bars vs first-crossing only ──
    const sustained = baseMask.map((m, i) => m && i > 0 && baseMask[i - 1]);
    const firstCross = baseMask.map((m, i) => m && !(i > 0 && baseMask[i - 1]));
    const t3 = evalMask(bars, outcomes, sustained, 'AND sustained (>=2 consecutive bars)', { coin });
    const t4 = evalMask(bars, outcomes, firstCross, 'AND first-crossing bar only', { coin });
    if (t3) allTests.push(t3); if (t4) allTests.push(t4);

    // ── 4. Session timing (UTC hour of the bar's start timestamp) ──
    const hourOf = i => new Date(bars[i].start).getUTCHours();
    const asia = baseMask.map((m, i) => m && hourOf(i) >= 0 && hourOf(i) < 8);
    const europe = baseMask.map((m, i) => m && hourOf(i) >= 8 && hourOf(i) < 16);
    const us = baseMask.map((m, i) => m && hourOf(i) >= 16 && hourOf(i) < 24);
    const t5 = evalMask(bars, outcomes, asia, 'AND Asia session (00-08 UTC)', { coin });
    const t6 = evalMask(bars, outcomes, europe, 'AND Europe session (08-16 UTC)', { coin });
    const t7 = evalMask(bars, outcomes, us, 'AND US session (16-24 UTC)', { coin });
    if (t5) allTests.push(t5); if (t6) allTests.push(t6); if (t7) allTests.push(t7);

    process.stdout.write(`  ${coin} done\n`);
  }

  // ── 2. Cross-asset agreement (needs both coins' ensembles aligned by timestamp) ──
  const btc = perCoin['BTCUSDT'], eth = perCoin['ETHUSDT'];
  const ethByTs = new Map();
  eth.bars.forEach((c, i) => ethByTs.set(c.start, i));
  const btcMask = causalTopMask(btc.ensemble, 0.10, 200);
  const agreeMask = new Array(btc.bars.length).fill(false);
  const disagreeMask = new Array(btc.bars.length).fill(false);
  for (let i = 0; i < btc.bars.length; i++) {
    if (!btcMask[i]) continue;
    const j = ethByTs.get(btc.bars[i].start);
    if (j == null || eth.ensemble[j] == null) continue;
    // "Agrees" = ETH's ensemble score is ALSO in the upper half of its own recent distribution.
    if (eth.ensemble[j] > 0) agreeMask[i] = true; else disagreeMask[i] = true;
  }
  const tAgree = evalMask(btc.bars, btc.outcomes, agreeMask, 'BTC ensemble top10% AND ETH ensemble agrees (>0)', { coin: 'BTCUSDT' });
  const tDisagree = evalMask(btc.bars, btc.outcomes, disagreeMask, 'BTC ensemble top10% AND ETH ensemble disagrees (<=0)', { coin: 'BTCUSDT' });
  if (tAgree) allTests.push(tAgree); if (tDisagree) allTests.push(tDisagree);

  console.log(`\n  ${'coin'.padEnd(10)}${'test'.padEnd(46)}${'n'.padStart(6)}${'WR'.padStart(8)}${'p(boot)'.padStart(10)}${'expR'.padStart(9)}${'breakeven'.padStart(11)}${'1st/2nd'.padStart(14)}`);
  for (const t of allTests) {
    const stable = Math.sign(t.first - 0.5) === Math.sign(t.second - 0.5) ? '' : '  <- FLIPS';
    console.log(`  ${t.coin.padEnd(10)}${t.label.padEnd(46)}${String(t.n).padStart(6)}${(t.wr*100).toFixed(1).padStart(7)}%${(t.p!=null?t.p.toFixed(3):'—').padStart(10)}${t.expR.toFixed(3).padStart(9)}${t.breakevenBps.toFixed(1).padStart(10)}bps${((t.first*100).toFixed(0)+'%/'+(t.second*100).toFixed(0)+'%').padStart(14)}${stable}`);
  }

  const withP = allTests.filter(t => t.p != null);
  const { survivors, tested, threshold } = S.fdr(withP, 0.10);
  console.log(`\n  ${tested} tests carried a p-value; FDR q=0.10 threshold: ${threshold!=null?threshold.toExponential(2):'none passed'}`);
  console.log(`  ${survivors.length} survive FDR.`);

  console.log(`\n${line}\n  DID ANY NEW FEATURE ACTUALLY BEAT THE ENSEMBLE BASELINE?\n${line}`);
  for (const coin of COINS) {
    const base = allTests.find(t => t.coin === coin && t.label === 'Ensemble top10% (baseline)');
    if (!base) continue;
    console.log(`\n  ${coin}: baseline (Ensemble top10%) = ${(base.wr*100).toFixed(1)}% WR, n=${base.n}`);
    const others = allTests.filter(t => t.coin === coin && t.label !== base.label);
    for (const t of others.sort((a,b) => b.wr - a.wr)) {
      const beats = t.wr > base.wr ? `+${((t.wr-base.wr)*100).toFixed(1)}pp` : `${((t.wr-base.wr)*100).toFixed(1)}pp`;
      const nRatio = (t.n / base.n * 100).toFixed(0);
      console.log(`    ${t.label.padEnd(46)} ${(t.wr*100).toFixed(1)}% (${beats}, n=${t.n} = ${nRatio}% of baseline's trade count)`);
    }
  }

  fs.mkdirSync(path.join(__dirname, 'results'), { recursive: true });
  fs.writeFileSync(path.join(__dirname, 'results', 'new-features.json'),
    JSON.stringify({ generatedAt: new Date().toISOString(), costBps: COST_BPS, horizon: HORIZON, tested, survivorsCount: survivors.length, allTests }, null, 2));
  console.log(`\n${line}\n`);
}

main();
