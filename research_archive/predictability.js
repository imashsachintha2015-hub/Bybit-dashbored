#!/usr/bin/env node
/**
 * STUDY 1 — How much of this market is predictable at all, and which part?
 *
 * "Why can't we build an accurate prophet?" is the right question asked of the
 * wrong variable. Price direction and price VOLATILITY are two completely
 * different prediction problems, and they have completely different answers.
 * This measures both on the same data so the difference is visible rather than
 * asserted.
 *
 * What is measured:
 *
 *  1. RETURN AUTOCORRELATION. Does the last move tell you the next one? If
 *     markets were predictable from their own history in the naive sense, this
 *     would be large. It is the most-tested quantity in all of finance.
 *
 *  2. ABSOLUTE-RETURN AUTOCORRELATION. Does the last move's SIZE tell you the
 *     next one's size? This is volatility clustering, and it is one of the most
 *     robust empirical regularities that exists in markets.
 *
 *  3. VARIANCE RATIO. If prices were a pure random walk, variance would scale
 *     linearly with the horizon, so VR(k) = 1. Above 1 means trending
 *     (momentum), below 1 means mean-reverting. Deviations from 1 are the
 *     honest measure of how much structure exists.
 *
 *  4. SIGN PREDICTABILITY CEILING. Best achievable next-bar direction accuracy
 *     from simple history-based predictors, so the ceiling is a number rather
 *     than a feeling.
 *
 *  5. VOLATILITY PREDICTABILITY. The same exercise for volatility, so the two
 *     can be compared directly.
 *
 * The point of the study is not pessimism. It is to locate the edge where it
 * actually is instead of where it is most intuitive to look for it.
 */
const fs = require('fs');
const path = require('path');
const I = require('../agents/indicators.js');

const DATA_DIR = path.join(__dirname, '..', 'backtest', 'data');

function autocorr(series, lag) {
  const n = series.length - lag;
  if (n < 30) return null;
  const a = series.slice(0, n), b = series.slice(lag, lag + n);
  const ma = a.reduce((x, y) => x + y, 0) / n;
  const mb = b.reduce((x, y) => x + y, 0) / n;
  let num = 0, da = 0, db = 0;
  for (let i = 0; i < n; i++) {
    num += (a[i] - ma) * (b[i] - mb);
    da += (a[i] - ma) ** 2;
    db += (b[i] - mb) ** 2;
  }
  return (da && db) ? num / Math.sqrt(da * db) : null;
}

/** Variance ratio: VR(k) = Var(k-period return) / (k * Var(1-period return)). */
function varianceRatio(returns, k) {
  if (returns.length < k * 30) return null;
  const v1 = variance(returns);
  const agg = [];
  for (let i = 0; i + k <= returns.length; i += 1) {
    agg.push(returns.slice(i, i + k).reduce((a, b) => a + b, 0));
  }
  const vk = variance(agg);
  return (v1 && k) ? vk / (k * v1) : null;
}

function variance(arr) {
  if (arr.length < 2) return 0;
  const m = arr.reduce((a, b) => a + b, 0) / arr.length;
  return arr.reduce((a, b) => a + (b - m) ** 2, 0) / (arr.length - 1);
}

/**
 * Walk-forward accuracy of simple predictors. Every prediction at bar i uses
 * only information available at bar i.
 */
function signPredictors(returns) {
  const preds = {
    'momentum (last bar)': (h) => h[h.length - 1],
    'momentum (last 5)': (h) => h.slice(-5).reduce((a, b) => a + b, 0),
    'mean reversion (last bar)': (h) => -h[h.length - 1],
    'mean reversion (last 5)': (h) => -h.slice(-5).reduce((a, b) => a + b, 0),
    'always up': () => 1
  };
  const out = {};
  for (const [name, fn] of Object.entries(preds)) {
    let correct = 0, total = 0, capturedMove = 0;
    for (let i = 20; i < returns.length - 1; i++) {
      const signal = fn(returns.slice(0, i));
      if (!signal) continue;
      const dir = signal > 0 ? 1 : -1;
      const actual = returns[i];
      if (actual === 0) continue;
      total++;
      if (dir * actual > 0) correct++;
      capturedMove += dir * actual;
    }
    out[name] = {
      accuracy: total ? +((correct / total) * 100).toFixed(2) : null,
      n: total,
      avgCapturedBps: total ? +((capturedMove / total) * 10000).toFixed(3) : null
    };
  }
  return out;
}

/** Volatility predictability: does past realised vol predict future vol? */
function volPredictability(returns, window = 24, horizon = 24) {
  const past = [], future = [];
  for (let i = window; i + horizon < returns.length; i++) {
    const p = Math.sqrt(variance(returns.slice(i - window, i)));
    const f = Math.sqrt(variance(returns.slice(i, i + horizon)));
    if (p > 0 && f > 0) { past.push(p); future.push(f); }
  }
  if (past.length < 50) return null;
  // Correlation of past vol with future vol, and R^2 of the naive "tomorrow
  // looks like today" forecast.
  const n = past.length;
  const mp = past.reduce((a, b) => a + b, 0) / n;
  const mf = future.reduce((a, b) => a + b, 0) / n;
  let num = 0, dp = 0, df = 0;
  for (let i = 0; i < n; i++) {
    num += (past[i] - mp) * (future[i] - mf);
    dp += (past[i] - mp) ** 2;
    df += (future[i] - mf) ** 2;
  }
  const r = num / Math.sqrt(dp * df);
  return { correlation: +r.toFixed(4), rSquared: +(r * r).toFixed(4), n };
}

function main() {
  const files = fs.readdirSync(DATA_DIR).filter(f => f.endsWith('.json') && !f.includes('-derivs'));
  const line = '─'.repeat(78);

  console.log(`\n${line}`);
  console.log('  STUDY 1 — WHAT IS ACTUALLY PREDICTABLE');
  console.log(line);

  for (const f of files) {
    const symbol = f.replace('.json', '');
    const bundle = JSON.parse(fs.readFileSync(path.join(DATA_DIR, f), 'utf8'));
    const bars = bundle.series['15'];
    if (!bars || bars.length < 1000) continue;

    const closes = bars.map(c => c.close);
    const returns = [];
    for (let i = 1; i < closes.length; i++) returns.push(Math.log(closes[i] / closes[i - 1]));
    const absReturns = returns.map(Math.abs);

    console.log(`\n  ${symbol} — ${returns.length} x 15m returns\n`);

    console.log('  DIRECTION: autocorrelation of returns (is the next move predictable from the last?)');
    const lags = [1, 2, 3, 5, 10, 20];
    console.log('    lag      ' + lags.map(l => String(l).padStart(8)).join(''));
    console.log('    r        ' + lags.map(l => {
      const v = autocorr(returns, l);
      return (v == null ? '—' : v.toFixed(4)).padStart(8);
    }).join(''));

    console.log('\n  VOLATILITY: autocorrelation of |returns| (is the next move SIZE predictable?)');
    console.log('    lag      ' + lags.map(l => String(l).padStart(8)).join(''));
    console.log('    r        ' + lags.map(l => {
      const v = autocorr(absReturns, l);
      return (v == null ? '—' : v.toFixed(4)).padStart(8);
    }).join(''));

    const vrs = [2, 4, 8, 16, 32];
    console.log('\n  VARIANCE RATIO (1.0 = random walk, >1 trending, <1 mean-reverting)');
    console.log('    k        ' + vrs.map(k => String(k).padStart(8)).join(''));
    console.log('    VR       ' + vrs.map(k => {
      const v = varianceRatio(returns, k);
      return (v == null ? '—' : v.toFixed(3)).padStart(8);
    }).join(''));

    console.log('\n  DIRECTION-PREDICTION CEILING (walk-forward, no look-ahead)');
    const sp = signPredictors(returns);
    console.log(`    ${'predictor'.padEnd(28)}${'accuracy %'.padStart(12)}${'avg captured (bps)'.padStart(21)}`);
    for (const [name, r] of Object.entries(sp)) {
      console.log(`    ${name.padEnd(28)}${String(r.accuracy).padStart(12)}${String(r.avgCapturedBps).padStart(21)}`);
    }

    const vp = volPredictability(returns);
    if (vp) {
      console.log('\n  VOLATILITY-PREDICTION (past 24 bars of vol vs next 24 bars of vol)');
      console.log(`    correlation ${vp.correlation}   R² ${vp.rSquared}   n=${vp.n}`);
      console.log(`    -> ${(vp.rSquared * 100).toFixed(1)}% of future volatility is explained by recent volatility alone.`);
    }
  }

  console.log(`\n${line}`);
  console.log('  HOW TO READ THIS');
  console.log(line);
  console.log(`
  Compare the two autocorrelation rows. Return autocorrelation sits near zero at
  every lag: the direction of the next move is very nearly independent of the
  last one. Absolute-return autocorrelation is large and decays slowly: the SIZE
  of the next move is strongly predictable from the size of recent ones.

  This is volatility clustering, and it is among the most robust findings in
  empirical finance. It is also the direct answer to "why can't we identify
  chaos first" — you can. Chaos is the predictable part. Direction is not.

  The direction-prediction table shows the ceiling: simple predictors land
  within roughly a percentage point of 50%, and the average captured move per
  trade is a fraction of a basis point. Against a round-trip cost of about 110
  basis points as a taker, that is not a small edge — it is no edge.
`);
  console.log(line + '\n');
}

main();
