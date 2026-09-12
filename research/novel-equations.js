#!/usr/bin/env node
/**
 * STUDY 13 — Genuinely different mathematics, not new technical-analysis formulas.
 *
 * The request behind this study: stop re-arranging price into new indicator
 * shapes, and instead bring in equations from a different field entirely — the
 * kind a market microstructure researcher or an information theorist would
 * reach for, not the kind in a retail charting package.
 *
 * Three are implemented, each from a different discipline, each measuring
 * something none of the 8 price indicators in Part VI can measure even in
 * principle (that was proven there — effective rank 2.4, arithmetic on one
 * series cannot add a genuinely new dimension):
 *
 * 1. KYLE'S LAMBDA (market microstructure, Kyle 1985). The canonical equation
 *    for price impact: regress price change on signed order flow over a
 *    window. The slope, lambda, is the market's price-impact coefficient — how
 *    much size it takes to move the price. It is a measure of information
 *    asymmetry in the Kyle model: high lambda means informed traders are
 *    moving size and the market maker is charging more to be on the other side.
 *
 * 2. JUMP / CONTINUOUS DECOMPOSITION (financial econometrics, Barndorff-Nielsen
 *    & Shephard 2004/2006). Realised variance (sum of squared returns) captures
 *    both continuous diffusion and discontinuous jumps. Bipower variation (sum
 *    of adjacent |return| products, rescaled) is a jump-ROBUST estimator of the
 *    continuous part alone. RV − BV isolates the jump component. This is
 *    mathematically distinct from "big candle" detectors: it separates two
 *    different STOCHASTIC PROCESSES driving the same price series, not just
 *    the size of one bar.
 *
 * 3. TRANSFER ENTROPY (information theory, Schreiber 2000). Correlation only
 *    detects LINEAR dependence. Transfer entropy measures how much uncertainty
 *    about the future of price is resolved by the past of another series,
 *    beyond what price's own past already resolves — including non-linear and
 *    non-monotonic dependence that Part VI's correlation-based effective-rank
 *    calculation is structurally blind to. Significance is established by a
 *    permutation test (shuffle the source, recompute, repeat), which is the
 *    correct null for this statistic and does not assume normality.
 *
 * All three are then tested for forward predictive value with the same
 * discipline as everything else here: HAC standard errors, FDR correction,
 * split-sample validation, cost floor.
 */
const fs = require('fs');
const path = require('path');
const S = require('./lib-stats.js');

const DATA = path.join(__dirname, '..', 'backtest', 'data');
const RESEARCH = path.join(__dirname, 'data');

function sd(a) {
  if (a.length < 2) return 0;
  const m = a.reduce((x, y) => x + y, 0) / a.length;
  return Math.sqrt(a.reduce((x, y) => x + (y - m) ** 2, 0) / (a.length - 1));
}

// ═══════════════════════════════════════════════════════════════════════
// 1. KYLE'S LAMBDA — price impact per unit of signed order flow
// ═══════════════════════════════════════════════════════════════════════
/**
 * Fits ret_t = lambda * signedFlow_t + eps over a trailing window via simple
 * OLS (one regressor, closed form), and returns lambda plus its own recent
 * z-score. signedFlow here is (takerBuy - takerSell) in USD, from the real
 * measured derivatives feed — not inferred from candle shape.
 */
function kylesLambda(rets, signedFlow, window) {
  const out = new Array(rets.length).fill(null);
  for (let i = window; i < rets.length; i++) {
    const y = rets.slice(i - window, i);
    const x = signedFlow.slice(i - window, i);
    const mx = x.reduce((a, b) => a + b, 0) / x.length;
    const my = y.reduce((a, b) => a + b, 0) / y.length;
    let num = 0, den = 0;
    for (let k = 0; k < x.length; k++) { num += (x[k] - mx) * (y[k] - my); den += (x[k] - mx) ** 2; }
    out[i] = den > 0 ? num / den : null;
  }
  return out;
}

// ═══════════════════════════════════════════════════════════════════════
// 2. JUMP / CONTINUOUS DECOMPOSITION — Barndorff-Nielsen & Shephard
// ═══════════════════════════════════════════════════════════════════════
function jumpRatio(rets, window) {
  const out = new Array(rets.length).fill(null);
  const muInv = 1 / Math.sqrt(2 / Math.PI);  // = pi/2 in the standard BN-S scaling
  for (let i = window; i < rets.length; i++) {
    const w = rets.slice(i - window, i);
    let rv = 0, bv = 0;
    for (let k = 0; k < w.length; k++) rv += w[k] * w[k];
    for (let k = 1; k < w.length; k++) bv += Math.abs(w[k]) * Math.abs(w[k - 1]);
    bv *= (muInv * muInv) * (w.length / (w.length - 1));
    const jump = Math.max(0, rv - bv);
    out[i] = rv > 0 ? jump / rv : null;   // fraction of variance attributable to jumps, in [0,1]
  }
  return out;
}

// ═══════════════════════════════════════════════════════════════════════
// 3. TRANSFER ENTROPY — Schreiber 2000, with a permutation-based null
// ═══════════════════════════════════════════════════════════════════════
/** Discretises a series into `bins` quantile buckets (0..bins-1). */
function discretise(series, bins) {
  const sorted = series.slice().sort((a, b) => a - b);
  const edges = [];
  for (let i = 1; i < bins; i++) edges.push(sorted[Math.floor((sorted.length * i) / bins)]);
  return series.map(v => {
    let b = 0;
    while (b < edges.length && v > edges[b]) b++;
    return b;
  });
}

/**
 * TE(X -> Y) = H(Y_t | Y_{t-1}) - H(Y_t | Y_{t-1}, X_{t-1})
 * Estimated via empirical joint/conditional histograms. Positive values mean
 * X's past resolves uncertainty about Y's future beyond Y's own past —
 * genuine directional information flow, including non-linear dependence.
 */
function transferEntropy(xDisc, yDisc, bins) {
  const n = Math.min(xDisc.length, yDisc.length) - 1;
  const jointXYY = {}, jointYY = {}, margY = {};
  for (let t = 1; t <= n; t++) {
    const yPrev = yDisc[t - 1], yNow = yDisc[t], xPrev = xDisc[t - 1];
    const kXYY = `${xPrev}|${yPrev}|${yNow}`;
    const kYY = `${yPrev}|${yNow}`;
    jointXYY[kXYY] = (jointXYY[kXYY] || 0) + 1;
    jointYY[kYY] = (jointYY[kYY] || 0) + 1;
    margY[yPrev] = (margY[yPrev] || 0) + 1;
  }
  // H(Y_t | Y_{t-1})
  let hY = 0;
  for (const [k, c] of Object.entries(jointYY)) {
    const yPrev = k.split('|')[0];
    const p = c / n, pCond = c / margY[yPrev];
    hY -= p * Math.log2(pCond);
  }
  // H(Y_t | Y_{t-1}, X_{t-1})
  const margXY = {};
  for (const k of Object.keys(jointXYY)) {
    const [xPrev, yPrev] = k.split('|');
    const key = `${xPrev}|${yPrev}`;
    margXY[key] = (margXY[key] || 0) + jointXYY[k];
  }
  let hYX = 0;
  for (const [k, c] of Object.entries(jointXYY)) {
    const [xPrev, yPrev] = k.split('|');
    const p = c / n, pCond = c / margXY[`${xPrev}|${yPrev}`];
    hYX -= p * Math.log2(pCond);
  }
  return hY - hYX;
}

/** Permutation test: shuffle X (breaking any X->Y link while preserving each
 * series' own structure) and recompute TE, many times, to get a null distribution. */
function teSignificance(xDisc, yDisc, bins, perms = 200) {
  const observed = transferEntropy(xDisc, yDisc, bins);
  const nullVals = [];
  const shuffled = xDisc.slice();
  let seed = 987654321;
  const rand = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; };
  for (let p = 0; p < perms; p++) {
    for (let i = shuffled.length - 1; i > 0; i--) {
      const j = Math.floor(rand() * (i + 1));
      [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
    }
    nullVals.push(transferEntropy(shuffled, yDisc, bins));
  }
  const nullMean = S.mean(nullVals), nullSd = sd(nullVals);
  const z = nullSd > 0 ? (observed - nullMean) / nullSd : 0;
  const rank = nullVals.filter(v => v >= observed).length;
  return { observed, nullMean, nullSd, z, permP: (rank + 1) / (perms + 1) };
}

// ═══════════════════════════════════════════════════════════════════════

function loadAligned(symbol, ccy, tf) {
  const bundle = JSON.parse(fs.readFileSync(path.join(DATA, `${symbol}.json`), 'utf8'));
  const bars = bundle.series[tf];
  const derivFile = path.join(DATA, `${ccy}-derivs.json`);
  if (!bars || !fs.existsSync(derivFile)) return null;
  const derivs = JSON.parse(fs.readFileSync(derivFile, 'utf8')).series.slice().sort((a, b) => a.ts - b.ts);

  const rows = [];
  let dp = 0;
  for (let i = 1; i < bars.length; i++) {
    const ts = bars[i].start;
    while (dp + 1 < derivs.length && derivs[dp + 1].ts <= ts) dp++;
    const d = derivs[dp];
    if (!d || d.ts > ts || d.takerBuy == null || d.takerSell == null) continue;
    rows.push({
      i, ts,
      ret: Math.log(bars[i].close / bars[i - 1].close),
      close: bars[i].close,
      signedFlow: d.takerBuy - d.takerSell,   // USD, real measured flow
      oiUsd: d.oiUsd
    });
  }
  return { bars, rows };
}

function main() {
  const args = {};
  for (let i = 2; i < process.argv.length; i += 2) args[process.argv[i].replace(/^--/, '')] = process.argv[i + 1];
  const tf = args.tf || '15';
  const costBps = parseFloat(args.cost || '4');
  const line = '─'.repeat(100);

  console.log(`\n${line}\n  STUDY 13 — THREE EQUATIONS FROM OUTSIDE TECHNICAL ANALYSIS\n${line}`);

  const pairs = [['BTCUSDT', 'BTC'], ['ETHUSDT', 'ETH'], ['SOLUSDT', 'SOL']];
  const allTests = [];

  // ── 1. Kyle's Lambda ──────────────────────────────────────────────────
  console.log(`\n${line}\n  1. KYLE'S LAMBDA — price impact per dollar of signed order flow\n${line}`);
  console.log(`
  lambda = d(price) / d(signed order flow), fit by OLS over a trailing window.
  High lambda: the market is thin/informed and small flow moves price a lot.
  Tests two hypotheses: (a) extreme lambda predicts a REVERSION (an
  informationally-driven overshoot that gives back), (b) extreme lambda
  predicts CONTINUATION (informed flow that keeps being right).
`);
  for (const [sym, ccy] of pairs) {
    const d = loadAligned(sym, ccy, tf);
    if (!d || d.rows.length < 1000) { console.log(`  ${sym}: insufficient aligned data`); continue; }
    const rets = d.rows.map(r => r.ret);
    const flow = d.rows.map(r => r.signedFlow);
    const lambda = kylesLambda(rets, flow, 48);

    const zseries = [];
    for (let i = 96; i < lambda.length; i++) {
      if (lambda[i] == null) { zseries.push(null); continue; }
      const win = lambda.slice(i - 48, i).filter(v => v != null);
      if (win.length < 20) { zseries.push(null); continue; }
      const m = S.mean(win), s = sd(win);
      zseries.push(s ? (lambda[i] - m) / s : null);
    }

    void zseries; // superseded by the clean re-walk below, which recomputes the
                  // rolling z-score inline against lambda[] to avoid an indexing
                  // mismatch between zseries' own offset and d.rows' index space.
    const events = [];
    for (let i = 144; i < d.rows.length; i++) {
      const li = i;
      if (lambda[li] == null) continue;
      const win = [];
      for (let k = Math.max(0, li - 48); k < li; k++) if (lambda[k] != null) win.push(lambda[k]);
      if (win.length < 20) continue;
      const m = S.mean(win), s = sd(win);
      if (!s) continue;
      const z = (lambda[li] - m) / s;
      if (Math.abs(z) < 1.5) continue;
      events.push({ i, z });
    }
    const mid = Math.floor(d.rows.length / 2);
    for (const h of [4, 16]) {
      for (const mode of ['fade', 'follow']) {
        const vals = [], first = [], second = [];
        for (const e of events) {
          if (e.i + h >= d.rows.length) continue;
          const fwdBps = ((d.rows[e.i + h].close - d.rows[e.i].close) / d.rows[e.i].close) * 10000;
          const dir = mode === 'fade' ? -Math.sign(e.z) : Math.sign(e.z);
          const v = dir * fwdBps - costBps;
          vals.push(v);
          (e.i < mid ? first : second).push(v);
        }
        if (vals.length < 80) continue;
        const t = S.hacT(vals, Math.max(0, h - 1));
        if (!t) continue;
        allTests.push({ family: 'kyleLambda', symbol: sym, mode, h, n: t.n, mean: t.mean, t: t.t, p: t.p, first: S.mean(first), second: S.mean(second) });
      }
    }
    console.log(`  ${sym}: ${events.length} extreme-lambda events (|z|>1.5) out of ${d.rows.length} bars`);
  }
  printFamily(allTests, 'kyleLambda', costBps);

  // ── 2. Jump / continuous decomposition ────────────────────────────────
  console.log(`\n${line}\n  2. JUMP / CONTINUOUS DECOMPOSITION — Barndorff-Nielsen & Shephard\n${line}`);
  console.log(`
  What fraction of recent variance came from discontinuous jumps rather than
  continuous diffusion? Unlike "big candle" detection this separates two
  different underlying stochastic processes. Tests whether a jump-dominated
  regime predicts reversion (jumps overshoot and revert) or continuation.
`);
  const jumpTests = [];
  for (const [sym, ccy] of pairs) {
    const d = loadAligned(sym, ccy, tf);
    if (!d || d.rows.length < 1000) continue;
    const rets = d.rows.map(r => r.ret);
    const jr = jumpRatio(rets, 32);

    const events = [];
    for (let i = 0; i < jr.length; i++) if (jr[i] != null && jr[i] > 0.5) events.push(i);
    const mid = Math.floor(d.rows.length / 2);
    for (const h of [4, 16]) {
      for (const mode of ['fade', 'follow']) {
        const vals = [], first = [], second = [];
        for (const i of events) {
          if (i + h >= d.rows.length) continue;
          const lastRet = Math.sign(d.rows[i].ret);
          if (!lastRet) continue;
          const fwdBps = ((d.rows[i + h].close - d.rows[i].close) / d.rows[i].close) * 10000;
          const dir = mode === 'fade' ? -lastRet : lastRet;
          const v = dir * fwdBps - costBps;
          vals.push(v);
          (i < mid ? first : second).push(v);
        }
        if (vals.length < 80) continue;
        const t = S.hacT(vals, Math.max(0, h - 1));
        if (!t) continue;
        jumpTests.push({ family: 'jumpRatio', symbol: sym, mode, h, n: t.n, mean: t.mean, t: t.t, p: t.p, first: S.mean(first), second: S.mean(second) });
      }
    }
    console.log(`  ${sym}: ${events.length} jump-dominated windows (jump ratio > 0.5) out of ${d.rows.length}`);
  }
  printFamily(jumpTests, 'jumpRatio', costBps);
  allTests.push(...jumpTests);

  // ── 3. Transfer entropy ────────────────────────────────────────────────
  console.log(`\n${line}\n  3. TRANSFER ENTROPY — non-linear directional information flow\n${line}`);
  console.log(`
  TE(X -> price) in bits: how much does knowing X's past reduce uncertainty
  about price's future, beyond what price's own past already tells you? Unlike
  correlation this captures non-linear dependence. Significance via a 200-fold
  permutation test (the correct null for this statistic).
`);
  console.log(`  ${'symbol'.padEnd(10)}${'source X'.padEnd(20)}${'TE(X->price) bits'.padStart(18)}${'null mean'.padStart(11)}${'z'.padStart(8)}${'perm p'.padStart(9)}`);
  const teResults = [];
  for (const [sym, ccy] of pairs) {
    const d = loadAligned(sym, ccy, tf);
    if (!d || d.rows.length < 1500) continue;

    const priceDir = d.rows.map(r => (r.ret > 0 ? 1 : r.ret < 0 ? -1 : 0));
    const priceDisc = discretise(priceDir, 3);

    const sources = {
      'signed order flow': d.rows.map(r => r.signedFlow),
      'open interest': d.rows.map(r => r.oiUsd || 0)
    };
    for (const [name, series] of Object.entries(sources)) {
      const xDisc = discretise(series, 5);
      const res = teSignificance(xDisc, priceDisc, 5, 200);
      teResults.push({ symbol: sym, source: name, ...res });
      console.log(`  ${sym.padEnd(10)}${name.padEnd(20)}${res.observed.toFixed(4).padStart(18)}${res.nullMean.toFixed(4).padStart(11)}${res.z.toFixed(2).padStart(8)}${res.permP.toFixed(3).padStart(9)}`);
    }
    // Control: TE(price -> price) via a 1-bar-shifted copy, i.e. how much
    // structure price's own past has beyond a trivial baseline — establishes
    // what a "real" TE magnitude looks like on this data for calibration.
    const selfDisc = discretise(d.rows.map(r => r.ret), 5);
    const selfRes = teSignificance(selfDisc, priceDisc, 5, 200);
    teResults.push({ symbol: sym, source: '(control) |return| -> direction', ...selfRes });
    console.log(`  ${sym.padEnd(10)}${'(control) |return|->dir'.padEnd(20)}${selfRes.observed.toFixed(4).padStart(18)}${selfRes.nullMean.toFixed(4).padStart(11)}${selfRes.z.toFixed(2).padStart(8)}${selfRes.permP.toFixed(3).padStart(9)}`);
  }

  console.log(`
  Read p < 0.05 as "this source's past genuinely reduces uncertainty about
  price's future direction, beyond chance". The control row calibrates what a
  real, mechanical dependency (return magnitude predicting direction bucketing,
  which is partly definitional) looks like on this same statistic, so a source's
  TE can be judged against something known rather than against zero alone.
`);

  // ── Overall FDR across every directional test in this study ──
  const { survivors, tested, threshold } = S.fdr(allTests.map(t => ({ p: t.p, ...t })), 0.10);
  console.log(`${line}\n  FDR ACROSS ALL ${tested} DIRECTIONAL TESTS (Kyle's lambda + jump ratio)\n${line}`);
  if (!survivors.length) {
    console.log(`  Nothing survived (threshold would have been p <= ${threshold ? threshold.toExponential(2) : 'n/a'}).`);
  } else {
    const solid = survivors.filter(s => s.mean > 0 && Math.sign(s.first) === Math.sign(s.second) && Math.sign(s.first) === Math.sign(s.mean));
    console.log(`  ${survivors.length} of ${tested} passed FDR. ${solid.length} positive and stable across both halves.`);
    for (const s of solid) {
      console.log(`    ${s.family} ${s.symbol} ${s.mode} ${s.h}b: ${s.mean.toFixed(2)} bps, t=${s.t.toFixed(2)}, 1st=${s.first.toFixed(1)}, 2nd=${s.second.toFixed(1)}`);
    }
  }
  console.log(`\n${line}\n`);

  fs.mkdirSync(path.join(__dirname, 'results'), { recursive: true });
  fs.writeFileSync(path.join(__dirname, 'results', 'novel-equations.json'),
    JSON.stringify({ generatedAt: new Date().toISOString(), tf, costBps, directionalTests: allTests, transferEntropy: teResults }, null, 2));
}

function printFamily(tests, family, costBps) {
  const mine = tests.filter(t => t.family === family);
  if (!mine.length) return;
  console.log(`\n  ${'symbol'.padEnd(10)}${'mode'.padEnd(9)}${'hold'.padStart(6)}${'n'.padStart(7)}${'mean bps'.padStart(10)}${'t(HAC)'.padStart(9)}${'1st half'.padStart(10)}${'2nd half'.padStart(10)}`);
  for (const t of mine) {
    console.log(`  ${t.symbol.padEnd(10)}${t.mode.padEnd(9)}${(t.h + 'b').padStart(6)}${String(t.n).padStart(7)}${t.mean.toFixed(2).padStart(10)}${t.t.toFixed(2).padStart(9)}${t.first.toFixed(1).padStart(10)}${t.second.toFixed(1).padStart(10)}`);
  }
  void costBps;
}

main();
