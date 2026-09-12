#!/usr/bin/env node
/**
 * STUDY 15 — Re-testing an externally supplied "advanced formula" report.
 *
 * A user uploaded a research summary reporting 192 tests of 16 tanh-wrapped
 * formulas (F1-F8 on price/volume/volatility, A-H adding derivatives) against
 * a barrier-race event: does price hit +0.5% before -0.5% within a fixed
 * horizon (2/4/8/16 bars on 15m data)? Best full-sample AUCs were 0.52-0.58;
 * best "top/bottom decile" tail win rates ran up to 0.79 on samples as small
 * as n=29.
 *
 * The source file is honest about its own gaps — its own "NEXT VALIDATION
 * REQUIRED" section lists multiple-testing correction, walk-forward
 * validation, and cost accounting as still needed. This script does exactly
 * that: implements the 16 formulas verbatim from the file's definitions, and
 * re-tests every one of them with the same four gates used throughout this
 * project, plus two checks specific to this report's own methodology:
 *
 *   1. CAUSAL (WALK-FORWARD) DECILE THRESHOLDS. "Top 10% / bottom 10%" must be
 *      computed from an EXPANDING window of only past data at each point, or
 *      the threshold itself leaks the future. Re-implemented that way here;
 *      it is not clear from the source file whether the original did.
 *   2. BLOCK-BOOTSTRAP SIGNIFICANCE. A barrier-race outcome sampled every bar
 *      over a fixed horizon is heavily overlapping — adjacent trials share
 *      almost their entire horizon. A plain binomial test on win rate (which
 *      the source file implicitly invites by just reporting a percentage)
 *      would be exactly the same "naive standard error" mistake this project
 *      already caught and corrected once (Part II, 18 phantom survivors).
 *      Moving-block bootstrap (block length = 3x horizon) is used instead.
 *
 * Then: FDR across everything tested, split-sample validation, an effective-
 * rank check on the formula sets (do 8 "different" formulas measure 8
 * different things, or 2?), and — the one genuinely different structural
 * question this report raises — a cost-adjusted expectancy check, because a
 * SYMMETRIC 1:1 barrier (0.5% target, 0.5% stop) is a far more cost-forgiving
 * setup than the asymmetric hold-to-close trades tested everywhere else in
 * this project.
 */
const fs = require('fs');
const path = require('path');
const S = require('./lib-stats.js');

const DATA = path.join(__dirname, '..', 'backtest', 'data');
const RESEARCH = path.join(__dirname, 'data');
const PRICE_COINS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'DOGEUSDT'];
const DERIV_COINS = [['BTCUSDT', 'BTC'], ['ETHUSDT', 'ETH'], ['SOLUSDT', 'SOL']];
const HORIZONS = [2, 4, 8, 16];   // bars on 15m: 30min, 1h, 2h, 4h — matches the source file

function mean(a) { return a.length ? a.reduce((x, y) => x + y, 0) / a.length : 0; }
function sd(a) {
  if (a.length < 2) return 0;
  const m = mean(a);
  return Math.sqrt(a.reduce((x, y) => x + (y - m) ** 2, 0) / (a.length - 1));
}
function median(a) { const s = a.slice().sort((x, y) => x - y); return s[Math.floor(s.length / 2)]; }
function mad(a) { const m = median(a); return median(a.map(v => Math.abs(v - m))) * 1.4826; }
function tanh(x) { return Math.tanh(x); }

// ─── Base variables, exactly as defined in the source file ───
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
      const vm = median(vwin), vs = mad(vwin);
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

function buildDerivSeries(bars, derivRows) {
  const sorted = derivRows.slice().sort((a, b) => a.ts - b.ts);
  let dp = 0;
  const out = { ofi: [], oi: [], fund: [], ls: [], dv: [], avail: [] };
  for (let i = 0; i < bars.length; i++) {
    const ts = bars[i].start;
    while (dp + 1 < sorted.length && sorted[dp + 1].ts <= ts) dp++;
    const d = sorted[dp];
    if (!d || d.ts > ts || dp < 30 || d.fundingRate == null || d.longShortRatio == null || d.takerBuy == null) {
      out.ofi.push(null); out.oi.push(null); out.fund.push(null); out.ls.push(null); out.dv.push(null); out.avail.push(false);
      continue;
    }
    const win = sorted.slice(Math.max(0, dp - 120), dp + 1);
    const zOf = (arr, v) => { const s = sd(arr); const m = mean(arr); return s ? (v - m) / s : 0; };

    out.ofi.push(zOf(win.map(r => (r.takerBuy - r.takerSell) / Math.max(1, r.takerBuy + r.takerSell)), (d.takerBuy - d.takerSell) / Math.max(1, d.takerBuy + d.takerSell)));
    const oiArr = win.map(r => r.oiUsd).filter(v => v != null);
    out.oi.push(oiArr.length > 5 ? zOf(oiArr, d.oiUsd) : 0);
    const fundArr = win.map(r => r.fundingRate).filter(v => v != null);
    out.fund.push(fundArr.length > 5 ? zOf(fundArr, d.fundingRate) : 0);
    const lsArr = win.map(r => r.longShortRatio).filter(v => v != null);
    out.ls.push(lsArr.length > 5 ? zOf(lsArr, d.longShortRatio) : 0);
    const dvArr = win.map(r => r.volUsd).filter(v => v != null);
    out.dv.push(d.volUsd != null && dvArr.length > 5 ? zOf(dvArr, d.volUsd) : 0);
    out.avail.push(true);
  }
  return out;
}

// ─── Formula definitions, verbatim from the source file ───
const PRICE_FORMULAS = {
  F1_NonlinearPressure: b => tanh((2 * b.m4 + 1.2 * b.m16 + 0.7 * b.m32) * (1 + 0.8 * b.vz) / (b.vr + 0.25)),
  F2_RangeEnergy: b => tanh((1 - b.pos ** 2) * (b.m4 + 0.6 * b.accel) * (1 + Math.abs(b.vz))) - 0.35 * b.pos,
  F3_TrendEfficiency: b => tanh((b.m16 + 0.7 * b.m32) * (0.4 + 1.8 * b.eff) * (1 + 0.5 * b.vz)),
  F4_CurvatureShock: b => tanh((b.curv + 0.8 * b.accel) * (1 + Math.abs(b.vz)) * Math.sqrt(Math.max(b.vr, 0.05))),
  F5_MeanReversion: b => tanh(-b.dev * (1 + 0.7 * Math.abs(b.vz)) / (0.7 + b.vr) + 0.25 * b.m4),
  F6_Interaction: b => tanh((b.m4 * (1 + 1.5 * b.pos) + b.m16 * (1 - b.pos) + 0.5 * b.m32) * (1 + b.vz) / (0.5 + b.vr)),
  F7_Asymmetry: b => tanh((b.m4 + 0.5 * b.m16) * (1 + 0.8 * b.vz) + 0.8 * b.body - 0.4 * b.wick),
  F8_MultiScale: b => tanh((b.m4 + 0.8 * b.m16 + 0.5 * b.m32 + 0.25 * b.m64) * (1 + 0.6 * b.eff) * (1 + 0.5 * Math.abs(b.vz)))
};
const PRICE_REQS = {
  F1_NonlinearPressure: ['m4', 'm16', 'm32', 'vz', 'vr'], F2_RangeEnergy: ['pos', 'm4', 'accel', 'vz'],
  F3_TrendEfficiency: ['m16', 'm32', 'eff', 'vz'], F4_CurvatureShock: ['curv', 'accel', 'vz', 'vr'],
  F5_MeanReversion: ['dev', 'vz', 'vr', 'm4'], F6_Interaction: ['m4', 'pos', 'm16', 'm32', 'vz', 'vr'],
  F7_Asymmetry: ['m4', 'm16', 'vz', 'body', 'wick'], F8_MultiScale: ['m4', 'm16', 'm32', 'm64', 'eff', 'vz']
};

const DERIV_FORMULAS = {
  A_ConvexFlow: (b, d) => tanh(((b.m4 + 0.7 * b.m16) * (1 + 0.7 * d.ofi) * (1 + 0.5 * d.oi)) / (0.5 + b.vr)),
  B_OI_FundingSqueeze: (b, d) => tanh((d.oi * d.ofi - 0.8 * d.fund * d.oi) * (1 + Math.abs(b.vz))),
  C_LiquidityCascade: (b, d) => tanh((d.ofi * d.oi) * (1 + Math.abs(d.fund) + Math.abs(d.ls)) * (1 + 0.5 * d.dv)),
  D_RegimeWeighted: (b, d) => tanh((b.m16 + 0.5 * b.m32) * (0.5 + b.eff) * (1 + 0.8 * d.ofi) * (1 + 0.4 * d.oi)),
  E_CrowdingReversal: (b, d) => tanh(-d.ls * d.fund * (1 + Math.abs(d.oi)) + 0.35 * (-b.dev)),
  F_CrossPressure: (b, d) => tanh((b.m4 + 0.5 * b.m16) + 0.8 * d.ofi + 0.5 * d.oi - 0.4 * d.fund + 0.3 * d.dv),
  G_Reflexivity: (b, d) => tanh((b.m4 * (1 + d.ofi)) * (1 + d.oi * d.ofi) * (1 + 0.6 * Math.abs(b.vz))),
  H_EntropyShock: (b, d) => tanh((d.ofi * d.oi) * (1 + b.vr) * (1 + Math.abs(d.fund)) + 0.4 * (d.dv - b.vz))
};

// ─── Barrier-race event: +0.5% before -0.5% within `horizon` bars ───
function barrierOutcome(bars, i, horizon, barrier = 0.005) {
  const entry = bars[i].close;
  const up = entry * (1 + barrier), dn = entry * (1 - barrier);
  for (let k = i + 1; k <= Math.min(i + horizon, bars.length - 1); k++) {
    const hitUp = bars[k].high >= up, hitDn = bars[k].low <= dn;
    if (hitUp && hitDn) return null;   // ambiguous within-bar — excluded, matching the source file
    if (hitUp) return 1;
    if (hitDn) return 0;
  }
  return null;   // neither hit within horizon — ambiguous, excluded
}

/** Rank-based AUC (Mann-Whitney U form): P(score of a random positive > score
 * of a random negative), with ties split. */
function computeAUC(scores, labels) {
  const pos = [], neg = [];
  for (let i = 0; i < scores.length; i++) (labels[i] ? pos : neg).push(scores[i]);
  if (!pos.length || !neg.length) return null;
  const all = scores.map((s, i) => ({ s, l: labels[i] })).sort((a, b) => a.s - b.s);
  let rankSum = 0, i = 0;
  while (i < all.length) {
    let j = i;
    while (j < all.length && all[j].s === all[i].s) j++;
    const avgRank = (i + 1 + j) / 2;
    for (let k = i; k < j; k++) if (all[k].l) rankSum += avgRank;
    i = j;
  }
  const nPos = pos.length, nNeg = neg.length;
  return (rankSum - nPos * (nPos + 1) / 2) / (nPos * nNeg);
}

/** Moving-block bootstrap significance for a win-rate-vs-0.5 test, respecting
 * the horizon-length overlap between adjacent trials. */
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
    // Centre the resample at 0.5 to build the null, then compare |stat| to |observed|.
    const stat = mean(resample) - mean(outcomes);   // deviation under resampling of THIS series
    if (Math.abs(stat) >= Math.abs(observed)) extreme++;
  }
  return (extreme + 1) / (iters + 1);
}

/** Causal (expanding-window) decile threshold: at bar i, the cutoff for "top
 * 10%" uses only scores from bars seen so far, never future ones.
 *
 * Re-sorting the full "seen so far" array at every single bar is O(n^2 log n)
 * over a 40,000-bar series -- that made the first run of this script take
 * over an hour and never finish. The threshold is instead refreshed only
 * every REFRESH bars (still using only past data at the moment it's
 * computed, so it stays causal) and reused for the bars in between -- a
 * standard walk-forward approximation, not a look-ahead shortcut. */
function causalTopBottomMask(scores, frac, minHistory) {
  const n = scores.length;
  const topMask = new Array(n).fill(false);
  const botMask = new Array(n).fill(false);
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
      if (scores[i] >= hi) topMask[i] = true;
      if (scores[i] <= lo) botMask[i] = true;
    }
    if (scores[i] != null) { seen.push(scores[i]); sinceRefresh++; }
  }
  return { topMask, botMask };
}

function evaluateFormula(bars, scores, horizon, costBpsForBarrier) {
  const n = bars.length;
  const outcomes = new Array(n).fill(null);
  for (let i = 0; i < n - horizon; i++) if (scores[i] != null) outcomes[i] = barrierOutcome(bars, i, horizon);

  const validIdx = [];
  for (let i = 0; i < n; i++) if (scores[i] != null && outcomes[i] != null) validIdx.push(i);
  if (validIdx.length < 300) return null;

  const allScores = validIdx.map(i => scores[i]);
  const allLabels = validIdx.map(i => outcomes[i]);
  const auc = computeAUC(allScores, allLabels);

  const { topMask, botMask } = causalTopBottomMask(scores, 0.10, 200);
  const topIdx = validIdx.filter(i => topMask[i]);
  const botIdx = validIdx.filter(i => botMask[i]);

  const topWR = topIdx.length >= 30 ? mean(topIdx.map(i => outcomes[i])) : null;       // long tail: want outcome=1
  const botWR = botIdx.length >= 30 ? 1 - mean(botIdx.map(i => outcomes[i])) : null;   // short tail: want outcome=0

  // Position-in-validIdx lookup done once (O(n)), not via .indexOf() inside a
  // filter (which was O(n) per element -- O(n^2) overall on a ~40k-bar series).
  const posInValid = new Map();
  validIdx.forEach((idx, pos) => posInValid.set(idx, pos));
  const mid = Math.floor(validIdx.length / 2);
  const topFirst = topIdx.filter(i => posInValid.get(i) < mid).map(i => outcomes[i]);
  const topSecond = topIdx.filter(i => posInValid.get(i) >= mid).map(i => outcomes[i]);
  // Short tail wants outcome=0, so its "hit rate" for split-half purposes is
  // 1-mean, same transform as botWR itself -- computed separately from the
  // top tail's split (they are different bars, not interchangeable).
  const botFirstRaw = botIdx.filter(i => posInValid.get(i) < mid).map(i => outcomes[i]);
  const botSecondRaw = botIdx.filter(i => posInValid.get(i) >= mid).map(i => outcomes[i]);

  const blockLen = Math.max(8, horizon * 3);
  const topP = topIdx.length >= 30 ? blockBootstrapP(topIdx.map(i => outcomes[i]), blockLen) : null;
  const botP = botIdx.length >= 30 ? blockBootstrapP(botIdx.map(i => outcomes[i]).map(v => 1 - v), blockLen) : null;

  // Cost-adjusted expectancy: symmetric 1:1 barrier (0.5% target/stop). At win
  // rate p, expectancy in R = 2p - 1; converting the round-trip cost into R
  // means dividing bps cost by the 50bps barrier width.
  const costR = costBpsForBarrier / 50;
  const topExpR = topWR != null ? (2 * topWR - 1) - costR : null;
  const botExpR = botWR != null ? (2 * botWR - 1) - costR : null;

  return {
    n: validIdx.length, auc,
    topN: topIdx.length, topWR, topP, topExpR,
    topFirst: topFirst.length >= 10 ? mean(topFirst) : null, topSecond: topSecond.length >= 10 ? mean(topSecond) : null,
    botN: botIdx.length, botWR, botP, botExpR,
    botFirst: botFirstRaw.length >= 10 ? 1 - mean(botFirstRaw) : null, botSecond: botSecondRaw.length >= 10 ? 1 - mean(botSecondRaw) : null
  };
}

// ═══════════════════════════════════════════════════════════════════════
function main() {
  const args = {};
  for (let i = 2; i < process.argv.length; i += 2) args[process.argv[i].replace(/^--/, '')] = process.argv[i + 1];
  const costBps = parseFloat(args.cost || '4');
  const line = '─'.repeat(108);

  console.log(`\n${line}\n  STUDY 15 — RE-TESTING THE UPLOADED "ADVANCED FORMULA" REPORT\n${line}`);
  console.log(`
  Re-implements all 16 formulas verbatim, tests them with: causal (walk-forward)
  decile thresholds instead of a possibly-leaking fixed cutoff, moving-block
  bootstrap significance (correct for the heavy overlap in a horizon-race
  outcome), FDR across everything, split-sample validation, an effective-rank
  check on the two formula families, and cost-adjusted expectancy at ${costBps} bps.
`);

  // ── Part 1: price formulas ──
  console.log(`${line}\n  PART 1 — PRICE/VOLUME/VOLATILITY FORMULAS (F1-F8), 4 coins x 4 horizons\n${line}`);
  const priceTests = [];
  const priceScoreCache = {};   // for effective-rank check

  for (const coin of PRICE_COINS) {
    const bars = JSON.parse(fs.readFileSync(path.join(DATA, `${coin}.json`), 'utf8')).series['15'];
    const b = buildBaseSeries(bars);
    priceScoreCache[coin] = {};

    for (const [name, fn] of Object.entries(PRICE_FORMULAS)) {
      const reqs = PRICE_REQS[name];
      const scores = new Array(bars.length).fill(null);
      for (let i = 0; i < bars.length; i++) {
        if (!reqs.every(r => b[r][i] != null)) continue;
        const row = {}; for (const r of reqs) row[r] = b[r][i];
        const v = fn(row);
        if (isFinite(v)) scores[i] = v;
      }
      priceScoreCache[coin][name] = scores;

      for (const h of HORIZONS) {
        const res = evaluateFormula(bars, scores, h, costBps);
        if (!res) continue;
        priceTests.push({ family: 'price', coin, formula: name, horizon: h, ...res });
      }
    }
    process.stdout.write(`\r  ${coin} done`);
  }
  console.log('');

  // Effective rank of the 8 price formulas (on BTC, the deepest series).
  const btcCols = Object.keys(PRICE_FORMULAS).map(k => priceScoreCache['BTCUSDT'][k].filter(v => v != null));
  const minLen = Math.min(...btcCols.map(c => c.length));
  const aligned = btcCols.map(c => c.slice(-minLen));
  const rankInfo = effectiveRank(aligned);
  console.log(`\n  Effective rank of the 8 price formulas (BTC): ${rankInfo.rank.toFixed(2)} out of 8`);
  console.log(`  (mirrors the same check Part VI ran on RSI/ROC/Bollinger/etc. — these are tanh-wrapped`);
  console.log(`  combinations of the same underlying momentum/volatility terms, so a low effective rank`);
  console.log(`  here would mean the same conclusion applies: 8 formulas, far fewer independent measurements.)`);

  reportTop(priceTests, 'PRICE', costBps);

  // ── Part 2: derivatives formulas ──
  console.log(`\n${line}\n  PART 2 — DERIVATIVES-ENHANCED FORMULAS (A-H), 3 coins x 4 horizons\n${line}`);
  console.log(`  (bounded to ~30 days by the derivatives feed, same limit noted throughout this project)\n`);
  const derivTests = [];
  const derivScoreCache = {};

  for (const [symbol, ccy] of DERIV_COINS) {
    const bars = JSON.parse(fs.readFileSync(path.join(DATA, `${symbol}.json`), 'utf8')).series['15'];
    const derivRows = JSON.parse(fs.readFileSync(path.join(DATA, `${ccy}-derivs.json`), 'utf8')).series;
    const b = buildBaseSeries(bars);
    const d = buildDerivSeries(bars, derivRows);
    derivScoreCache[symbol] = {};

    for (const [name, fn] of Object.entries(DERIV_FORMULAS)) {
      const scores = new Array(bars.length).fill(null);
      for (let i = 0; i < bars.length; i++) {
        if (!d.avail[i]) continue;
        if (b.m4[i] == null || b.m16[i] == null || b.m32[i] == null || b.vz[i] == null || b.vr[i] == null || b.eff[i] == null || b.dev[i] == null) continue;
        const bRow = { m4: b.m4[i], m16: b.m16[i], m32: b.m32[i], vz: b.vz[i], vr: b.vr[i], eff: b.eff[i], dev: b.dev[i] };
        const dRow = { ofi: d.ofi[i], oi: d.oi[i], fund: d.fund[i], ls: d.ls[i], dv: d.dv[i] };
        const v = fn(bRow, dRow);
        if (isFinite(v)) scores[i] = v;
      }
      derivScoreCache[symbol][name] = scores;
      for (const h of HORIZONS) {
        const res = evaluateFormula(bars, scores, h, costBps);
        if (!res) continue;
        derivTests.push({ family: 'deriv', coin: symbol, formula: name, horizon: h, ...res });
      }
    }
  }

  const btcDCols = Object.keys(DERIV_FORMULAS).map(k => derivScoreCache['BTCUSDT'][k].filter(v => v != null));
  const minLenD = Math.min(...btcDCols.map(c => c.length));
  const alignedD = btcDCols.map(c => c.slice(-minLenD));
  const rankInfoD = effectiveRank(alignedD);
  console.log(`  Effective rank of the 8 derivatives formulas (BTC): ${rankInfoD.rank.toFixed(2)} out of 8\n`);

  reportTop(derivTests, 'DERIVATIVES', costBps);

  // ── Combined FDR across everything ──
  const combined = [...priceTests, ...derivTests];
  const topTests = combined.filter(t => t.topP != null).map(t => ({ ...t, p: t.topP }));
  const botTests = combined.filter(t => t.botP != null).map(t => ({ ...t, p: t.botP }));
  const allDirTests = [...topTests, ...botTests];
  const { survivors, tested, threshold } = S.fdr(allDirTests, 0.10);

  console.log(`\n${line}\n  FDR ACROSS ALL ${tested} DECILE TESTS (both formula families, both tails, all coins/horizons)\n${line}`);
  if (!survivors.length) {
    console.log(`  Nothing survived.`);
  } else {
    const solid = survivors.filter(s => {
      const isTop = s.p === s.topP;
      const wr = isTop ? s.topWR : s.botWR;
      const expR = isTop ? s.topExpR : s.botExpR;
      const first = isTop ? s.topFirst : s.botFirst;
      const second = isTop ? s.topSecond : s.botSecond;
      const stable = first != null && second != null ? Math.sign(first - 0.5) === Math.sign(second - 0.5) : true;
      return wr > 0.5 && expR > 0 && stable;
    });
    console.log(`  ${survivors.length} of ${tested} passed FDR (p <= ${threshold.toExponential(2)}).`);
    console.log(`  ${solid.length} also have positive cost-adjusted expectancy and (where checkable) stable split-sample sign.\n`);
    if (solid.length) {
      console.log(`  ${'family'.padEnd(9)}${'coin'.padEnd(10)}${'formula'.padEnd(20)}${'h'.padStart(4)}${'tail'.padStart(6)}${'n'.padStart(6)}${'WR'.padStart(7)}${'expR@cost'.padStart(11)}${'p'.padStart(10)}${'split'.padStart(12)}`);
      for (const s of solid) {
        const isTop = s.p === s.topP;
        const first = isTop ? s.topFirst : s.botFirst, second = isTop ? s.topSecond : s.botSecond;
        const split = (first != null && second != null) ? `${(first*100).toFixed(0)}%/${(second*100).toFixed(0)}%` : '—';
        console.log(`  ${s.family.padEnd(9)}${s.coin.padEnd(10)}${s.formula.padEnd(20)}${String(s.horizon).padStart(4)}${(isTop ? 'long' : 'short').padStart(6)}${String(isTop ? s.topN : s.botN).padStart(6)}${((isTop ? s.topWR : s.botWR) * 100).toFixed(1).padStart(6)}%${(isTop ? s.topExpR : s.botExpR).toFixed(3).padStart(11)}${s.p.toExponential(1).padStart(10)}${split.padStart(12)}`);
      }
    }
  }

  console.log(`\n${line}\n`);
  fs.mkdirSync(path.join(__dirname, 'results'), { recursive: true });
  fs.writeFileSync(path.join(__dirname, 'results', 'external-formulas.json'),
    JSON.stringify({ generatedAt: new Date().toISOString(), costBps, priceRank: rankInfo.rank, derivRank: rankInfoD.rank, priceTests, derivTests }, null, 2));
}

function reportTop(tests, label, costBps) {
  const byTop = tests.filter(t => t.topWR != null).sort((a, b) => b.topWR - a.topWR).slice(0, 8);
  const byBot = tests.filter(t => t.botWR != null).sort((a, b) => b.botWR - a.botWR).slice(0, 8);
  console.log(`\n  ${label} — top 8 by LONG-tail win rate (causal decile, block-bootstrap p, cost-adjusted expectancy @ ${costBps}bps)`);
  console.log(`  ${'coin'.padEnd(10)}${'formula'.padEnd(20)}${'h'.padStart(4)}${'n'.padStart(6)}${'WR'.padStart(8)}${'p(boot)'.padStart(10)}${'expR'.padStart(9)}${'1st'.padStart(8)}${'2nd'.padStart(8)}`);
  for (const t of byTop) {
    console.log(`  ${t.coin.padEnd(10)}${t.formula.padEnd(20)}${String(t.horizon).padStart(4)}${String(t.topN).padStart(6)}${(t.topWR * 100).toFixed(1).padStart(7)}%${(t.topP != null ? t.topP.toFixed(3) : '—').padStart(10)}${t.topExpR.toFixed(3).padStart(9)}${(t.topFirst != null ? (t.topFirst * 100).toFixed(0) + '%' : '—').padStart(8)}${(t.topSecond != null ? (t.topSecond * 100).toFixed(0) + '%' : '—').padStart(8)}`);
  }
  console.log(`\n  ${label} — top 8 by SHORT-tail win rate`);
  console.log(`  ${'coin'.padEnd(10)}${'formula'.padEnd(20)}${'h'.padStart(4)}${'n'.padStart(6)}${'WR'.padStart(8)}${'p(boot)'.padStart(10)}${'expR'.padStart(9)}`);
  for (const t of byBot) {
    console.log(`  ${t.coin.padEnd(10)}${t.formula.padEnd(20)}${String(t.horizon).padStart(4)}${String(t.botN).padStart(6)}${(t.botWR * 100).toFixed(1).padStart(7)}%${(t.botP != null ? t.botP.toFixed(3) : '—').padStart(10)}${t.botExpR.toFixed(3).padStart(9)}`);
  }
}

/** Effective rank via participation ratio (same method as Part VI). */
function effectiveRank(matrix) {
  const k = matrix.length;
  const C = [];
  for (let i = 0; i < k; i++) {
    C.push([]);
    for (let j = 0; j < k; j++) C[i][j] = i === j ? 1 : (S.corr(matrix[i], matrix[j]) || 0);
  }
  const A = C.map(r => r.slice());
  for (let sweep = 0; sweep < 100; sweep++) {
    let off = 0;
    for (let i = 0; i < k; i++) for (let j = i + 1; j < k; j++) off += A[i][j] * A[i][j];
    if (off < 1e-12) break;
    for (let p = 0; p < k; p++) for (let q = p + 1; q < k; q++) {
      if (Math.abs(A[p][q]) < 1e-14) continue;
      const theta = (A[q][q] - A[p][p]) / (2 * A[p][q]);
      const t = Math.sign(theta || 1) / (Math.abs(theta) + Math.sqrt(theta * theta + 1));
      const c = 1 / Math.sqrt(t * t + 1), s = t * c;
      for (let i = 0; i < k; i++) { const aip = A[i][p], aiq = A[i][q]; A[i][p] = c * aip - s * aiq; A[i][q] = s * aip + c * aiq; }
      for (let i = 0; i < k; i++) { const api = A[p][i], aqi = A[q][i]; A[p][i] = c * api - s * aqi; A[q][i] = s * api + c * aqi; }
    }
  }
  const eig = []; for (let i = 0; i < k; i++) eig.push(Math.max(0, A[i][i]));
  const sum = eig.reduce((a, b) => a + b, 0), sumSq = eig.reduce((a, b) => a + b * b, 0);
  return { rank: sumSq > 0 ? (sum * sum) / sumSq : 0 };
}

main();
