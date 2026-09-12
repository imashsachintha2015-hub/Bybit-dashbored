#!/usr/bin/env node
/**
 * STUDY 23 — CME-X4 FORENSIC RECOVERY STUDY.
 *
 * Not another formula. The user's own framing, verbatim: "What measurable
 * market state causes the small predictive edge observed in CME-X, and
 * under what conditions does that state cease to exist?"
 *
 * The exact original CME-Ultimate/CME-X equation from Study 20
 * (cme-invariant-test.js) is reused HERE VERBATIM -- same buildAssetSeries,
 * buildCrossAsset, buildTemporal, FEATURE_NAMES, and the same Experiment-B
 * training setup (fit on DOGE+LINK+AVAX, FIT_HORIZON=4) that produced the
 * strongest reported edge (BTC h=2: AUC 0.557, WR 62.6%, +0.172R). That
 * fitted model -- weights, mu, sigma -- is FROZEN ONCE and never refit
 * again in this file except where a branch explicitly says "refit" (the
 * single-asset-only branches, which are a data ablation, not a formula
 * change).
 *
 * Every other branch below perturbs either the INPUT to that frozen model
 * or the POPULATION it's evaluated on, and reports what happens:
 *
 *   REMOVAL (does this feature's real-time value matter to the frozen
 *   model's output?): substitute the feature with its training-set mean
 *   (neutralising its z-scored contribution to ~0) at prediction time.
 *     - remove momentum      (M_COH -> its training mean)
 *     - remove volume        (ABSD -> its training mean)
 *     - remove volatility    (TREND_GATE -> its training mean)
 *     - remove nonlinear interaction (M_COH -> raw M, i.e. drop the
 *       *(0.5+0.5*COH)*TREND_GATE modulation, same frozen weight/scale)
 *
 *   SHUFFLE (does this feature's TEMPORAL ALIGNMENT matter, not just its
 *   marginal distribution?): deterministically shuffle that one feature's
 *   values across the test bars, every one of the 15 named features in
 *   turn, plus the label-shuffle placebo (shuffle the OUTCOMES instead).
 *
 *   SHIFT (does the edge require fresh features, and is there any sign of
 *   look-ahead contamination?): re-evaluate using feature values taken
 *   from 2 bars before or 2 bars after the actual decision bar, while the
 *   outcome stays anchored to the real decision bar.
 *
 *   SINGLE-ASSET (does the phenomenon appear without ANY cross-asset
 *   pooling?): refit -- the one place this file refits -- the identical
 *   formula on each coin's own history alone (60/40 time split, same as
 *   Study 20's Experiment C), reported for all 6 coins.
 *
 *   REGIME (where specifically does the frozen model's edge live?):
 *   partition the test bars by momentum direction, volatility level, and
 *   volume level -- each split against a threshold fixed from the
 *   TRAINING population only, never the test data -- and report AUC/WR
 *   inside each half separately.
 */
const fs = require('fs');
const path = require('path');
const S = require('./lib-stats.js');

const DATA = path.join(__dirname, '..', 'backtest', 'data');
const COINS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'DOGEUSDT', 'LINKUSDT', 'AVAXUSDT'];
const TEST_COINS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT'];     // where the original edge showed up
const TRAIN_COINS = ['DOGEUSDT', 'LINKUSDT', 'AVAXUSDT']; // Experiment B's training set, frozen
const K = [1, 2, 4, 8, 16, 32, 64, 128];
const FIT_HORIZON = 4;
const EVAL_HORIZON = 2; // 30min -- where Study 20 found its strongest signal
const BARRIER = 0.005;
const RIDGE_LAMBDA = 5.0;
const COST_BPS_DEFAULT = 4;

function mean(a) { return a.length ? a.reduce((x, y) => x + y, 0) / a.length : 0; }
function sd(a) { if (a.length < 2) return 0; const m = mean(a); return Math.sqrt(a.reduce((x, y) => x + (y - m) ** 2, 0) / (a.length - 1)); }
function median(a) { const s = a.slice().sort((x, y) => x - y); return s[Math.floor(s.length / 2)]; }
function mad(a) { const m = median(a); return median(a.map(v => Math.abs(v - m))) * 1.4826; }
function tanh(x) { return Math.tanh(x); }
function sigmoid(x) { return 1 / (1 + Math.exp(-x)); }
function clamp(x, lo, hi) { return Math.max(lo, Math.min(hi, x)); }

function loadBars(symbol) {
  const f = path.join(DATA, `${symbol}.json`);
  if (!fs.existsSync(f)) throw new Error(`missing ${f} -- run: node backtest/fetch-klines.js ${symbol} 15 40000`);
  return JSON.parse(fs.readFileSync(f, 'utf8')).series['15'];
}
function alignAll(barsBySymbol) {
  const symbols = Object.keys(barsBySymbol);
  const maps = symbols.map(s => { const m = new Map(); for (const b of barsBySymbol[s]) m.set(b.start, b); return m; });
  const common = [...maps[0].keys()].filter(ts => maps.every(m => m.has(ts))).sort((a, b) => a - b);
  const out = {};
  symbols.forEach((s, si) => { out[s] = common.map(ts => maps[si].get(ts)); });
  return { timestamps: common, bars: out };
}

// ═══════════════════════════════════════════════════════════════════════
// THE FROZEN FORMULA'S INGREDIENTS -- verbatim from research/cme-invariant-test.js
// (Study 20). Not one line of the construction below is modified.
// ═══════════════════════════════════════════════════════════════════════
function buildAssetSeries(bars) {
  const n = bars.length;
  const closes = bars.map(c => c.close);
  const logVol = bars.map(c => Math.log(Math.max(c.volume, 1e-9)));
  const rets1 = new Array(n).fill(null);
  for (let i = 1; i < n; i++) rets1[i] = Math.log(closes[i] / closes[i - 1]);

  const out = {
    M: new Array(n).fill(null), COH: new Array(n).fill(null),
    VR: new Array(n).fill(null), VA: new Array(n).fill(null),
    EFF: new Array(n).fill(null), TREND_GATE: new Array(n).fill(null),
    VZ: new Array(n).fill(null), DISP: new Array(n).fill(null),
    ABS: new Array(n).fill(null), ABSD: new Array(n).fill(null),
    LIQ: new Array(n).fill(null), RANGE: new Array(n).fill(null)
  };

  const sigmaWin = 200;
  const rk = {}; for (const k of K) rk[k] = new Array(n).fill(null);
  for (const k of K) for (let i = k; i < n; i++) rk[k][i] = Math.log(closes[i] / closes[i - k]);

  for (let i = 0; i < n; i++) {
    if (i >= 128 + sigmaWin) {
      let sumMR = 0, sumSign = 0, cnt = 0;
      for (const k of K) {
        const r = rk[k][i];
        if (r == null) continue;
        const hist = [];
        for (let j = i - sigmaWin; j < i; j++) if (rk[k][j] != null) hist.push(rk[k][j]);
        const s = sd(hist) || 1e-9;
        sumMR += tanh(r / s);
        sumSign += Math.sign(r);
        cnt++;
      }
      if (cnt === K.length) { out.M[i] = sumMR / cnt; out.COH[i] = Math.abs(sumSign / cnt); }
    }

    if (i >= 128) {
      const volShort = sd(rets1.slice(i - 15, i + 1).filter(v => v != null));
      const volLong = sd(rets1.slice(i - 127, i + 1).filter(v => v != null));
      out.VR[i] = volLong ? volShort / volLong : null;
    }
    if (i >= 128 + 16 && out.VR[i] != null && out.VR[i - 16] != null) {
      const histVR = [];
      for (let j = i - 200; j < i; j++) if (out.VR[j] != null && out.VR[j - 16] != null) histVR.push(out.VR[j] - out.VR[j - 16]);
      const s = sd(histVR) || 1e-9;
      out.VA[i] = (out.VR[i] - out.VR[i - 16]) / s;
    }
    if (i >= 16) {
      const w = rets1.slice(i - 15, i + 1).filter(v => v != null);
      const net = Math.abs(w.reduce((a, b) => a + b, 0));
      const pathLen = w.reduce((a, b) => a + Math.abs(b), 0);
      out.EFF[i] = pathLen ? net / pathLen : 0;
    }
    if (out.EFF[i] != null && out.VA[i] != null && out.VR[i] != null) {
      const histVR2 = [];
      for (let j = Math.max(0, i - 200); j < i; j++) if (out.VR[j] != null) histVR2.push(out.VR[j]);
      const mv = mean(histVR2), sv = sd(histVR2) || 1e-9;
      const comp = -((out.VR[i] - mv) / sv);
      const reg = (out.EFF[i] + tanh(out.VA[i]) + tanh(comp)) / 3;
      out.TREND_GATE[i] = sigmoid(reg);
    }

    if (i >= 40) {
      const vwin = logVol.slice(i - 39, i + 1);
      const vm = median(vwin), vs = mad(vwin) || 1e-9;
      out.VZ[i] = (logVol[i] - vm) / vs;
    }
    const c = bars[i];
    const range = c.high - c.low;
    out.RANGE[i] = range;
    const ret1 = rets1[i];
    if (ret1 != null && range > 0) out.DISP[i] = ret1 / range;
    if (out.VZ[i] != null && out.DISP[i] != null) out.ABS[i] = out.VZ[i] * (1 - Math.abs(clamp(out.DISP[i], -3, 3)));
    if (i >= 16 && out.ABS[i] != null && out.ABS[i - 16] != null) {
      const histABS = [];
      for (let j = Math.max(0, i - 200); j < i; j++) if (out.ABS[j] != null && out.ABS[j - 16] != null) histABS.push(out.ABS[j] - out.ABS[j - 16]);
      const s = sd(histABS) || 1e-9;
      out.ABSD[i] = (out.ABS[i] - out.ABS[i - 16]) / s;
    }

    if (i >= 40) {
      const vol = Math.max(c.volume, 1e-9);
      const liq1raw = range / vol, liq2raw = ret1 != null ? Math.abs(ret1) / vol : null, liq3raw = out.VR[i];
      const zOfRecent = (arr, v) => { const m = median(arr), s = mad(arr) || 1e-9; return v != null ? (v - m) / s : null; };
      const win1 = [], win2 = [], win3 = [];
      for (let j = Math.max(0, i - 200); j < i; j++) {
        const cj = bars[j], volj = Math.max(cj.volume, 1e-9);
        win1.push((cj.high - cj.low) / volj);
        if (rets1[j] != null) win2.push(Math.abs(rets1[j]) / volj);
        if (out.VR[j] != null) win3.push(out.VR[j]);
      }
      const z1 = zOfRecent(win1, liq1raw), z2 = liq2raw != null ? zOfRecent(win2, liq2raw) : null, z3 = liq3raw != null ? zOfRecent(win3, liq3raw) : null;
      const parts = [z1, z2, z3].filter(v => v != null);
      if (parts.length === 3) out.LIQ[i] = sigmoid(mean(parts));
    }
  }
  return out;
}

function buildCrossAsset(alignedBars, perAsset) {
  const symbols = Object.keys(alignedBars);
  const n = alignedBars[symbols[0]].length;
  const out = {};
  for (const s of symbols) out[s] = { BREADTH_M: new Array(n).fill(null), RV: new Array(n).fill(null), LL: new Array(n).fill(null) };
  const BREADTH = new Array(n).fill(null), BA = new Array(n).fill(null);
  const SYNC = new Array(n).fill(null), SYNC_A = new Array(n).fill(null);
  const H = new Array(n).fill(null), DH = new Array(n).fill(null);

  const dollarVol = {};
  for (const s of symbols) dollarVol[s] = alignedBars[s].map(b => b.close * b.volume);
  const rets1 = {};
  for (const s of symbols) { rets1[s] = new Array(n).fill(null); for (let i = 1; i < n; i++) rets1[s][i] = Math.log(alignedBars[s][i].close / alignedBars[s][i - 1].close); }

  for (let i = 0; i < n; i++) {
    if (i < 200) continue;
    const weights = {}; let wSum = 0;
    for (const s of symbols) { const hist = dollarVol[s].slice(i - 200, i); const w = median(hist); weights[s] = w; wSum += w; }

    let bSum = 0, bmSum = 0;
    const states = [];
    for (const s of symbols) {
      const r = rets1[s][i];
      const sign = r == null ? 0 : (Math.abs(r) < 1e-6 ? 0 : Math.sign(r));
      states.push(sign);
      bSum += (weights[s] / wSum) * sign;
      const m = perAsset[s].M[i];
      if (m != null) bmSum += (weights[s] / wSum) * m;
    }
    BREADTH[i] = bSum;
    const upFrac = states.filter(v => v > 0).length / states.length;
    const downFrac = states.filter(v => v < 0).length / states.length;
    const neutFrac = states.filter(v => v === 0).length / states.length;
    const eps = 1e-9;
    H[i] = -[upFrac, downFrac, neutFrac].reduce((a, p) => a + (p > 0 ? p * Math.log(p + eps) : 0), 0);
    for (const s of symbols) if (perAsset[s].M[i] != null) out[s].BREADTH_M[i] = bmSum;

    const logCs = symbols.map(s => Math.log(alignedBars[s][i].close));
    const avgLogC = mean(logCs);
    for (let si = 0; si < symbols.length; si++) { const s = symbols[si]; out[s].RV_raw = out[s].RV_raw || new Array(n).fill(null); out[s].RV_raw[i] = logCs[si] - avgLogC; }

    if (i >= 1) {
      for (const s of symbols) {
        const others = symbols.filter(x => x !== s);
        const vals = others.map(o => perAsset[o].M[i - 1]).filter(v => v != null);
        if (vals.length === others.length) out[s].LL[i] = mean(vals);
      }
    }
  }

  for (let i = 200; i < n; i++) {
    if (i >= 16 && BREADTH.length) {
      for (const s of symbols) {
        if (out[s].BREADTH_M[i] != null && out[s].BREADTH_M[i - 16] != null) {
          out[s].BA = out[s].BA || new Array(n).fill(null);
          out[s].BA[i] = out[s].BREADTH_M[i] - out[s].BREADTH_M[i - 16];
        }
      }
    }
    if (i >= 16 && H[i] != null && H[i - 16] != null) DH[i] = H[i] - H[i - 16];
    for (const s of symbols) if (out[s].RV_raw && out[s].RV_raw[i] != null && out[s].RV_raw[i - 16] != null) out[s].RV[i] = out[s].RV_raw[i] - out[s].RV_raw[i - 16];

    let corrSum = 0, corrCnt = 0;
    for (let a = 0; a < symbols.length; a++) for (let b = a + 1; b < symbols.length; b++) {
      const ra = rets1[symbols[a]].slice(i - 63, i + 1), rb = rets1[symbols[b]].slice(i - 63, i + 1);
      if (ra.some(v => v == null) || rb.some(v => v == null)) continue;
      const c = S.corr(ra, rb);
      if (c != null) { corrSum += c; corrCnt++; }
    }
    if (corrCnt) SYNC[i] = corrSum / corrCnt;
    if (i >= 16 && SYNC[i] != null && SYNC[i - 16] != null) SYNC_A[i] = SYNC[i] - SYNC[i - 16];
  }
  return { perAssetCross: out, BREADTH, BA, SYNC, SYNC_A, H, DH };
}

function buildTemporal(M) {
  const n = M.length;
  const PC = new Array(n).fill(null), ACC = new Array(n).fill(null), EX = new Array(n).fill(null);
  for (let i = 32; i < n; i++) {
    if (M[i] == null || M[i - 1] == null || M[i - 2] == null) continue;
    const persist = mean([Math.abs(M[i]), Math.abs(M[i - 1]), Math.abs(M[i - 2])]);
    const dp = Math.abs(Math.sign(M[i]) + Math.sign(M[i - 1]) + Math.sign(M[i - 2])) / 3;
    PC[i] = tanh(persist) * dp;
    const acc1 = M[i] - M[i - 1], acc1prev = M[i - 1] - M[i - 2];
    ACC[i] = tanh(acc1 + 0.5 * (acc1 - acc1prev));
    const win = []; for (let j = i - 31; j <= i; j++) if (M[j] != null) win.push(M[j]);
    const peak = Math.max(...win.map(Math.abs));
    EX[i] = tanh((peak - Math.abs(M[i])) / (peak + 1e-9));
  }
  return { PC, ACC, EX };
}

const FEATURE_NAMES = ['M_COH', 'TREND_GATE', 'ABSD', 'BREADTH', 'BREADTH_M', 'BA', 'RV', 'LL', 'SYNC', 'SYNC_A', 'DH', 'PC', 'ACC', 'EX', 'LIQ'];

/** Identical to Study 20's buildFeatureRows, PLUS a `raw` side-channel
 * (M, COH, TREND_GATE, VR, VZ) stashed alongside each row -- needed only by
 * the ablation/regime branches below, never by the frozen formula itself
 * (the `x` vector every row carries is byte-for-byte what Study 20 fit on). */
function buildFeatureRows(symbol, perAsset, cross, temporal, bars) {
  const n = bars.length;
  const rows = [];
  const a = perAsset[symbol], c = cross.perAssetCross[symbol], t = temporal[symbol];
  for (let i = 0; i < n; i++) {
    if (a.M[i] == null || a.COH[i] == null) continue;
    const mCoh = a.M[i] * (0.5 + 0.5 * a.COH[i]) * (a.TREND_GATE[i] != null ? a.TREND_GATE[i] : 0.5);
    const vals = [
      mCoh, a.TREND_GATE[i], a.ABSD[i], cross.BREADTH[i], c.BREADTH_M[i], c.BA ? c.BA[i] : null, c.RV[i], c.LL[i],
      cross.SYNC[i], cross.SYNC_A[i], cross.DH[i], t.PC[i], t.ACC[i], t.EX[i], a.LIQ[i]
    ];
    if (vals.some(v => v == null || !isFinite(v))) continue;
    rows.push({ i, x: vals, raw: { M: a.M[i], COH: a.COH[i], TREND_GATE: a.TREND_GATE[i], VR: a.VR[i], VZ: a.VZ[i] } });
  }
  return rows;
}

function fitRidge(rows, lambda) {
  const p = rows[0].x.length;
  const mu = new Array(p).fill(0), sigma = new Array(p).fill(1);
  for (let j = 0; j < p; j++) { const col = rows.map(r => r.x[j]); mu[j] = mean(col); sigma[j] = sd(col) || 1; }
  const X = rows.map(r => r.x.map((v, j) => (v - mu[j]) / sigma[j]));
  const y = rows.map(r => r.y);
  const XtX = Array.from({ length: p }, () => new Array(p).fill(0));
  const Xty = new Array(p).fill(0);
  for (let n = 0; n < X.length; n++) for (let a = 0; a < p; a++) { Xty[a] += X[n][a] * y[n]; for (let b = 0; b < p; b++) XtX[a][b] += X[n][a] * X[n][b]; }
  for (let a = 0; a < p; a++) XtX[a][a] += lambda;
  return { w: solveLinearSystem(XtX, Xty), mu, sigma };
}
function solveLinearSystem(A, b) {
  const n = b.length;
  const M = A.map((row, i) => [...row, b[i]]);
  for (let col = 0; col < n; col++) {
    let piv = col;
    for (let r = col + 1; r < n; r++) if (Math.abs(M[r][col]) > Math.abs(M[piv][col])) piv = r;
    [M[col], M[piv]] = [M[piv], M[col]];
    if (Math.abs(M[col][col]) < 1e-12) continue;
    for (let r = 0; r < n; r++) { if (r === col) continue; const f = M[r][col] / M[col][col]; for (let c2 = col; c2 <= n; c2++) M[r][c2] -= f * M[col][c2]; }
  }
  return M.map((row, i) => Math.abs(row[i]) > 1e-12 ? row[n] / row[i] : 0);
}
/** predict, but taking a raw x[] array directly (not a `rows` array) so
 * ablations can substitute individual slots before scoring. */
function scoreVector(x, model) {
  let s = 0;
  for (let j = 0; j < x.length; j++) s += model.w[j] * ((x[j] - model.mu[j]) / model.sigma[j]);
  return tanh(s);
}

function barrierOutcome(bars, i, horizon, barrier = BARRIER) {
  const entry = bars[i].close;
  const up = entry * (1 + barrier), dn = entry * (1 - barrier);
  for (let k = i + 1; k <= Math.min(i + horizon, bars.length - 1); k++) {
    const hitUp = bars[k].high >= up, hitDn = bars[k].low <= dn;
    if (hitUp && hitDn) return null;
    if (hitUp) return 1; if (hitDn) return 0;
  }
  return null;
}
function computeAUC(scores, labels) {
  const pos = [], neg = [];
  for (let i = 0; i < scores.length; i++) (labels[i] ? pos : neg).push(scores[i]);
  if (!pos.length || !neg.length) return null;
  const all = scores.map((s, i) => ({ s, l: labels[i] })).sort((a, b) => a.s - b.s);
  let rankSum = 0, idx = 0;
  while (idx < all.length) { let j = idx; while (j < all.length && all[j].s === all[idx].s) j++; const avgRank = (idx + 1 + j) / 2; for (let k = idx; k < j; k++) if (all[k].l) rankSum += avgRank; idx = j; }
  return (rankSum - pos.length * (pos.length + 1) / 2) / (pos.length * neg.length);
}
function blockBootstrapP(outcomes, blockLen, iters = 300) {
  const n = outcomes.length; if (n < 30) return null;
  const observed = mean(outcomes) - 0.5;
  let seed = 424242;
  const rand = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; };
  const nBlocks = Math.ceil(n / blockLen);
  let extreme = 0;
  for (let it = 0; it < iters; it++) {
    const resample = [];
    for (let b = 0; b < nBlocks; b++) { const start = Math.floor(rand() * Math.max(1, n - blockLen)); for (let k = 0; k < blockLen && resample.length < n; k++) resample.push(outcomes[start + k]); }
    const stat = mean(resample) - mean(outcomes);
    if (Math.abs(stat) >= Math.abs(observed)) extreme++;
  }
  return (extreme + 1) / (iters + 1);
}
function causalTopMask(scores, frac, minHistory) {
  const n = scores.length; const topMask = new Array(n).fill(false);
  const REFRESH = 200; const seen = []; let hi = null, sinceRefresh = 0;
  for (let i = 0; i < n; i++) {
    if (scores[i] != null && seen.length >= minHistory) {
      if (hi == null || sinceRefresh >= REFRESH) { const sorted = seen.slice().sort((a, b) => a - b); hi = sorted[Math.floor(sorted.length * (1 - frac))]; sinceRefresh = 0; }
      if (scores[i] >= hi) topMask[i] = true;
    }
    if (scores[i] != null) { seen.push(scores[i]); sinceRefresh++; }
  }
  return topMask;
}

/** Deterministic seeded shuffle (Fisher-Yates). */
function seededShuffle(arr, seed) {
  const out = arr.slice();
  let s = seed;
  const rand = () => { s = (s * 1103515245 + 12345) & 0x7fffffff; return s / 0x7fffffff; };
  for (let i = out.length - 1; i > 0; i--) { const j = Math.floor(rand() * (i + 1)); [out[i], out[j]] = [out[j], out[i]]; }
  return out;
}

/** Evaluates a (symbol, scoreArray) pair against the real barrier outcome
 * at EVAL_HORIZON, optionally restricted to a subset of bar indices
 * (for regime splits) and optionally with outcomes shuffled (placebo). */
function evaluate(bars, scoreByIdx, costBps, { subsetMask = null, shuffleOutcomes = false, shuffleSeed = 1 } = {}) {
  const n = bars.length;
  const idxs = [...scoreByIdx.keys()].filter(i => !subsetMask || subsetMask[i]);
  let outcomes = idxs.map(i => barrierOutcome(bars, i, EVAL_HORIZON));
  const validPairs = [];
  for (let k = 0; k < idxs.length; k++) if (outcomes[k] != null) validPairs.push({ i: idxs[k], score: scoreByIdx.get(idxs[k]), y: outcomes[k] });
  if (validPairs.length < 300) return null;

  let ys = validPairs.map(p => p.y);
  if (shuffleOutcomes) ys = seededShuffle(ys, shuffleSeed);
  const scoresArr = new Array(Math.max(...validPairs.map(p => p.i)) + 1).fill(null);
  for (const p of validPairs) scoresArr[p.i] = p.score;

  const auc = computeAUC(validPairs.map(p => p.score), ys);
  const topMask = causalTopMask(scoresArr, 0.10, 200);
  const topPairs = [];
  for (let k = 0; k < validPairs.length; k++) if (topMask[validPairs[k].i]) topPairs.push({ ...validPairs[k], y: ys[k] });
  if (topPairs.length < 30) return { n: validPairs.length, auc, topN: topPairs.length, topWR: null };
  const topWR = mean(topPairs.map(p => p.y));
  const costR = costBps / 100;
  const expR = topWR * 1 - (1 - topWR) * 1 - costR; // symmetric 1:1 barrier, same convention as Study 20
  const p = blockBootstrapP(topPairs.map(p => p.y), Math.max(8, EVAL_HORIZON * 3));
  return { n: validPairs.length, auc, topN: topPairs.length, topWR, p, expR };
}

function fmt(res) {
  if (!res) return 'n<300';
  if (res.topWR == null) return `AUC=${res.auc != null ? res.auc.toFixed(3) : '—'} (topN<30)`;
  return `AUC=${res.auc.toFixed(3)}  n=${String(res.topN).padStart(5)}  WR=${(res.topWR * 100).toFixed(1)}%  expR=${res.expR.toFixed(3)}  p=${res.p != null ? res.p.toFixed(3) : '—'}`;
}

// ═══════════════════════════════════════════════════════════════════════
function main() {
  const args = {};
  for (let i = 2; i < process.argv.length; i += 2) args[process.argv[i].replace(/^--/, '')] = process.argv[i + 1];
  const costBps = parseFloat(args.cost || String(COST_BPS_DEFAULT));
  const line = '─'.repeat(114);

  console.log(`\n${line}\n  STUDY 23 — CME-X4 FORENSIC RECOVERY: WHERE DOES THE ORIGINAL EDGE LIVE?\n${line}`);
  console.log('  Loading, aligning, building the EXACT Study-20 feature set...');
  const rawBars = {}; for (const s of COINS) rawBars[s] = loadBars(s);
  const { bars: alignedBars } = alignAll(rawBars);
  const perAsset = {}; for (const s of COINS) perAsset[s] = buildAssetSeries(alignedBars[s]);
  const cross = buildCrossAsset(alignedBars, perAsset);
  const temporal = {};
  for (const s of COINS) {
    const mCoh = perAsset[s].M.map((m, i) => m != null && perAsset[s].COH[i] != null && perAsset[s].TREND_GATE[i] != null
      ? m * (0.5 + 0.5 * perAsset[s].COH[i]) * perAsset[s].TREND_GATE[i] : null);
    temporal[s] = buildTemporal(mCoh);
  }
  const rows = {}; for (const s of COINS) rows[s] = buildFeatureRows(s, perAsset, cross, temporal, alignedBars[s]);
  console.log(`  Rows per coin: ${COINS.map(s => `${s} ${rows[s].length}`).join(', ')}\n`);

  function outcomesFor(symbol, horizon) {
    const bars = alignedBars[symbol]; const m = new Map();
    for (const r of rows[symbol]) { const y = barrierOutcome(bars, r.i, horizon); if (y != null) m.set(r.i, y); }
    return m;
  }

  // ── FREEZE THE ORIGINAL MODEL: Experiment B's exact training setup ──
  console.log(`${line}\n  FREEZING THE ORIGINAL MODEL (Study 20 Experiment B: train ${TRAIN_COINS.join('+')}, fit horizon ${FIT_HORIZON})\n${line}`);
  const trainRows = [];
  for (const s of TRAIN_COINS) { const y = outcomesFor(s, FIT_HORIZON); for (const r of rows[s]) if (y.has(r.i)) trainRows.push({ i: r.i, x: r.x, y: y.get(r.i) }); }
  const frozen = fitRidge(trainRows, RIDGE_LAMBDA);
  console.log(`  Frozen weights: ${FEATURE_NAMES.map((nm, j) => `${nm}=${frozen.w[j].toFixed(3)}`).join(', ')}\n`);

  function baselineScores(symbol) {
    const m = new Map();
    for (const r of rows[symbol]) m.set(r.i, scoreVector(r.x, frozen));
    return m;
  }

  console.log(`${line}\n  BASELINE — frozen model, unmodified, h=${EVAL_HORIZON} (30min)\n${line}`);
  const baseline = {};
  for (const s of TEST_COINS) {
    baseline[s] = evaluate(alignedBars[s], baselineScores(s), costBps);
    console.log(`  ${s.padEnd(10)} ${fmt(baseline[s])}`);
  }
  console.log('');

  const results = { baseline: {} };
  for (const s of TEST_COINS) results.baseline[s] = baseline[s];

  // ── REMOVAL ABLATIONS ──
  console.log(`${line}\n  REMOVAL ABLATIONS — does this feature's real-time value matter?\n${line}`);
  const REMOVALS = {
    removeMomentum: (r, j0) => { const x = r.x.slice(); x[0] = frozen.mu[0]; return x; },
    removeVolatility: (r) => { const x = r.x.slice(); x[1] = frozen.mu[1]; return x; },
    removeVolume: (r) => { const x = r.x.slice(); x[2] = frozen.mu[2]; return x; },
    removeNonlinearInteraction: (r) => { const x = r.x.slice(); x[0] = r.raw.M; return x; } // M_COH -> raw M
  };
  results.removals = {};
  for (const [name, fn] of Object.entries(REMOVALS)) {
    console.log(`  ${name}:`);
    results.removals[name] = {};
    for (const s of TEST_COINS) {
      const scoreByIdx = new Map(rows[s].map(r => [r.i, scoreVector(fn(r), frozen)]));
      const res = evaluate(alignedBars[s], scoreByIdx, costBps);
      results.removals[name][s] = res;
      console.log(`    ${s.padEnd(10)} ${fmt(res)}`);
    }
  }
  console.log('');

  // ── PER-FEATURE SHUFFLE + LABEL SHUFFLE ──
  console.log(`${line}\n  SHUFFLE ABLATIONS — does this feature's TEMPORAL ALIGNMENT matter (not just its distribution)?\n${line}`);
  results.shuffles = {};
  for (let j = 0; j < FEATURE_NAMES.length; j++) {
    const name = FEATURE_NAMES[j];
    results.shuffles[name] = {};
    const line2 = [];
    for (const s of TEST_COINS) {
      const shuffledCol = seededShuffle(rows[s].map(r => r.x[j]), 1000 + j);
      const scoreByIdx = new Map(rows[s].map((r, k) => { const x = r.x.slice(); x[j] = shuffledCol[k]; return [r.i, scoreVector(x, frozen)]; }));
      const res = evaluate(alignedBars[s], scoreByIdx, costBps);
      results.shuffles[name][s] = res;
      line2.push(`${s}:${res && res.auc != null ? res.auc.toFixed(3) : '—'}`);
    }
    console.log(`  shuffle ${name.padEnd(12)} ${line2.join('  ')}`);
  }
  console.log(`\n  shuffle LABELS (placebo -- the ultimate control):`);
  results.labelShuffle = {};
  for (const s of TEST_COINS) {
    const res = evaluate(alignedBars[s], baselineScores(s), costBps, { shuffleOutcomes: true, shuffleSeed: 777 });
    results.labelShuffle[s] = res;
    console.log(`    ${s.padEnd(10)} ${fmt(res)}`);
  }
  console.log('');

  // ── SHIFT ABLATIONS ──
  console.log(`${line}\n  SHIFT ABLATIONS — stale (backward) vs. future (forward) features, same outcome bar\n${line}`);
  results.shifts = {};
  for (const shift of [-2, 2]) {
    const label = shift < 0 ? 'shiftBackward2bars' : 'shiftForward2bars';
    results.shifts[label] = {};
    console.log(`  ${label}:`);
    for (const s of TEST_COINS) {
      const byIndex = new Map(rows[s].map(r => [r.i, r]));
      const scoreByIdx = new Map();
      for (const r of rows[s]) { const shifted = byIndex.get(r.i + shift); if (shifted) scoreByIdx.set(r.i, scoreVector(shifted.x, frozen)); }
      const res = evaluate(alignedBars[s], scoreByIdx, costBps);
      results.shifts[label][s] = res;
      console.log(`    ${s.padEnd(10)} ${fmt(res)}`);
    }
  }
  console.log('');

  // ── SINGLE-ASSET-ONLY REFITS (the one place this file refits) ──
  console.log(`${line}\n  SINGLE-ASSET-ONLY — same formula, refit on each coin's own history alone (60/40 split)\n${line}`);
  results.singleAsset = {};
  for (const s of COINS) {
    const allRows = rows[s];
    const cut = Math.floor(allRows.length * 0.6);
    const y = outcomesFor(s, FIT_HORIZON);
    const trainR = allRows.slice(0, cut).filter(r => y.has(r.i)).map(r => ({ i: r.i, x: r.x, y: y.get(r.i) }));
    const testR = allRows.slice(cut);
    if (trainR.length < 500) { console.log(`  ${s.padEnd(10)} insufficient training rows`); continue; }
    const model = fitRidge(trainR, RIDGE_LAMBDA);
    const scoreByIdx = new Map(testR.map(r => [r.i, scoreVector(r.x, model)]));
    const res = evaluate(alignedBars[s], scoreByIdx, costBps);
    results.singleAsset[s] = res;
    console.log(`  ${s.padEnd(10)} ${fmt(res)}`);
  }
  console.log('');

  // ── REGIME SPLITS — thresholds fixed from TRAINING population only ──
  console.log(`${line}\n  REGIME SPLITS — where specifically does the frozen model's edge live?\n${line}`);
  const trainVR = TRAIN_COINS.flatMap(s => rows[s].map(r => r.raw.VR)).filter(v => v != null);
  const trainVZ = TRAIN_COINS.flatMap(s => rows[s].map(r => r.raw.VZ)).filter(v => v != null);
  const vrThresh = median(trainVR), vzThresh = median(trainVZ);
  console.log(`  Thresholds (frozen, from training population): VR median=${vrThresh.toFixed(3)}, VZ median=${vzThresh.toFixed(3)}\n`);

  function regimeSplit(name, predicate) {
    results.regimes = results.regimes || {};
    results.regimes[name] = {};
    console.log(`  ${name}:`);
    for (const s of TEST_COINS) {
      const mask = {};
      for (const r of rows[s]) mask[r.i] = predicate(r);
      const scores = baselineScores(s);
      const res = evaluate(alignedBars[s], scores, costBps, { subsetMask: mask });
      results.regimes[name][s] = res;
      console.log(`    ${s.padEnd(10)} ${fmt(res)}`);
    }
  }
  regimeSplit('positiveMomentum (M>0)', r => r.raw.M > 0);
  regimeSplit('negativeMomentum (M<0)', r => r.raw.M < 0);
  regimeSplit('highVolatility (VR>trainMedian)', r => r.raw.VR != null && r.raw.VR > vrThresh);
  regimeSplit('lowVolatility (VR<=trainMedian)', r => r.raw.VR != null && r.raw.VR <= vrThresh);
  regimeSplit('highVolume (VZ>trainMedian)', r => r.raw.VZ != null && r.raw.VZ > vzThresh);
  regimeSplit('lowVolume (VZ<=trainMedian)', r => r.raw.VZ != null && r.raw.VZ <= vzThresh);
  console.log('');

  console.log(`${line}\n`);
  fs.mkdirSync(path.join(__dirname, 'results'), { recursive: true });
  fs.writeFileSync(path.join(__dirname, 'results', 'cme-x4-forensic.json'),
    JSON.stringify({ generatedAt: new Date().toISOString(), costBps, frozenWeights: frozen.w, featureNames: FEATURE_NAMES, vrThresh, vzThresh, results }, null, 2));
}

main();
