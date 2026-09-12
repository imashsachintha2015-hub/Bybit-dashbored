#!/usr/bin/env node
/**
 * STUDY 22 — CME-X3, scoped to a focused, honest subset.
 *
 * The uploaded CME-X3 specification is a 53-section, multi-week research
 * program (a full model tournament across random forests, gradient
 * boosting, neural nets, symbolic regression search, hierarchical
 * clustering, conformal prediction, Pareto frontiers, equation
 * clustering...). This environment has no sklearn/numpy/xgboost -- pure
 * Node.js only -- and implementing the full spec in one sitting would
 * produce a shallow, likely-buggy result across 53 sections rather than a
 * trustworthy one. Per the user's explicit choice, this implements a
 * smaller subset with real rigor instead:
 *
 *   1. THE OPEN QUESTION FROM STUDY 21, closed. Study 20's symmetric
 *      barrier + flat feature blend found a real BTC/ETH edge (AUC
 *      0.556-0.588). Study 21's asymmetric barrier + "Market Potential"
 *      interaction terms found nothing (AUC 0.48-0.52) on the SAME data.
 *      Which change destroyed it? A 2x2 (symmetric/asymmetric barrier x
 *      flat/interaction feature structure) isolates the answer.
 *   2. A GENUINE NONLINEAR CHALLENGER (section 30/53.1). Every prior study
 *      fit only linear (ridge) combinations. A from-scratch gradient-
 *      boosted shallow-tree model is added here specifically because it
 *      can discover conditional relationships (section 33: "feature A
 *      matters only at high volatility") that no linear blend can
 *      express, without requiring symbolic-regression infrastructure this
 *      session doesn't have time to build correctly.
 *   3. SIGNAL VS. PERSISTENCE DECOMPISITION (section 53.24). This
 *      project's own research (Part XII) flagged persistence as a
 *      possible confound once already. Explicitly separated here: signal
 *      alone, persistence alone, both together, and a persistence value
 *      computed on SHUFFLED history (a persistence-shaped control that
 *      carries no real information) as the placebo.
 *   4. LEAVE-ONE-ASSET-OUT for all 6 coins individually (section 38),
 *      cost robustness (section 46), threshold/selectivity robustness
 *      (sections 45/50), and one of the three verdicts the document itself
 *      mandates (section 53.36).
 *
 * NOT implemented, and why: random forests / neural nets (redundant with
 * the gradient-boosted-tree challenger for this question -- one genuine
 * nonlinear model is enough to test "can nonlinearity find what linear
 * blends missed"); symbolic regression search (needs its own validated
 * infrastructure this session doesn't have time to build correctly, so a
 * rushed version would be worse than none); regime-conditional and
 * hierarchical (group/global) models, state clustering, distribution-shift
 * detection, conformal prediction, and equation-stability-across-seeds
 * replication (each a substantial sub-project on its own; only worth
 * building once something here actually survives the tests below).
 */
const fs = require('fs');
const path = require('path');
const S = require('./lib-stats.js');

const DATA = path.join(__dirname, '..', 'backtest', 'data');
const COINS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'DOGEUSDT', 'LINKUSDT', 'AVAXUSDT'];
const TRAIN_SET_A = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT'];
const TEST_SET_A = ['DOGEUSDT', 'LINKUSDT', 'AVAXUSDT'];
const D_HORIZONS = [1, 2, 4, 8, 16, 32];
const H_LIST = [2, 4, 8];          // trimmed from Study 21's {2,4,8,16,32} -- see file header
const FIT_HORIZON = 4;
const RIDGE_LAMBDA = 5.0;
const COST_BPS_DEFAULT = 4;
const BARRIERS = { symmetric: { alpha: 1.0, beta: 1.0 }, asymmetric: { alpha: 1.0, beta: 0.75 } };

function mean(a) { return a.length ? a.reduce((x, y) => x + y, 0) / a.length : 0; }
function sd(a) { if (a.length < 2) return 0; const m = mean(a); return Math.sqrt(a.reduce((x, y) => x + (y - m) ** 2, 0) / (a.length - 1)); }
function median(a) { const s = a.slice().sort((x, y) => x - y); return s[Math.floor(s.length / 2)]; }
function tanh(x) { return Math.tanh(x); }
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
// O(1)-amortized sliding statistics (verbatim from Study 21 -- this is
// what made that study runnable at all; re-deriving it slower here would
// repeat the exact mistake that study's own header warns about).
// ═══════════════════════════════════════════════════════════════════════
class SlidingMV {
  constructor(win) { this.win = win; this.buf = new Array(win).fill(0); this.ptr = 0; this.n = 0; this.sum = 0; this.sumSq = 0; }
  push(v) {
    if (this.n === this.win) { const old = this.buf[this.ptr]; this.sum -= old; this.sumSq -= old * old; } else this.n++;
    this.buf[this.ptr] = v; this.sum += v; this.sumSq += v * v;
    this.ptr = (this.ptr + 1) % this.win;
  }
  mean() { return this.n ? this.sum / this.n : null; }
  variance() { if (this.n < 2) return null; const m = this.mean(); return Math.max(0, this.sumSq / this.n - m * m); }
  std() { const v = this.variance(); return v == null ? null : Math.sqrt(v); }
  z(v) { const m = this.mean(), s = this.std(); return (m == null || !s) ? null : (v - m) / s; }
  ready(min) { return this.n >= min; }
}

class SlidingCov {
  constructor(win) { this.win = win; this.a = new Array(win).fill(0); this.b = new Array(win).fill(0); this.ptr = 0; this.n = 0; this.sa = 0; this.sb = 0; this.saa = 0; this.sbb = 0; this.sab = 0; }
  push(x, y) {
    if (this.n === this.win) {
      const oa = this.a[this.ptr], ob = this.b[this.ptr];
      this.sa -= oa; this.sb -= ob; this.saa -= oa * oa; this.sbb -= ob * ob; this.sab -= oa * ob;
    } else this.n++;
    this.a[this.ptr] = x; this.b[this.ptr] = y;
    this.sa += x; this.sb += y; this.saa += x * x; this.sbb += y * y; this.sab += x * y;
    this.ptr = (this.ptr + 1) % this.win;
  }
  stats() {
    if (this.n < 2) return null;
    const ma = this.sa / this.n, mb = this.sb / this.n;
    const varA = Math.max(0, this.saa / this.n - ma * ma), varB = Math.max(0, this.sbb / this.n - mb * mb);
    const cov = this.sab / this.n - ma * mb;
    return { n: this.n, meanA: ma, meanB: mb, varA, varB, cov, corr: (varA && varB) ? cov / Math.sqrt(varA * varB) : null };
  }
}

// ═══════════════════════════════════════════════════════════════════════
// PER-ASSET STATE (D,C,V,F,L,X,P,A,E) -- verbatim construction from Study
// 21, already validated there. Reused rather than rebuilt.
// ═══════════════════════════════════════════════════════════════════════
function buildAssetState(bars) {
  const n = bars.length;
  const closes = bars.map(c => c.close);
  const logVol = bars.map(c => Math.log(Math.max(c.volume, 1e-9)));
  const rets1 = new Array(n).fill(null);
  for (let i = 1; i < n; i++) rets1[i] = Math.log(closes[i] / closes[i - 1]);

  const rh = {}; for (const h of D_HORIZONS) rh[h] = new Array(n).fill(null);
  for (const h of D_HORIZONS) for (let i = h; i < n; i++) rh[h][i] = Math.log(closes[i] / closes[i - h]);

  const out = {
    D: new Array(n).fill(null), C: new Array(n).fill(null),
    sigmaFast: new Array(n).fill(null), sigmaMed: new Array(n).fill(null), sigmaSlow: new Array(n).fill(null),
    V: new Array(n).fill(null), volExpansion: new Array(n).fill(null),
    F: new Array(n).fill(null), L: new Array(n).fill(null),
    X: new Array(n).fill(null), P: new Array(n).fill(null), A: new Array(n).fill(null),
    E: new Array(n).fill(null), zRets1: new Array(n).fill(null)
  };

  const SIGMA_WIN = 200;
  const rhWin = {}; for (const h of D_HORIZONS) rhWin[h] = new SlidingMV(SIGMA_WIN);
  const sigFastWin = new SlidingMV(16), sigMedWin = new SlidingMV(64), sigSlowWin = new SlidingMV(128);
  const r1Win = new SlidingMV(200), volWin = new SlidingMV(200);
  const rangeWin = new SlidingMV(200), gapWin = new SlidingMV(200), volAnomWin = new SlidingMV(200);
  const volExpWin = new SlidingMV(200);

  for (let i = 0; i < n; i++) {
    if (i > 0) for (const h of D_HORIZONS) if (rh[h][i - 1] != null) rhWin[h].push(rh[h][i - 1]);
    if (i >= 32 + SIGMA_WIN) {
      let sumD = 0, cnt = 0, signSum = 0;
      for (const h of D_HORIZONS) {
        const r = rh[h][i];
        if (r == null || !rhWin[h].ready(40)) continue;
        const s = rhWin[h].std() || 1e-9;
        sumD += tanh(r / s); signSum += Math.sign(r); cnt++;
      }
      if (cnt === D_HORIZONS.length) { out.D[i] = sumD / cnt; out.C[i] = Math.abs(signSum / D_HORIZONS.length); }
    }

    if (rets1[i] != null) { sigFastWin.push(rets1[i]); sigMedWin.push(rets1[i]); sigSlowWin.push(rets1[i]); }
    if (i >= 128) {
      out.sigmaFast[i] = sigFastWin.std(); out.sigmaMed[i] = sigMedWin.std(); out.sigmaSlow[i] = sigSlowWin.std();
      if (out.sigmaFast[i] && out.sigmaMed[i] && out.sigmaSlow[i]) {
        const vr1 = out.sigmaFast[i] / out.sigmaMed[i], vr2 = out.sigmaMed[i] / out.sigmaSlow[i], vr3 = out.sigmaFast[i] / out.sigmaSlow[i];
        out.V[i] = (vr1 + vr2 + vr3) / 3;
        out.volExpansion[i] = out.sigmaFast[i] - out.sigmaSlow[i];
      }
    }

    const zR1 = r1Win.ready(40) ? r1Win.z(rets1[i] != null ? rets1[i] : r1Win.mean()) : null;
    out.zRets1[i] = zR1;
    const zVol = volWin.ready(40) ? volWin.z(logVol[i]) : null;
    if (zR1 != null && zVol != null) out.F[i] = clamp(zR1, -4, 4) * clamp(zVol, -4, 4);
    if (rets1[i] != null) r1Win.push(rets1[i]);
    volWin.push(logVol[i]);

    const c = bars[i];
    const rangePct = (c.high - c.low) / c.close;
    const gapPct = i > 0 ? Math.abs(c.close - bars[i - 1].close) / bars[i - 1].close : null;
    const zRange = rangeWin.ready(40) ? rangeWin.z(rangePct) : null;
    const zGap = (gapPct != null && gapWin.ready(40)) ? gapWin.z(gapPct) : null;
    const zVolAnom = volAnomWin.ready(40) ? volAnomWin.z(c.volume) : null;
    if (zRange != null && zGap != null && zVolAnom != null) out.L[i] = tanh(clamp(zRange, -4, 4) + clamp(zGap, -4, 4) + clamp(zVolAnom, -4, 4));
    rangeWin.push(rangePct); if (gapPct != null) gapWin.push(gapPct); volAnomWin.push(c.volume);

    if (out.D[i] != null && i >= 9) {
      const pks = [];
      for (const k of [1, 2, 4, 8]) {
        let acc = 0, cnt2 = 0;
        for (let j = 0; j < k; j++) { if (out.D[i - j] == null) continue; acc += Math.sign(out.D[i - j]) * Math.sign(out.D[i]); cnt2++; }
        if (cnt2 === k) pks.push(acc / k);
      }
      if (pks.length === 4) out.P[i] = mean(pks);
    }

    if (out.D[i] != null && out.D[i - 1] != null) out.A[i] = out.D[i] - out.D[i - 1];

    const volExpZ = volExpWin.ready(40) && out.volExpansion[i] != null ? volExpWin.z(out.volExpansion[i]) : null;
    if (out.D[i] != null && out.P[i] != null && volExpZ != null) out.X[i] = tanh(Math.abs(out.D[i]) * out.P[i] * clamp(volExpZ, -4, 4));
    if (out.volExpansion[i] != null) volExpWin.push(out.volExpansion[i]);

    if (i >= 64) {
      const bins = [0, 0, 0, 0, 0]; let total = 0;
      for (let j = i - 63; j <= i; j++) { const z = out.zRets1[j]; if (z == null) continue; total++; if (z < -1.5) bins[0]++; else if (z < -0.5) bins[1]++; else if (z < 0.5) bins[2]++; else if (z < 1.5) bins[3]++; else bins[4]++; }
      if (total >= 40) { let ent = 0; for (const b of bins) { const p = b / total; if (p > 0) ent -= p * Math.log(p); } out.E[i] = ent / Math.log(5); }
    }
  }
  return out;
}

function buildCrossAsset(alignedBars) {
  const symbols = Object.keys(alignedBars);
  const n = alignedBars[symbols[0]].length;
  const rets1 = {};
  for (const s of symbols) { rets1[s] = new Array(n).fill(null); for (let i = 1; i < n; i++) rets1[s][i] = Math.log(alignedBars[s][i].close / alignedBars[s][i - 1].close); }
  const marketReturn = new Array(n).fill(null);
  for (let i = 0; i < n; i++) { const rs = symbols.map(s => rets1[s][i]).filter(v => v != null); if (rs.length === symbols.length) marketReturn[i] = median(rs); }

  const RR = {}; for (const s of symbols) RR[s] = new Array(n).fill(null);
  const SYNC = new Array(n).fill(null);
  const BETA_WIN = 200, SYNC_WIN = 64;
  const betaCov = {}; for (const s of symbols) betaCov[s] = new SlidingCov(BETA_WIN);
  const pairCov = [];
  for (let a = 0; a < symbols.length; a++) for (let b = a + 1; b < symbols.length; b++) pairCov.push({ a: symbols[a], b: symbols[b], cov: new SlidingCov(SYNC_WIN) });

  for (let i = 0; i < n; i++) {
    if (i > 0 && marketReturn[i - 1] != null) for (const s of symbols) if (rets1[s][i - 1] != null) betaCov[s].push(rets1[s][i - 1], marketReturn[i - 1]);
    if (marketReturn[i] != null) {
      for (const s of symbols) {
        if (rets1[s][i] == null) continue;
        const st = betaCov[s].stats();
        if (!st || st.n < 40 || !st.varB) continue;
        const beta = st.cov / st.varB;
        const resid = rets1[s][i] - beta * marketReturn[i];
        const residVar = st.varA - 2 * beta * st.cov + beta * beta * st.varB;
        const residSd = Math.sqrt(Math.max(0, residVar)) || 1e-9;
        RR[s][i] = clamp(resid / residSd, -4, 4);
      }
    }
    if (i > 0) for (const pc of pairCov) if (rets1[pc.a][i - 1] != null && rets1[pc.b][i - 1] != null) pc.cov.push(rets1[pc.a][i - 1], rets1[pc.b][i - 1]);
    let corrSum = 0, corrCnt = 0;
    for (const pc of pairCov) { const st = pc.cov.stats(); if (st && st.n >= SYNC_WIN && st.corr != null) { corrSum += st.corr; corrCnt++; } }
    if (corrCnt) SYNC[i] = corrSum / corrCnt;
  }
  return { RR, SYNC };
}

// ═══════════════════════════════════════════════════════════════════════
// FEATURE ROWS -- FLAT (11 raw state variables) and INTERACT (CME-X2's own
// 6 named products). Also a PERSISTENCE-CONTROL row set (P replaced by a
// deterministically shuffled version of itself -- same marginal
// distribution, no real temporal relationship to that bar's own outcome).
// ═══════════════════════════════════════════════════════════════════════
const FLAT_NAMES = ['D', 'C', 'V', 'F', 'L', 'X', 'P', 'A', 'E', 'RR', 'SYNC'];
const INTERACT_NAMES = ['D_C_POT', 'F_D', 'RR_SYNC', 'P_A', 'X_L', 'E_C'];

function buildFeatureRows(symbol, state, cross) {
  const st = state[symbol];
  const n = st.D.length;
  const flat = [], interact = [];
  for (let i = 0; i < n; i++) {
    const D = st.D[i], C = st.C[i], V = st.V[i], F = st.F[i], L = st.L[i], X = st.X[i], P = st.P[i], A = st.A[i], E = st.E[i];
    const RR = cross.RR[symbol][i], SYNC = cross.SYNC[i];
    const vals = [D, C, V, F, L, X, P, A, E, RR, SYNC];
    if (vals.some(v => v == null || !isFinite(v))) continue;
    flat.push({ i, x: vals });
    const POT = tanh(Math.abs(D) * C * (1 + F) / (1 + Math.abs(X) + Math.abs(L)));
    interact.push({ i, x: [D * C * POT, F * D, RR * SYNC, P * A, -X * L, E * C] });
  }
  return { flat, interact };
}

/** Deterministic pseudo-shuffle of an array's VALUES (not its index order)
 * -- a Fisher-Yates with a fixed seed, so the persistence-control feature
 * has the identical marginal distribution as real persistence but no
 * relationship to any particular bar's own future outcome. */
function shuffleValues(arr, seed) {
  const out = arr.slice();
  let s = seed;
  const rand = () => { s = (s * 1103515245 + 12345) & 0x7fffffff; return s / 0x7fffffff; };
  for (let i = out.length - 1; i > 0; i--) { const j = Math.floor(rand() * (i + 1)); [out[i], out[j]] = [out[j], out[i]]; }
  return out;
}

// ═══════════════════════════════════════════════════════════════════════
// VOLATILITY-RELATIVE BARRIER TARGET -- alpha/beta now PARAMETERS (Study
// 21 hard-coded them as constants; this study needs both configs).
// ═══════════════════════════════════════════════════════════════════════
function longOutcome(bars, i, horizon, sigma, alpha, beta) {
  if (!sigma) return null;
  const B = sigma * Math.sqrt(horizon);
  const entry = bars[i].close;
  const tp = entry * (1 + alpha * B), sl = entry * (1 - beta * B);
  for (let k = i + 1; k <= Math.min(i + horizon, bars.length - 1); k++) {
    const hitTp = bars[k].high >= tp, hitSl = bars[k].low <= sl;
    if (hitTp && hitSl) return null;
    if (hitTp) return 1; if (hitSl) return 0;
  }
  return null;
}
function shortOutcome(bars, i, horizon, sigma, alpha, beta) {
  if (!sigma) return null;
  const B = sigma * Math.sqrt(horizon);
  const entry = bars[i].close;
  const tp = entry * (1 - alpha * B), sl = entry * (1 + beta * B);
  for (let k = i + 1; k <= Math.min(i + horizon, bars.length - 1); k++) {
    const hitTp = bars[k].low <= tp, hitSl = bars[k].high >= sl;
    if (hitTp && hitSl) return null;
    if (hitTp) return 1; if (hitSl) return 0;
  }
  return null;
}
function fairBaseline(alpha, beta) { return beta / (alpha + beta); } // WR*(alpha/beta)=(1-WR) solved for WR

// ═══════════════════════════════════════════════════════════════════════
// RIDGE (verbatim mechanism from Study 21/20)
// ═══════════════════════════════════════════════════════════════════════
// IMPORTANT: rows must carry their own `.y` (embedded per-row), NOT be
// looked up afterward via a shared `Map<index, y>` keyed by `r.i` alone.
// Row indices `i` are each symbol's own 0..n-1 position in its ALIGNED
// series, so BTC's row i=5000 and ETH's row i=5000 are DIFFERENT bars that
// share the same index -- a shared lookup map silently lets whichever
// symbol was processed last overwrite every earlier symbol's label at
// that index, corrupting every pooled multi-symbol training fit (this
// exact bug shipped in Study 20's Experiments A/B and Study 21's
// Universal Asset Tests + leave-one-out; see research/README.md's
// correction note). Embedding `y` on the row itself makes the bug
// structurally impossible instead of relying on remembering to key
// correctly.
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
  return { type: 'ridge', w: solveLinearSystem(XtX, Xty), mu, sigma };
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
function predictRidge(rows, model) {
  const out = new Map();
  for (const r of rows) { let s = 0; for (let j = 0; j < r.x.length; j++) s += model.w[j] * ((r.x[j] - model.mu[j]) / model.sigma[j]); out.set(r.i, tanh(s)); }
  return out;
}

// ═══════════════════════════════════════════════════════════════════════
// GRADIENT-BOOSTED SHALLOW TREES -- a genuine nonlinear challenger,
// implemented from scratch (no ML library available in this environment).
// Depth-2 regression trees (up to 4 leaves) via greedy per-feature
// mean+/-k*std threshold search (cheap: no sort needed), boosted on
// squared-error residuals of the 0/1 outcome. This can express "feature A
// matters differently depending on feature B" -- section 33's explicit
// ask -- which no linear (ridge) blend can.
// ═══════════════════════════════════════════════════════════════════════
function fitTree(rows, resid, depth) {
  const idx = rows.map((_, k) => k);
  return growNode(rows, resid, idx, depth);
}
function growNode(rows, resid, idx, depth) {
  const n = idx.length;
  const sumY = idx.reduce((a, k) => a + resid[k], 0);
  const leafVal = n ? sumY / n : 0;
  if (depth === 0 || n < 60) return { leaf: true, value: leafVal };

  const p = rows[0].x.length;
  let best = null;
  for (let f = 0; f < p; f++) {
    const vals = idx.map(k => rows[k].x[f]);
    const m = mean(vals), s = sd(vals) || 1e-9;
    for (const mult of [-1.5, -0.5, 0, 0.5, 1.5]) {
      const thresh = m + mult * s;
      let leftSum = 0, leftN = 0, rightSum = 0, rightN = 0;
      for (const k of idx) { if (rows[k].x[f] <= thresh) { leftSum += resid[k]; leftN++; } else { rightSum += resid[k]; rightN++; } }
      if (leftN < 30 || rightN < 30) continue;
      const leftMean = leftSum / leftN, rightMean = rightSum / rightN;
      let sse = 0;
      for (const k of idx) { const pred = rows[k].x[f] <= thresh ? leftMean : rightMean; sse += (resid[k] - pred) ** 2; }
      if (!best || sse < best.sse) best = { sse, f, thresh, leftN, rightN };
    }
  }
  if (!best) return { leaf: true, value: leafVal };
  const leftIdx = idx.filter(k => rows[k].x[best.f] <= best.thresh);
  const rightIdx = idx.filter(k => rows[k].x[best.f] > best.thresh);
  return { leaf: false, f: best.f, thresh: best.thresh, left: growNode(rows, resid, leftIdx, depth - 1), right: growNode(rows, resid, rightIdx, depth - 1) };
}
function predictTree(node, x) {
  if (node.leaf) return node.value;
  return x[node.f] <= node.thresh ? predictTree(node.left, x) : predictTree(node.right, x);
}
function fitGBM(rows, rounds, lr, depth) {
  const y = rows.map(r => r.y); // rows must carry `.y` -- see fitRidge's comment
  const pred = new Array(rows.length).fill(mean(y));
  const trees = [];
  for (let t = 0; t < rounds; t++) {
    const resid = y.map((yi, k) => yi - pred[k]);
    const tree = fitTree(rows, resid, depth);
    trees.push(tree);
    for (let k = 0; k < rows.length; k++) pred[k] += lr * predictTree(tree, rows[k].x);
  }
  return { type: 'gbm', trees, lr, base: mean(y) };
}
function predictGBM(rows, model) {
  const out = new Map();
  for (const r of rows) {
    let s = model.base;
    for (const t of model.trees) s += model.lr * predictTree(t, r.x);
    out.set(r.i, tanh(s * 3)); // scaled into a comparable range to the ridge/tanh scores
  }
  return out;
}

// ═══════════════════════════════════════════════════════════════════════
// EVALUATION HARNESS (verbatim mechanism from Study 20/21)
// ═══════════════════════════════════════════════════════════════════════
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
  let seed = 271828;
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
function evaluateDirectional(scoreByIdx, outcomeByIdx, costR, rewardR, frac, minTop) {
  const idxs = [...scoreByIdx.keys()].filter(i => outcomeByIdx.has(i));
  if (idxs.length < 300) return null;
  const scoresArr = new Array(Math.max(...idxs) + 1).fill(null);
  for (const i of idxs) scoresArr[i] = scoreByIdx.get(i);
  const auc = computeAUC(idxs.map(i => scoreByIdx.get(i)), idxs.map(i => outcomeByIdx.get(i)));
  const topMask = causalTopMask(scoresArr, frac, 200);
  const topIdx = idxs.filter(i => topMask[i]);
  if (topIdx.length < minTop) return { n: idxs.length, auc, topN: topIdx.length, topWR: null };
  const topWR = mean(topIdx.map(i => outcomeByIdx.get(i)));
  const expR = topWR * rewardR - (1 - topWR) * 1 - costR;
  const sorted = topIdx.slice().sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  const first = sorted.slice(0, mid).map(i => outcomeByIdx.get(i)), second = sorted.slice(mid).map(i => outcomeByIdx.get(i));
  const p = blockBootstrapP(topIdx.map(i => outcomeByIdx.get(i)), 24);
  return { n: idxs.length, auc, topN: topIdx.length, topWR, p, expR, first: first.length >= 10 ? mean(first) : null, second: second.length >= 10 ? mean(second) : null };
}

// ═══════════════════════════════════════════════════════════════════════
function main() {
  const args = {};
  for (let i = 2; i < process.argv.length; i += 2) args[process.argv[i].replace(/^--/, '')] = process.argv[i + 1];
  const costBps = parseFloat(args.cost || String(COST_BPS_DEFAULT));
  const line = '─'.repeat(116);

  console.log(`\n${line}\n  STUDY 22 — CME-X3 (SCOPED SUBSET): 2x2 BARRIER/STRUCTURE DIAGNOSTIC + MODEL TOURNAMENT\n${line}`);
  console.log('  Loading, aligning, building state + cross-asset field...');
  const rawBars = {}; for (const s of COINS) rawBars[s] = loadBars(s);
  const { bars: alignedBars } = alignAll(rawBars);
  const n = alignedBars[COINS[0]].length;
  const state = {}; for (const s of COINS) state[s] = buildAssetState(alignedBars[s]);
  const cross = buildCrossAsset(alignedBars);
  const rows = {}; for (const s of COINS) rows[s] = buildFeatureRows(s, state, cross);
  console.log(`  ${n} aligned bars. Rows per coin: ${COINS.map(s => `${s} ${rows[s].flat.length}`).join(', ')}\n`);

  function outcomesFor(symbol, horizon, dir, alpha, beta) {
    const bars = alignedBars[symbol]; const sigmaMed = state[symbol].sigmaMed;
    const m = new Map(); const fn = dir === 'long' ? longOutcome : shortOutcome;
    for (const r of rows[symbol].flat) { const y = fn(bars, r.i, horizon, sigmaMed[r.i], alpha, beta); if (y != null) m.set(r.i, y); }
    return m;
  }

  // ── PART 1: the 2x2 (barrier x structure) diagnostic from Study 21's open question ──
  console.log(`${line}\n  PART 1 — 2x2 DIAGNOSTIC: which change killed Study 20's BTC/ETH signal?\n${line}`);
  console.log('  Train DOGE+LINK+AVAX, test BTC+ETH+SOL, h=2 (30min) -- the exact cell Study 20 found strongest.\n');
  const diag = [];
  for (const [barrierName, { alpha, beta }] of Object.entries(BARRIERS)) {
    const rewardR = alpha / beta, costR = costBps / 100 / beta, baseline = fairBaseline(alpha, beta);
    for (const structure of ['flat', 'interact']) {
      const trainRows = [];
      for (const s of TEST_SET_A) { const y = outcomesFor(s, FIT_HORIZON, 'long', alpha, beta); for (const r of rows[s][structure]) if (y.has(r.i)) trainRows.push({ i: r.i, x: r.x, y: y.get(r.i) }); }
      const model = fitRidge(trainRows, RIDGE_LAMBDA);
      for (const s of TRAIN_SET_A) {
        const scoreByIdx = predictRidge(rows[s][structure], model);
        const outcomeByIdx = outcomesFor(s, 2, 'long', alpha, beta);
        const res = evaluateDirectional(scoreByIdx, outcomeByIdx, costR, rewardR, 0.10, 30);
        if (res) diag.push({ barrier: barrierName, structure, coin: s, fairBaseline: baseline, ...res });
      }
    }
  }
  console.log(`  ${'barrier'.padEnd(11)}${'structure'.padEnd(11)}${'coin'.padEnd(10)}${'fairWR'.padStart(8)}${'AUC'.padStart(8)}${'topWR'.padStart(8)}${'lift'.padStart(8)}${'expR'.padStart(8)}${'p'.padStart(9)}`);
  for (const d of diag) {
    const lift = d.topWR != null ? (d.topWR - d.fairBaseline) * 100 : null;
    console.log(`  ${d.barrier.padEnd(11)}${d.structure.padEnd(11)}${d.coin.padEnd(10)}${(d.fairBaseline * 100).toFixed(1).padStart(7)}%${(d.auc != null ? d.auc.toFixed(3) : '—').padStart(8)}${(d.topWR != null ? (d.topWR * 100).toFixed(1) + '%' : '—').padStart(8)}${(lift != null ? (lift >= 0 ? '+' : '') + lift.toFixed(1) + 'pp' : '—').padStart(8)}${(d.expR != null ? d.expR.toFixed(3) : '—').padStart(8)}${(d.p != null ? d.p.toFixed(3) : '—').padStart(9)}`);
  }
  console.log('');

  // Pick the winning (barrier, structure) combo by mean AUC lift over 0.5, for the tournament below.
  const byCombo = {};
  for (const d of diag) { const key = `${d.barrier}|${d.structure}`; (byCombo[key] = byCombo[key] || []).push(d.auc || 0.5); }
  let bestCombo = null, bestScore = -Infinity;
  for (const [key, aucs] of Object.entries(byCombo)) { const m = mean(aucs); if (m > bestScore) { bestScore = m; bestCombo = key; } }
  const [winBarrierName, winStructure] = bestCombo.split('|');
  const { alpha: WA, beta: WB } = BARRIERS[winBarrierName];
  console.log(`  Winning combo for Part 2: barrier=${winBarrierName} (alpha=${WA},beta=${WB}), structure=${winStructure} (mean AUC ${bestScore.toFixed(3)})\n`);

  // ── PART 2: model tournament, leave-one-asset-out, h=2, using the winning combo ──
  console.log(`${line}\n  PART 2 — MODEL TOURNAMENT (leave-one-asset-out, h=2, ${winBarrierName} barrier, ${winStructure} structure)\n${line}`);
  const rewardR2 = WA / WB, costR2 = costBps / 100 / WB, baseline2 = fairBaseline(WA, WB);
  console.log(`  Fair (no-edge) baseline win rate for this barrier: ${(baseline2 * 100).toFixed(1)}%\n`);

  const tourney = [];
  for (const held of COINS) {
    const trainSymbols = COINS.filter(c => c !== held);
    // Each row carries its own `.y` -- see fitRidge's comment on why a
    // shared index-keyed Map is unsafe once rows from multiple symbols
    // (whose indices fully overlap, 0..n-1 each) are pooled together.
    const flatRowsLabeled = [], intRowsLabeled = [];
    for (const s of trainSymbols) {
      const y = outcomesFor(s, FIT_HORIZON, 'long', WA, WB);
      for (const r of rows[s].flat) if (y.has(r.i)) flatRowsLabeled.push({ i: r.i, x: r.x, y: y.get(r.i) });
      for (const r of rows[s].interact) if (y.has(r.i)) intRowsLabeled.push({ i: r.i, x: r.x, y: y.get(r.i) });
    }

    const ridgeFlat = fitRidge(flatRowsLabeled, RIDGE_LAMBDA);
    const ridgeInt = fitRidge(intRowsLabeled, RIDGE_LAMBDA);
    const gbm = fitGBM(flatRowsLabeled, 40, 0.1, 2);

    const testFlat = rows[held].flat, testInt = rows[held].interact;
    const outcomeByIdx = outcomesFor(held, 2, 'long', WA, WB);

    const models = {
      random: new Map(testFlat.map(r => [r.i, Math.sin(r.i * 12.9898) * 43758.5453 % 1])),
      momentum: new Map(testFlat.map(r => [r.i, r.x[FLAT_NAMES.indexOf('D')]])),
      meanReversion: new Map(testFlat.map(r => [r.i, -r.x[FLAT_NAMES.indexOf('D')]])),
      persistenceOnly: new Map(testFlat.map(r => [r.i, r.x[FLAT_NAMES.indexOf('P')]])),
      ridgeFlat: predictRidge(testFlat, ridgeFlat),
      ridgeInteract: predictRidge(testInt, ridgeInt),
      gbm: predictGBM(testFlat, gbm)
    };
    for (const [name, scoreByIdx] of Object.entries(models)) {
      const res = evaluateDirectional(scoreByIdx, outcomeByIdx, costR2, rewardR2, 0.10, 30);
      if (res) tourney.push({ model: name, heldOut: held, ...res });
    }
  }
  console.log(`  ${'model'.padEnd(16)}${'heldOut'.padEnd(11)}${'AUC'.padStart(8)}${'topN'.padStart(7)}${'topWR'.padStart(8)}${'lift'.padStart(8)}${'expR'.padStart(8)}${'p'.padStart(9)}`);
  for (const t of tourney) {
    const lift = t.topWR != null ? (t.topWR - baseline2) * 100 : null;
    console.log(`  ${t.model.padEnd(16)}${t.heldOut.padEnd(11)}${(t.auc != null ? t.auc.toFixed(3) : '—').padStart(8)}${String(t.topN).padStart(7)}${(t.topWR != null ? (t.topWR * 100).toFixed(1) + '%' : '—').padStart(8)}${(lift != null ? (lift >= 0 ? '+' : '') + lift.toFixed(1) + 'pp' : '—').padStart(8)}${(t.expR != null ? t.expR.toFixed(3) : '—').padStart(8)}${(t.p != null ? t.p.toFixed(3) : '—').padStart(9)}`);
  }

  // Per-model mean AUC across all 6 held-out coins.
  console.log('\n  Mean AUC by model, across all 6 leave-one-out coins:');
  const byModel = {};
  for (const t of tourney) (byModel[t.model] = byModel[t.model] || []).push(t.auc || 0.5);
  const modelRanking = Object.entries(byModel).map(([m, aucs]) => ({ model: m, meanAUC: mean(aucs) })).sort((a, b) => b.meanAUC - a.meanAUC);
  for (const r of modelRanking) console.log(`    ${r.model.padEnd(16)} ${r.meanAUC.toFixed(4)}`);
  const bestModel = modelRanking[0].model;
  console.log('');

  // ── PART 3: signal vs. persistence decomposition (section 53.24) ──
  console.log(`${line}\n  PART 3 — SIGNAL VS. PERSISTENCE DECOMPOSITION (leave-one-out, BTC+ETH held out)\n${line}`);
  const persistTests = [];
  for (const held of ['BTCUSDT', 'ETHUSDT']) {
    const trainSymbols = COINS.filter(c => c !== held);
    const flatRowsLabeled = [];
    for (const s of trainSymbols) { const y = outcomesFor(s, FIT_HORIZON, 'long', WA, WB); for (const r of rows[s].flat) if (y.has(r.i)) flatRowsLabeled.push({ i: r.i, x: r.x, y: y.get(r.i) }); }
    const pIdx = FLAT_NAMES.indexOf('P');
    // "Signal only" = D,C,V,F,L,X,A,E,RR,SYNC (persistence P zeroed out).
    const signalOnlyRows = flatRowsLabeled.map(r => ({ i: r.i, x: r.x.map((v, j) => j === pIdx ? 0 : v), y: r.y }));
    const signalOnly = fitRidge(signalOnlyRows, RIDGE_LAMBDA);
    // "Persistence only" = a 1-feature ridge on P alone.
    const persistOnlyRows = flatRowsLabeled.map(r => ({ i: r.i, x: [r.x[pIdx]], y: r.y }));
    const persistOnly = fitRidge(persistOnlyRows, RIDGE_LAMBDA);
    // "Both" = the full flat model (already have ridgeFlat-equivalent, refit here for a clean identical split).
    const bothModel = fitRidge(flatRowsLabeled, RIDGE_LAMBDA);
    // "Random-persistence control" = P replaced by a shuffled version of itself (same marginal distribution, no real link to that bar).
    const shuffledP = shuffleValues(flatRowsLabeled.map(r => r.x[pIdx]), 555);
    const controlRows = flatRowsLabeled.map((r, k) => ({ i: r.i, x: r.x.map((v, j) => j === pIdx ? shuffledP[k] : v), y: r.y }));
    const controlModel = fitRidge(controlRows, RIDGE_LAMBDA);

    const testFlat = rows[held].flat;
    const testSignalOnly = testFlat.map(r => ({ i: r.i, x: r.x.map((v, j) => j === pIdx ? 0 : v) }));
    const testPersistOnly = testFlat.map(r => ({ i: r.i, x: [r.x[pIdx]] }));
    const shuffledPTest = shuffleValues(testFlat.map(r => r.x[pIdx]), 777);
    const testControl = testFlat.map((r, k) => ({ i: r.i, x: r.x.map((v, j) => j === pIdx ? shuffledPTest[k] : v) }));

    const outcomeByIdx = outcomesFor(held, 2, 'long', WA, WB);
    const variants = {
      signalOnly: predictRidge(testSignalOnly, signalOnly),
      persistenceOnly: predictRidge(testPersistOnly, persistOnly),
      both: predictRidge(testFlat, bothModel),
      randomPersistenceControl: predictRidge(testControl, controlModel)
    };
    for (const [name, scoreByIdx] of Object.entries(variants)) {
      const res = evaluateDirectional(scoreByIdx, outcomeByIdx, costR2, rewardR2, 0.10, 30);
      if (res) persistTests.push({ variant: name, heldOut: held, ...res });
    }
  }
  console.log(`  ${'variant'.padEnd(26)}${'heldOut'.padEnd(11)}${'AUC'.padStart(8)}${'topWR'.padStart(8)}${'lift'.padStart(8)}${'expR'.padStart(8)}`);
  for (const t of persistTests) {
    const lift = t.topWR != null ? (t.topWR - baseline2) * 100 : null;
    console.log(`  ${t.variant.padEnd(26)}${t.heldOut.padEnd(11)}${(t.auc != null ? t.auc.toFixed(3) : '—').padStart(8)}${(t.topWR != null ? (t.topWR * 100).toFixed(1) + '%' : '—').padStart(8)}${(lift != null ? (lift >= 0 ? '+' : '') + lift.toFixed(1) + 'pp' : '—').padStart(8)}${(t.expR != null ? t.expR.toFixed(3) : '—').padStart(8)}`);
  }
  console.log('');

  // ── PART 4: cost robustness + threshold/selectivity robustness for the best model ──
  console.log(`${line}\n  PART 4 — COST & THRESHOLD ROBUSTNESS (${bestModel}, BTC held out, h=2)\n${line}`);
  const heldForRobustness = 'BTCUSDT';
  const trainSymbolsR = COINS.filter(c => c !== heldForRobustness);
  const flatRowsR = [];
  for (const s of trainSymbolsR) { const y = outcomesFor(s, FIT_HORIZON, 'long', WA, WB); for (const r of rows[s].flat) if (y.has(r.i)) flatRowsR.push({ i: r.i, x: r.x, y: y.get(r.i) }); }
  const modelR = bestModel === 'gbm' ? fitGBM(flatRowsR, 40, 0.1, 2) : fitRidge(flatRowsR, RIDGE_LAMBDA);
  const scoreByIdxR = bestModel === 'gbm' ? predictGBM(rows[heldForRobustness].flat, modelR) : predictRidge(rows[heldForRobustness].flat, modelR);
  const outcomeByIdxR = outcomesFor(heldForRobustness, 2, 'long', WA, WB);

  console.log('  Cost robustness (bps round-trip):');
  for (const cb of [0, 2, 4, 8, 16]) {
    const res = evaluateDirectional(scoreByIdxR, outcomeByIdxR, cb / 100 / WB, WA / WB, 0.10, 30);
    if (res) console.log(`    ${String(cb).padStart(3)}bps  WR=${(res.topWR * 100).toFixed(1)}%  expR=${res.expR.toFixed(3)}`);
  }
  console.log('\n  Threshold/selectivity robustness (top X% by score):');
  for (const frac of [0.01, 0.02, 0.05, 0.10, 0.20, 1.0]) {
    const res = evaluateDirectional(scoreByIdxR, outcomeByIdxR, costR2, rewardR2, frac, frac >= 1 ? 300 : 30);
    if (res) console.log(`    top ${(frac * 100).toFixed(0)}%  n=${String(res.topN).padStart(5)}  WR=${(res.topWR * 100).toFixed(1)}%  expR=${res.expR.toFixed(3)}`);
  }

  // ── FINAL VERDICT (section 53.36) ──
  console.log(`\n${line}\n  FINAL VERDICT\n${line}`);
  const allWithP = tourney.filter(t => t.p != null);
  const { survivors, tested } = S.fdr(allWithP, 0.10);
  const solid = survivors.filter(t => t.topWR > baseline2 && t.expR > 0 && (t.first == null || t.second == null || Math.sign(t.first - baseline2) === Math.sign(t.second - baseline2)));
  console.log(`  Tournament: ${survivors.length}/${tested} pass FDR; ${solid.length} also clear cost floor with stable split-sample sign.`);
  console.log(`  Best model by mean leave-one-out AUC: ${bestModel} (${modelRanking[0].meanAUC.toFixed(4)}).`);
  let verdict;
  if (solid.length >= 4 && modelRanking[0].meanAUC > 0.54) verdict = 'PROMISING BUT NOT YET PROVEN';
  else if (solid.length === 0 || modelRanking[0].meanAUC <= 0.51) verdict = 'NO ROBUST UNIVERSAL INVARIANT FOUND';
  else verdict = 'PROMISING BUT NOT YET PROVEN';
  console.log(`\n  ${verdict}\n`);
  console.log(`${line}\n`);

  fs.mkdirSync(path.join(__dirname, 'results'), { recursive: true });
  fs.writeFileSync(path.join(__dirname, 'results', 'cme-x3-test.json'),
    JSON.stringify({ generatedAt: new Date().toISOString(), costBps, diag, winBarrierName, winStructure, tourney, modelRanking, persistTests, verdict }, null, 2));
}

main();
