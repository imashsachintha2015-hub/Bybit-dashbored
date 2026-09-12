#!/usr/bin/env node
/**
 * STUDY 17 — Can the surviving formula family (Part X / Study 15) be
 * upgraded to a higher win rate?
 *
 * Study 15 found 5 of the uploaded report's price formulas (F1, F3, F6, F7,
 * F8) survive every gate at h=2 (30min) on BTC/ETH — but their effective
 * rank is 2.44/8, meaning they are mostly ONE signal (short-horizon trend
 * efficiency) restated five ways. "Upgrade the formula" is tested here as
 * four separate, legitimate things rather than one vague hope:
 *
 *   1. ENSEMBLE. Averaging five noisy measurements of the same underlying
 *      signal is a standard, principled way to reduce noise -- does it beat
 *      the single best formula (F3_TrendEfficiency)?
 *   2. TIGHTER PERCENTILE. If the signal is real and monotonic, the most
 *      extreme 2-5% of readings should show a HIGHER win rate than the top
 *      10% (fewer, better trades) -- a genuine signal should behave this
 *      way; noise dressed as signal usually does not scale cleanly.
 *   3. CONVICTION / AGREEMENT. Requiring several of the five formulas to
 *      simultaneously agree (all in their own top decile at once) is a
 *      cheap way to filter for the moments the shared underlying signal is
 *      strongest, at the cost of fewer trades.
 *   4. REGIME FILTER. Does restricting to bars where trend efficiency (the
 *      vol-ratio proxy already in the base series) is itself in a favorable
 *      regime raise the win rate of the decile signal -- the same
 *      regime-conditional-validity idea from Part III/IV, applied here.
 *
 * Same discipline as Study 15: causal (walk-forward, refreshed-every-200-bar)
 * decile thresholds, moving-block bootstrap significance, split-half
 * stability, FDR across every test run here, and cost-adjusted expectancy
 * (breakeven bps = (2*WR-1)*50, since this is the same symmetric 0.5%/0.5%
 * barrier as Study 15).
 */
const fs = require('fs');
const path = require('path');
const S = require('./lib-stats.js');

const DATA = path.join(__dirname, '..', 'backtest', 'data');
const COINS = ['BTCUSDT', 'ETHUSDT'];   // the two coins Study 15 found survivors on
const HORIZON = 2;                       // 30min -- where the effect was strongest
const COST_BPS = 4;

function mean(a) { return a.length ? a.reduce((x, y) => x + y, 0) / a.length : 0; }
function sd(a) {
  if (a.length < 2) return 0;
  const m = mean(a);
  return Math.sqrt(a.reduce((x, y) => x + (y - m) ** 2, 0) / (a.length - 1));
}
function median(a) { const s = a.slice().sort((x, y) => x - y); return s[Math.floor(s.length / 2)]; }
function tanh(x) { return Math.tanh(x); }

// ─── Base series (identical to Study 15) ───
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

// ─── The 5 survivor formulas, verbatim from Study 15 ───
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

// Causal (walk-forward) threshold at an arbitrary top fraction, refreshed
// every 200 bars (same fix as Study 15 -- avoids the O(n^2 log n) re-sort).
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

function main() {
  const line = '─'.repeat(112);
  console.log(`\n${line}\n  STUDY 17 — UPGRADING THE SURVIVING FORMULA FAMILY (ensemble / tighter percentile / conviction / regime)\n${line}`);

  const allTests = [];

  for (const coin of COINS) {
    const bars = JSON.parse(fs.readFileSync(path.join(DATA, `${coin}.json`), 'utf8')).series['15'];
    const b = buildBaseSeries(bars);
    const n = bars.length;

    const outcomes = new Array(n).fill(null);
    for (let i = 0; i < n - HORIZON; i++) outcomes[i] = barrierOutcome(bars, i, HORIZON);

    // Individual formula scores
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

    // ── 1. Baseline: single best formula (F3) at top-10% ──
    const f3Mask = causalTopMask(scores.F3_TrendEfficiency, 0.10, 200);
    const baseline = evalMask(bars, outcomes, f3Mask, 'F3 alone, top10%', { coin });
    if (baseline) allTests.push(baseline);

    // ── 2. Ensemble: mean of the 5 scores, top-10% ──
    const ensemble = new Array(n).fill(null);
    for (let i = 0; i < n; i++) {
      const vals = Object.values(scores).map(s => s[i]).filter(v => v != null);
      if (vals.length === 5) ensemble[i] = mean(vals);
    }
    const ensMask10 = causalTopMask(ensemble, 0.10, 200);
    const ens10 = evalMask(bars, outcomes, ensMask10, 'Ensemble (5-formula mean), top10%', { coin });
    if (ens10) allTests.push(ens10);

    // ── 3. Tighter percentile sweep, on both F3 alone and the ensemble ──
    for (const frac of [0.05, 0.02]) {
      const f3T = evalMask(bars, outcomes, causalTopMask(scores.F3_TrendEfficiency, frac, 200), `F3 alone, top${(frac*100)}%`, { coin });
      if (f3T) allTests.push(f3T);
      const ensT = evalMask(bars, outcomes, causalTopMask(ensemble, frac, 200), `Ensemble, top${(frac*100)}%`, { coin });
      if (ensT) allTests.push(ensT);
    }

    // ── 4. Conviction: require k-of-5 individual formulas simultaneously in their OWN top-10% ──
    const indivMasks = {};
    for (const name of Object.keys(FORMULAS)) indivMasks[name] = causalTopMask(scores[name], 0.10, 200);
    const agreeCount = new Array(n).fill(0);
    for (let i = 0; i < n; i++) agreeCount[i] = Object.values(indivMasks).filter(m => m[i]).length;
    for (const k of [3, 4, 5]) {
      const mask = agreeCount.map(c => c >= k);
      const t = evalMask(bars, outcomes, mask, `Conviction >=${k}-of-5 agree`, { coin });
      if (t) allTests.push(t);
    }

    // ── 5. Regime filter: F3 top-10% signal, split by trend-efficiency (eff) itself ──
    const effVals = f3Mask.map((m, i) => m && b.eff[i] != null ? b.eff[i] : null).filter(v => v != null);
    const effMedian = median(effVals);
    const highEffMask = f3Mask.map((m, i) => m && b.eff[i] != null && b.eff[i] >= effMedian);
    const lowEffMask = f3Mask.map((m, i) => m && b.eff[i] != null && b.eff[i] < effMedian);
    const highEff = evalMask(bars, outcomes, highEffMask, 'F3 top10% AND high-efficiency regime', { coin });
    const lowEff = evalMask(bars, outcomes, lowEffMask, 'F3 top10% AND low-efficiency regime', { coin });
    if (highEff) allTests.push(highEff);
    if (lowEff) allTests.push(lowEff);

    process.stdout.write(`  ${coin} done\n`);
  }

  console.log(`\n  ${'coin'.padEnd(8)}${'test'.padEnd(38)}${'n'.padStart(7)}${'WR'.padStart(8)}${'p(boot)'.padStart(10)}${'expR'.padStart(9)}${'breakeven'.padStart(11)}${'1st/2nd'.padStart(14)}`);
  for (const t of allTests) {
    const stable = Math.sign(t.first - 0.5) === Math.sign(t.second - 0.5) ? '' : '  <- FLIPS';
    console.log(`  ${t.coin.padEnd(8)}${t.label.padEnd(38)}${String(t.n).padStart(7)}${(t.wr*100).toFixed(1).padStart(7)}%${(t.p!=null?t.p.toFixed(3):'—').padStart(10)}${t.expR.toFixed(3).padStart(9)}${t.breakevenBps.toFixed(1).padStart(10)}bps${((t.first*100).toFixed(0)+'%/'+(t.second*100).toFixed(0)+'%').padStart(14)}${stable}`);
  }

  const withP = allTests.filter(t => t.p != null);
  const { survivors, tested, threshold } = S.fdr(withP, 0.10);
  console.log(`\n  ${tested} tests carried a p-value; FDR q=0.10 threshold: ${threshold!=null?threshold.toExponential(2):'none passed'}`);
  console.log(`  ${survivors.length} survive FDR.`);

  // Direct comparison: does any "upgrade" beat the Study 15 baseline (F3 alone, top10%) on the SAME coin?
  console.log(`\n${line}\n  DID ANY UPGRADE ACTUALLY BEAT THE BASELINE?\n${line}`);
  for (const coin of COINS) {
    const base = allTests.find(t => t.coin === coin && t.label === 'F3 alone, top10%');
    if (!base) continue;
    console.log(`\n  ${coin}: baseline (F3 alone, top10%) = ${(base.wr*100).toFixed(1)}% WR, n=${base.n}`);
    const others = allTests.filter(t => t.coin === coin && t.label !== base.label);
    for (const t of others.sort((a,b) => b.wr - a.wr)) {
      const beats = t.wr > base.wr ? `+${((t.wr-base.wr)*100).toFixed(1)}pp` : `${((t.wr-base.wr)*100).toFixed(1)}pp`;
      const nRatio = (t.n / base.n * 100).toFixed(0);
      console.log(`    ${t.label.padEnd(38)} ${(t.wr*100).toFixed(1)}% (${beats}, n=${t.n} = ${nRatio}% of baseline's trade count)`);
    }
  }

  fs.mkdirSync(path.join(__dirname, 'results'), { recursive: true });
  fs.writeFileSync(path.join(__dirname, 'results', 'upgrade-formula.json'),
    JSON.stringify({ generatedAt: new Date().toISOString(), costBps: COST_BPS, horizon: HORIZON, tested, survivorsCount: survivors.length, allTests }, null, 2));
  console.log(`\n${line}\n`);
}

main();
