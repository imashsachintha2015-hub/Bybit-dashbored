#!/usr/bin/env node
/**
 * STUDY 14 — Three constructs that do not exist in any indicator library,
 * tested specifically for 30-minute direction, across four coins together.
 *
 * The brief: instead of instant exits, commit to "where does price go in the
 * next 30 minutes" as the actual bet, and invent mathematics for it rather
 * than reusing anything already tried — including using all four coins'
 * data AT ONCE, not one at a time, since that is information no single-asset
 * study in this project has used.
 *
 * Note on the 30-minute question directly: Part VI already tested a combined
 * price+derivatives model at exactly this horizon (2 bars on 15m) and found
 * IC of -0.052 to +0.030 — indistinguishable from zero. This study does not
 * repeat that; it tests three constructs that were never tried at all.
 *
 * 1. MARKET GRAVITY. Retail volume-profile indicators use the nearest
 *    high-volume node linearly. This instead applies an inverse-square law —
 *    literally Newton's F = m / r^2 — treating each historical volume node as
 *    a mass and current price as a test particle, and sums the signed pull
 *    from every node in range. Nothing in any indicator library does this;
 *    the inverse-square kernel is a specific, testable, physically-motivated
 *    choice that linear distance-weighting is not.
 *
 * 2. PERMUTATION ENTROPY REGIME FILTER (Bandt & Pompe, 2002 — genuine applied
 *    mathematics from nonlinear dynamics, not finance). Measures how ORDERED
 *    the sequence of recent price moves is, via the diversity of ordinal
 *    patterns, independent of their size or direction. The idea being tested:
 *    maybe direction is unpredictable on AVERAGE (established repeatedly) but
 *    predictable specifically in the rare windows where the path is unusually
 *    ordered rather than random-looking — which is exactly the "5 good trades,
 *    not 100" instinct, made mathematically precise as a selection filter
 *    rather than a vague feeling.
 *
 * 3. CROSS-ASSET DISPERSION RANK. The only construct here that literally
 *    cannot be computed from one coin's data. At every bar, rank each of the
 *    four coins by its own recent return among the other three. Tests whether
 *    a coin that has become the basket's outlier — the laggard or the leader
 *    relative to its normally-correlated peers — is pulled back toward them.
 *    This is cross-sectional information, structurally unavailable to any
 *    single-asset indicator, however cleverly it is built.
 *
 * All three tested at EXACTLY 30 minutes (2 bars on 15m data), on four coins,
 * with the same four gates as everything else: HAC standard errors, FDR
 * across the whole study, split-sample validation, cost floor.
 */
const fs = require('fs');
const path = require('path');
const S = require('./lib-stats.js');

const DATA = path.join(__dirname, '..', 'backtest', 'data');
const COINS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'DOGEUSDT'];
const HORIZON = 2;   // 2 bars x 15m = 30 minutes, exactly what was asked

function sd(a) {
  if (a.length < 2) return 0;
  const m = a.reduce((x, y) => x + y, 0) / a.length;
  return Math.sqrt(a.reduce((x, y) => x + (y - m) ** 2, 0) / (a.length - 1));
}

function loadAll() {
  const out = {};
  for (const c of COINS) {
    const f = path.join(DATA, `${c}.json`);
    if (!fs.existsSync(f)) continue;
    const bars = JSON.parse(fs.readFileSync(f, 'utf8')).series['15'];
    if (bars && bars.length > 5000) out[c] = bars;
  }
  return out;
}

// ═══════════════════════════════════════════════════════════════════════
// 1. MARKET GRAVITY — inverse-square pull from historical volume nodes
// ═══════════════════════════════════════════════════════════════════════
/**
 * Builds a coarse volume profile over the trailing `lookback` bars (binned by
 * price), then computes the net signed force on current price using an
 * inverse-square kernel: each bin contributes volume / (distance_in_ATR + eps)^2,
 * signed toward the bin. Positive = net pull upward.
 */
function marketGravity(bars, i, lookback, bins, atr) {
  if (i < lookback || !atr) return null;
  const win = bars.slice(i - lookback, i);
  const hi = Math.max(...win.map(c => c.high)), lo = Math.min(...win.map(c => c.low));
  if (hi <= lo) return null;
  const binSize = (hi - lo) / bins;
  const vol = new Array(bins).fill(0);
  for (const c of win) {
    const range = c.high - c.low;
    if (range <= 0) continue;
    const b0 = Math.max(0, Math.min(bins - 1, Math.floor((c.low - lo) / binSize)));
    const b1 = Math.max(0, Math.min(bins - 1, Math.floor((c.high - lo) / binSize)));
    const span = b1 - b0 + 1;
    for (let b = b0; b <= b1; b++) vol[b] += c.volume / span;
  }
  const price = bars[i].close;
  let force = 0, totalMass = 0;
  for (let b = 0; b < bins; b++) {
    if (!vol[b]) continue;
    const binPrice = lo + (b + 0.5) * binSize;
    const distAtr = Math.abs(binPrice - price) / atr;
    const eps = 0.15;   // softens the singularity for a node at the current price
    const pull = vol[b] / ((distAtr + eps) ** 2);
    force += pull * Math.sign(binPrice - price);
    totalMass += vol[b];
  }
  return totalMass > 0 ? force / totalMass : null;   // normalised, so magnitude is comparable across regimes
}

function atrAt(bars, i, p) {
  let tr = 0, n = 0;
  for (let k = Math.max(1, i - p); k < i; k++) {
    tr += Math.max(bars[k].high - bars[k].low, Math.abs(bars[k].high - bars[k - 1].close), Math.abs(bars[k].low - bars[k - 1].close));
    n++;
  }
  return n ? tr / n : 0;
}

// ═══════════════════════════════════════════════════════════════════════
// 2. PERMUTATION ENTROPY — Bandt & Pompe 2002
// ═══════════════════════════════════════════════════════════════════════
/** Ordinal pattern index for d consecutive values (one of d! possible orders). */
function ordinalPattern(vals) {
  const idx = vals.map((v, i) => i).sort((a, b) => vals[a] - vals[b]);
  return idx.join(',');
}

/** Permutation entropy of returns over a trailing window, order d, normalised to [0,1]. */
function permutationEntropy(rets, i, window, d) {
  if (i < window + d) return null;
  const counts = {};
  let total = 0;
  for (let k = i - window; k < i - d + 1; k++) {
    const pat = ordinalPattern(rets.slice(k, k + d));
    counts[pat] = (counts[pat] || 0) + 1;
    total++;
  }
  if (!total) return null;
  let h = 0;
  for (const c of Object.values(counts)) { const p = c / total; h -= p * Math.log2(p); }
  const maxH = Math.log2(factorial(d));
  return maxH > 0 ? h / maxH : null;
}
function factorial(n) { let r = 1; for (let i = 2; i <= n; i++) r *= i; return r; }

// ═══════════════════════════════════════════════════════════════════════
// 3. CROSS-ASSET DISPERSION RANK
// ═══════════════════════════════════════════════════════════════════════
/** At bar i, each coin's trailing return relative to the basket's mean and
 * spread — a cross-sectional z-score, computable only with all coins present. */
function crossSectionalZ(returnsByCoin, coin, i) {
  // Must exclude the coin's own value: a z-score of a point against a sample
  // that includes itself is mathematically bounded (for n points, the maximum
  // possible |z| for a member of its own sample is (n-1)/sqrt(n) ~= 1.5 at
  // n=4) and starves exactly the extreme events this construct is trying to
  // find. The comment always said "against the OTHER three"; the first
  // version of the code did not actually do that.
  const others = [];
  for (const c of COINS) {
    if (c === coin) continue;
    if (returnsByCoin[c] && returnsByCoin[c][i] != null) others.push(returnsByCoin[c][i]);
  }
  if (others.length < 3) return null;
  const m = S.mean(others), s = sd(others);
  const own = returnsByCoin[coin][i];
  return s > 0 ? (own - m) / s : null;
}

function main() {
  const args = {};
  for (let a = 2; a < process.argv.length; a += 2) args[process.argv[a].replace(/^--/, '')] = process.argv[a + 1];
  const costBps = parseFloat(args.cost || '4');
  const line = '─'.repeat(100);

  console.log(`\n${line}\n  STUDY 14 — THREE NEW CONSTRUCTS, TESTED AT EXACTLY 30 MINUTES, FOUR COINS TOGETHER\n${line}`);
  console.log(`
  Horizon fixed at ${HORIZON} bars (30 min) throughout, as asked. Cost: ${costBps} bps round trip.
`);

  const bars = loadAll();
  const coinsLoaded = Object.keys(bars);
  console.log(`  Coins loaded: ${coinsLoaded.join(', ')}\n`);
  const allTests = [];

  // ── Precompute trailing-window returns for cross-sectional use ──
  const retWindow = 8; // 2-hour trailing return, in 15m bars
  const trailingRet = {};
  for (const c of coinsLoaded) {
    const b = bars[c];
    trailingRet[c] = new Array(b.length).fill(null);
    for (let i = retWindow; i < b.length; i++) trailingRet[c][i] = Math.log(b[i].close / b[i - retWindow].close);
  }

  // ═══ 1. MARKET GRAVITY ═══
  console.log(`${line}\n  1. MARKET GRAVITY (inverse-square pull from volume nodes)\n${line}`);
  console.log(`  ${'symbol'.padEnd(10)}${'lookback'.padEnd(10)}${'threshold'.padEnd(11)}${'n'.padStart(7)}${'mean bps'.padStart(10)}${'t(HAC)'.padStart(9)}${'1st'.padStart(9)}${'2nd'.padStart(9)}`);
  for (const c of coinsLoaded) {
    const b = bars[c];
    for (const lookback of [40, 96]) {
      const gravity = new Array(b.length).fill(null);
      for (let i = lookback; i < b.length; i++) {
        const atr = atrAt(b, i, 14);
        gravity[i] = marketGravity(b, i, lookback, 24, atr);
      }
      const vals = gravity.filter(v => v != null);
      const absVals = vals.map(Math.abs).sort((x, y) => x - y);
      const thresh = absVals[Math.floor(absVals.length * 0.8)];   // top-quintile pull strength
      const mid = Math.floor(b.length / 2);

      const trades = [], first = [], second = [];
      for (let i = lookback; i < b.length - HORIZON; i++) {
        const g = gravity[i];
        if (g == null || Math.abs(g) < thresh) continue;
        const fwdBps = ((b[i + HORIZON].close - b[i].close) / b[i].close) * 10000;
        const dir = Math.sign(g);   // bet price moves TOWARD the net pull
        const v = dir * fwdBps - costBps;
        trades.push(v);
        (i < mid ? first : second).push(v);
      }
      if (trades.length < 100) continue;
      const t = S.hacT(trades, Math.max(0, HORIZON - 1));
      if (!t) continue;
      allTests.push({ family: 'gravity', symbol: c, lookback, n: t.n, mean: t.mean, t: t.t, p: t.p, first: S.mean(first), second: S.mean(second) });
      console.log(`  ${c.padEnd(10)}${String(lookback).padEnd(10)}${'top 20%'.padEnd(11)}${String(t.n).padStart(7)}${t.mean.toFixed(2).padStart(10)}${t.t.toFixed(2).padStart(9)}${S.mean(first).toFixed(1).padStart(9)}${S.mean(second).toFixed(1).padStart(9)}`);
    }
  }

  // ═══ 2. PERMUTATION ENTROPY REGIME FILTER ═══
  console.log(`\n${line}\n  2. PERMUTATION ENTROPY REGIME FILTER (trade only when the path is unusually ORDERED)\n${line}`);
  console.log(`
  Base signal: simple 5-bar reversion (the strongest simple predictor found in
  Part IV/VI). The question: does it work specifically in low-entropy windows,
  even though it does not work on average?
`);
  console.log(`  ${'symbol'.padEnd(10)}${'entropy band'.padEnd(16)}${'n'.padStart(7)}${'mean bps'.padStart(10)}${'t(HAC)'.padStart(9)}${'1st'.padStart(9)}${'2nd'.padStart(9)}`);
  for (const c of coinsLoaded) {
    const b = bars[c];
    const closes = b.map(x => x.close);
    const rets = [0];
    for (let i = 1; i < closes.length; i++) rets.push(Math.log(closes[i] / closes[i - 1]));

    const window = 48, d = 4;
    const pe = new Array(b.length).fill(null);
    for (let i = window + d; i < b.length; i++) pe[i] = permutationEntropy(rets, i, window, d);
    const peVals = pe.filter(v => v != null).sort((x, y) => x - y);
    const q20 = peVals[Math.floor(peVals.length * 0.2)];
    const q80 = peVals[Math.floor(peVals.length * 0.8)];
    const mid = Math.floor(b.length / 2);

    for (const [label, test] of [
      ['lowest 20% (ordered)', v => v != null && v <= q20],
      ['highest 20% (random)', v => v != null && v >= q80],
      ['all bars (baseline)', v => v != null]
    ]) {
      const trades = [], first = [], second = [];
      for (let i = window + d; i < b.length - HORIZON - 5; i++) {
        if (!test(pe[i])) continue;
        // 5-bar reversion signal.
        const back5 = Math.log(b[i].close / b[i - 5].close);
        if (!back5) continue;
        const dir = -Math.sign(back5);
        const fwdBps = ((b[i + HORIZON].close - b[i].close) / b[i].close) * 10000;
        const v = dir * fwdBps - costBps;
        trades.push(v);
        (i < mid ? first : second).push(v);
      }
      if (trades.length < 100) continue;
      const t = S.hacT(trades, Math.max(0, HORIZON - 1));
      if (!t) continue;
      allTests.push({ family: 'permEntropy', symbol: c, label, n: t.n, mean: t.mean, t: t.t, p: t.p, first: S.mean(first), second: S.mean(second) });
      console.log(`  ${c.padEnd(10)}${label.padEnd(16)}${String(t.n).padStart(7)}${t.mean.toFixed(2).padStart(10)}${t.t.toFixed(2).padStart(9)}${S.mean(first).toFixed(1).padStart(9)}${S.mean(second).toFixed(1).padStart(9)}`);
    }
  }

  // ═══ 3. CROSS-ASSET DISPERSION RANK ═══
  console.log(`\n${line}\n  3. CROSS-ASSET DISPERSION RANK (uses all four coins at once — not computable from one)\n${line}`);
  console.log(`
  At every bar, each coin's ${retWindow}-bar return is z-scored against the OTHER
  three coins' ${retWindow}-bar returns at that same instant. Tests whether being
  the basket's extreme outlier predicts snapping back toward the group.
`);
  console.log(`  ${'symbol'.padEnd(10)}${'extreme'.padEnd(12)}${'n'.padStart(7)}${'mean bps'.padStart(10)}${'t(HAC)'.padStart(9)}${'1st'.padStart(9)}${'2nd'.padStart(9)}`);
  const minLen = Math.min(...coinsLoaded.map(c => bars[c].length));
  for (const c of coinsLoaded) {
    const b = bars[c];
    const mid = Math.floor(minLen / 2);
    for (const [label, want] of [['laggard (z<-1.5)', z => z < -1.5], ['leader (z>1.5)', z => z > 1.5]]) {
      const trades = [], first = [], second = [];
      for (let i = retWindow; i < minLen - HORIZON; i++) {
        const z = crossSectionalZ(trailingRet, c, i);
        if (z == null || !want(z)) continue;
        const dir = -Math.sign(z);   // bet the outlier reverts toward the basket
        const fwdBps = ((b[i + HORIZON].close - b[i].close) / b[i].close) * 10000;
        const v = dir * fwdBps - costBps;
        trades.push(v);
        (i < mid ? first : second).push(v);
      }
      if (trades.length < 100) continue;
      const t = S.hacT(trades, Math.max(0, HORIZON - 1));
      if (!t) continue;
      allTests.push({ family: 'crossAsset', symbol: c, label, n: t.n, mean: t.mean, t: t.t, p: t.p, first: S.mean(first), second: S.mean(second) });
      console.log(`  ${c.padEnd(10)}${label.padEnd(12)}${String(t.n).padStart(7)}${t.mean.toFixed(2).padStart(10)}${t.t.toFixed(2).padStart(9)}${S.mean(first).toFixed(1).padStart(9)}${S.mean(second).toFixed(1).padStart(9)}`);
    }
  }

  // ── Overall verdict ──
  const { survivors, tested, threshold } = S.fdr(allTests, 0.10);
  console.log(`\n${line}\n  FDR ACROSS ALL ${tested} TESTS (all three constructs, all coins, 30-minute horizon)\n${line}`);
  if (!survivors.length) {
    console.log(`  Nothing survived.`);
  } else {
    const solid = survivors.filter(s => s.mean > 0 && Math.sign(s.first) === Math.sign(s.second) && Math.sign(s.first) === Math.sign(s.mean));
    console.log(`  ${survivors.length} of ${tested} passed FDR (p <= ${threshold.toExponential(2)}). ${solid.length} positive and stable across both halves.`);
    if (solid.length) {
      console.log(`\n  ${'family'.padEnd(14)}${'symbol'.padEnd(10)}${'detail'.padEnd(16)}${'n'.padStart(7)}${'mean bps'.padStart(10)}${'t'.padStart(8)}`);
      for (const s of solid.sort((a, b) => b.mean - a.mean)) {
        console.log(`  ${s.family.padEnd(14)}${s.symbol.padEnd(10)}${(s.lookback ? String(s.lookback) : s.label || '').padEnd(16)}${String(s.n).padStart(7)}${s.mean.toFixed(2).padStart(10)}${s.t.toFixed(2).padStart(8)}`);
      }
    }
  }
  console.log(`\n${line}\n`);

  fs.mkdirSync(path.join(__dirname, 'results'), { recursive: true });
  fs.writeFileSync(path.join(__dirname, 'results', 'creative-equations.json'),
    JSON.stringify({ generatedAt: new Date().toISOString(), horizonBars: HORIZON, costBps, coins: coinsLoaded, tests: allTests }, null, 2));
}

main();
