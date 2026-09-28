#!/usr/bin/env node
/**
 * STUDY 21 — Testing the uploaded "CME-X2" universal invariant blueprint.
 *
 * CME-X2 is explicitly a revision written in response to Study 20's result
 * (its own "current baseline to beat" in section 44 quotes Study 20's exact
 * numbers back: BTC ensemble ~58.9%, BTC+persistence ~61.3%, ETH ensemble
 * ~56.3%, ETH+persistence ~57.1%). Three structural changes from CME-X,
 * each implemented here:
 *
 *   1. VOLATILITY-RELATIVE TARGETS (section 4). Study 20 used one fixed
 *      +/-0.5% barrier for every coin and every regime. CME-X2 requires
 *      TP/SL scaled to causal realized volatility: B_t(H) = sigma_t *
 *      sqrt(H), TP = alpha*B_t, SL = beta*B_t (alpha=1.0, beta=0.75, the
 *      document's own example -- not re-fit here, for the same
 *      one-thing-at-a-time reason Study 20 fixed its own low-level
 *      sub-weights). Long and short therefore each get their OWN barrier
 *      race, evaluated separately.
 *   2. MARKET POTENTIAL (section 19) and the explicit CORE equation
 *      (section 49): I_t = tanh(w1*D*C*POT + w2*F*D + w3*RR*SYNC +
 *      w4*P*A - w5*X*L + w6*E*C). Study 20 fit a flat blend of 15 raw
 *      features; this fits the 6 named INTERACTION terms CME-X2 itself
 *      proposes as its core equation -- a genuinely different structure,
 *      not a re-run of the same one.
 *   3. SEPARATE LONG/SHORT PROBABILITY MODELS (section 25). CME-X2
 *      explicitly forbids P_S = 1 - P_L. Two independent ridge fits are
 *      run here, one per direction, each against its own success event.
 *
 * Mandatory tests run (sections 31/32/33, all against P_L and P_S, all
 * horizons H in {2,4,8,16,32}):
 *
 *   - UNIVERSAL ASSET TEST: train BTC+ETH+SOL / test DOGE+LINK+AVAX, and
 *     the reverse.
 *   - LEAVE-ONE-ASSET-OUT: for each of the 6 coins, train on the other 5,
 *     test on it alone. Every result reported individually (section 32's
 *     own instruction), not averaged away.
 *   - TIME TRANSFER: 60% train / 20% validation (reported, not used to
 *     tune anything, since the only "hyperparameter" here is a fixed ridge
 *     penalty already frozen before this study) / 20% untouched final test.
 *
 * Same four statistical gates as every other study in this project
 * (moving-block bootstrap significance, FDR, split-half stability, a cost
 * floor), reused verbatim from research/cme-invariant-test.js.
 */
const fs = require('fs');
const path = require('path');
const S = require('./lib-stats.js');

const DATA = path.join(__dirname, '..', 'backtest', 'data');
const COINS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'DOGEUSDT', 'LINKUSDT', 'AVAXUSDT'];
const TRAIN_SET_A = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT'];
const TEST_SET_A = ['DOGEUSDT', 'LINKUSDT', 'AVAXUSDT'];
const D_HORIZONS = [1, 2, 4, 8, 16, 32];   // for the multi-scale momentum blend (section 6)
const H_LIST = [2, 4, 8, 16, 32];          // barrier-race horizons (section 4)
const FIT_HORIZON = 4;
const ALPHA_TP = 1.0, BETA_SL = 0.75;      // section 4's own example, not re-fit (see file header)
const RIDGE_LAMBDA = 5.0;
const COST_BPS_DEFAULT = 4;

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

/** Causal robust z-score against a trailing window (median/MAD). */
/**
 * O(1)-per-step sliding window (mean/std, or covariance between two series).
 * The first implementation of this file recomputed a fresh median/MAD scan
 * over the trailing window from scratch at every bar, at every one of
 * several call sites (F, L, X, and worst of all E, which called it 64 times
 * PER BAR for its entropy window) -- O(n * win) at best, and the entropy
 * path alone was O(n * 64 * win). On 6 coins x ~40,000 bars that never
 * finished in 10 minutes. This replaces every one of those causal Z-scores
 * with an O(1)-amortized running sum/sum-of-squares (mean/std rather than
 * median/MAD -- a real precision-for-speed tradeoff, mitigated by clamping
 * every Z-score to [-4,4] before use everywhere below, same as before).
 */
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

/** Sliding covariance/correlation between two co-indexed series, same O(1) trick. */
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
// PER-ASSET UNIVERSAL STATE S_t = [D, C, V, F, L, X, P, A, E] (section 5;
// R and SYNC are cross-asset, built separately below)
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
    E: new Array(n).fill(null),
    zRets1: new Array(n).fill(null) // reused by both F and the entropy window below
  };

  const SIGMA_WIN = 200;
  const rhWin = {}; for (const h of D_HORIZONS) rhWin[h] = new SlidingMV(SIGMA_WIN);
  const sigFastWin = new SlidingMV(16), sigMedWin = new SlidingMV(64), sigSlowWin = new SlidingMV(128);
  const r1Win = new SlidingMV(200), volWin = new SlidingMV(200);
  const rangeWin = new SlidingMV(200), gapWin = new SlidingMV(200), volAnomWin = new SlidingMV(200);
  const volExpWin = new SlidingMV(200);

  for (let i = 0; i < n; i++) {
    // ── D: multi-scale directional pressure, C: coherence (sections 6-7) ──
    // Each rh[h] window is pushed with the PRIOR bar's value before being
    // read at bar i (causal: the window covers i-SIGMA_WIN..i-1).
    if (i > 0) for (const h of D_HORIZONS) if (rh[h][i - 1] != null) rhWin[h].push(rh[h][i - 1]);
    if (i >= 32 + SIGMA_WIN) {
      let sumD = 0, cnt = 0, signSum = 0;
      for (const h of D_HORIZONS) {
        const r = rh[h][i];
        if (r == null || !rhWin[h].ready(40)) continue;
        const s = rhWin[h].std() || 1e-9;
        sumD += tanh(r / s);
        signSum += Math.sign(r);
        cnt++;
      }
      if (cnt === D_HORIZONS.length) { out.D[i] = sumD / cnt; out.C[i] = Math.abs(signSum / D_HORIZONS.length); }
    }

    // ── Volatility geometry (section 8) ── windows pushed with rets1[i] itself
    // (using the CURRENT bar's realised move as part of "current" volatility,
    // same convention Study 20 used for sigma).
    if (rets1[i] != null) { sigFastWin.push(rets1[i]); sigMedWin.push(rets1[i]); sigSlowWin.push(rets1[i]); }
    if (i >= 128) {
      out.sigmaFast[i] = sigFastWin.std(); out.sigmaMed[i] = sigMedWin.std(); out.sigmaSlow[i] = sigSlowWin.std();
      if (out.sigmaFast[i] && out.sigmaMed[i] && out.sigmaSlow[i]) {
        const vr1 = out.sigmaFast[i] / out.sigmaMed[i], vr2 = out.sigmaMed[i] / out.sigmaSlow[i], vr3 = out.sigmaFast[i] / out.sigmaSlow[i];
        out.V[i] = (vr1 + vr2 + vr3) / 3;
        out.volExpansion[i] = out.sigmaFast[i] - out.sigmaSlow[i];
      }
    }

    // ── Flow/volume pressure F = Z(r_1)*Z(volume) (section 9) ──
    const zR1 = r1Win.ready(40) ? r1Win.z(rets1[i] != null ? rets1[i] : r1Win.mean()) : null;
    out.zRets1[i] = zR1;
    const zVol = volWin.ready(40) ? volWin.z(logVol[i]) : null;
    if (zR1 != null && zVol != null) out.F[i] = clamp(zR1, -4, 4) * clamp(zVol, -4, 4);
    if (rets1[i] != null) r1Win.push(rets1[i]);
    volWin.push(logVol[i]);

    // ── Liquidity stress (section 11) ──
    const c = bars[i];
    const rangePct = (c.high - c.low) / c.close;
    const gapPct = i > 0 ? Math.abs(c.close - bars[i - 1].close) / bars[i - 1].close : null;
    const zRange = rangeWin.ready(40) ? rangeWin.z(rangePct) : null;
    const zGap = (gapPct != null && gapWin.ready(40)) ? gapWin.z(gapPct) : null;
    const zVolAnom = volAnomWin.ready(40) ? volAnomWin.z(c.volume) : null;
    if (zRange != null && zGap != null && zVolAnom != null) {
      out.L[i] = tanh(clamp(zRange, -4, 4) + clamp(zGap, -4, 4) + clamp(zVolAnom, -4, 4));
    }
    rangeWin.push(rangePct); if (gapPct != null) gapWin.push(gapPct); volAnomWin.push(c.volume);

    // ── Persistence (section 16): mean of Persistence_k for k in {1,2,4,8}, on D ──
    if (out.D[i] != null && i >= 9) {
      const pks = [];
      for (const k of [1, 2, 4, 8]) {
        let acc = 0, cnt2 = 0;
        for (let j = 0; j < k; j++) { if (out.D[i - j] == null) continue; acc += Math.sign(out.D[i - j]) * Math.sign(out.D[i]); cnt2++; }
        if (cnt2 === k) pks.push(acc / k);
      }
      if (pks.length === 4) out.P[i] = mean(pks);
    }

    // ── Acceleration (section 17) ──
    if (out.D[i] != null && out.D[i - 1] != null) out.A[i] = out.D[i] - out.D[i - 1];

    // ── Exhaustion (section 18): tanh(|D| * persistence * volatility_expansion) ──
    const volExpZ = volExpWin.ready(40) && out.volExpansion[i] != null ? volExpWin.z(out.volExpansion[i]) : null;
    if (out.D[i] != null && out.P[i] != null && volExpZ != null) out.X[i] = tanh(Math.abs(out.D[i]) * out.P[i] * clamp(volExpZ, -4, 4));
    if (out.volExpansion[i] != null) volExpWin.push(out.volExpansion[i]);

    // ── Entropy of the distribution of recent normalised returns (section 15) ──
    // Reuses zRets1 (already computed above, O(1) per bar) instead of
    // recomputing a fresh causal Z-score inside this window -- this was the
    // single worst offender in the original O(n*64*win) version.
    if (i >= 64) {
      const bins = [0, 0, 0, 0, 0];
      let total = 0;
      for (let j = i - 63; j <= i; j++) {
        const z = out.zRets1[j];
        if (z == null) continue;
        total++;
        if (z < -1.5) bins[0]++; else if (z < -0.5) bins[1]++; else if (z < 0.5) bins[2]++; else if (z < 1.5) bins[3]++; else bins[4]++;
      }
      if (total >= 40) {
        let ent = 0;
        for (const b of bins) { const p = b / total; if (p > 0) ent -= p * Math.log(p); }
        out.E[i] = ent / Math.log(5);
      }
    }
  }
  return out;
}

// ═══════════════════════════════════════════════════════════════════════
// CROSS-ASSET FIELD: market factor + causal beta + residual (R), SYNC
// (sections 12-14)
// ═══════════════════════════════════════════════════════════════════════
function buildCrossAsset(alignedBars) {
  const symbols = Object.keys(alignedBars);
  const n = alignedBars[symbols[0]].length;
  const rets1 = {};
  for (const s of symbols) {
    rets1[s] = new Array(n).fill(null);
    for (let i = 1; i < n; i++) rets1[s][i] = Math.log(alignedBars[s][i].close / alignedBars[s][i - 1].close);
  }
  const marketReturn = new Array(n).fill(null);
  for (let i = 0; i < n; i++) {
    const rs = symbols.map(s => rets1[s][i]).filter(v => v != null);
    if (rs.length === symbols.length) marketReturn[i] = median(rs);
  }

  const RR = {}; for (const s of symbols) RR[s] = new Array(n).fill(null);
  const SYNC = new Array(n).fill(null);
  const BETA_WIN = 200, SYNC_WIN = 64;

  const betaCov = {}; for (const s of symbols) betaCov[s] = new SlidingCov(BETA_WIN);
  const pairCov = [];
  for (let a = 0; a < symbols.length; a++) for (let b = a + 1; b < symbols.length; b++) pairCov.push({ a: symbols[a], b: symbols[b], cov: new SlidingCov(SYNC_WIN) });

  for (let i = 0; i < n; i++) {
    // Beta/residual: window covers i-BETA_WIN..i-1 (pushed with i-1's values
    // before being read at i), current bar's own return is the "outcome".
    if (i > 0 && marketReturn[i - 1] != null) for (const s of symbols) if (rets1[s][i - 1] != null) betaCov[s].push(rets1[s][i - 1], marketReturn[i - 1]);
    if (marketReturn[i] != null) {
      for (const s of symbols) {
        if (rets1[s][i] == null) continue;
        const st = betaCov[s].stats();
        if (!st || st.n < 40 || !st.varB) continue;
        const beta = st.cov / st.varB;
        const resid = rets1[s][i] - beta * marketReturn[i];
        // var(r - beta*m) derived from the SAME running moments, closed-form
        // -- no second pass over the window needed.
        const residVar = st.varA - 2 * beta * st.cov + beta * beta * st.varB;
        const residSd = Math.sqrt(Math.max(0, residVar)) || 1e-9;
        RR[s][i] = clamp(resid / residSd, -4, 4);
      }
    }

    // Synchronization: window covers i-SYNC_WIN..i-1, same causal convention.
    if (i > 0) for (const pc of pairCov) if (rets1[pc.a][i - 1] != null && rets1[pc.b][i - 1] != null) pc.cov.push(rets1[pc.a][i - 1], rets1[pc.b][i - 1]);
    let corrSum = 0, corrCnt = 0;
    for (const pc of pairCov) {
      const st = pc.cov.stats();
      if (st && st.n >= SYNC_WIN && st.corr != null) { corrSum += st.corr; corrCnt++; }
    }
    if (corrCnt) SYNC[i] = corrSum / corrCnt;
  }
  return { RR, SYNC };
}

// ═══════════════════════════════════════════════════════════════════════
// MARKET POTENTIAL (section 19/50) + THE 6 NAMED CORE-EQUATION TERMS
// (section 49): D*C*POT, F*D, RR*SYNC, P*A, X*L, E*C
// ═══════════════════════════════════════════════════════════════════════
const CORE_TERM_NAMES = ['D_C_POT', 'F_D', 'RR_SYNC', 'P_A', 'X_L', 'E_C'];

function buildCoreTerms(symbol, state, cross) {
  const st = state[symbol];
  const n = st.D.length;
  const rows = [];
  for (let i = 0; i < n; i++) {
    const { D, C, F, L, X, P, A, E } = { D: st.D[i], C: st.C[i], F: st.F[i], L: st.L[i], X: st.X[i], P: st.P[i], A: st.A[i], E: st.E[i] };
    const RR = cross.RR[symbol][i], SYNC = cross.SYNC[i];
    if ([D, C, F, L, X, P, A, E, RR, SYNC].some(v => v == null || !isFinite(v))) continue;
    const POT = tanh(Math.abs(D) * C * (1 + F) / (1 + Math.abs(X) + Math.abs(L)));
    const terms = [D * C * POT, F * D, RR * SYNC, P * A, -X * L, E * C];
    rows.push({ i, x: terms });
  }
  return rows;
}

// ═══════════════════════════════════════════════════════════════════════
// VOLATILITY-RELATIVE BARRIER TARGET (section 4) -- long and short each get
// their OWN, independently-timed race, per the document's explicit ban on
// P_S = 1 - P_L.
// ═══════════════════════════════════════════════════════════════════════
// sigma_t is passed in directly as state[symbol].sigmaMed[i] (the 64-bar
// realized vol of 1-bar log returns from buildAssetState) -- a defensible
// single choice for "causal realized volatility" rather than introducing
// yet another free parameter to pick between sigmaFast/sigmaMed/sigmaSlow.
function longOutcome(bars, i, horizon, sigma) {
  if (!sigma) return null;
  const B = sigma * Math.sqrt(horizon);
  const entry = bars[i].close;
  const tp = entry * (1 + ALPHA_TP * B), sl = entry * (1 - BETA_SL * B);
  for (let k = i + 1; k <= Math.min(i + horizon, bars.length - 1); k++) {
    const hitTp = bars[k].high >= tp, hitSl = bars[k].low <= sl;
    if (hitTp && hitSl) return null; // ambiguous within-bar
    if (hitTp) return 1;
    if (hitSl) return 0;
  }
  return null; // timeout -- excluded, never silently scored (section 4's own rule)
}

function shortOutcome(bars, i, horizon, sigma) {
  if (!sigma) return null;
  const B = sigma * Math.sqrt(horizon);
  const entry = bars[i].close;
  const tp = entry * (1 - ALPHA_TP * B), sl = entry * (1 + BETA_SL * B);
  for (let k = i + 1; k <= Math.min(i + horizon, bars.length - 1); k++) {
    const hitTp = bars[k].low <= tp, hitSl = bars[k].high >= sl;
    if (hitTp && hitSl) return null;
    if (hitTp) return 1;
    if (hitSl) return 0;
  }
  return null;
}

// ═══════════════════════════════════════════════════════════════════════
// RIDGE FIT (verbatim mechanism from Study 20 -- only the feature set differs)
// ═══════════════════════════════════════════════════════════════════════
// CORRECTION (see research/README.md's correction note): rows must carry
// their own `.y` embedded rather than being looked up via a shared
// `Map<index, y>` keyed by `r.i` alone -- every coin's rows use the same
// 0..n-1 index range into the aligned timeline, so pooling multiple coins
// into one lookup map let the last-processed coin overwrite every earlier
// coin's label at each shared index. Affected the Universal Asset Tests
// and leave-one-asset-out (multi-coin training); not the Time Transfer
// Test (single coin per fit, no collision possible).
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

function predict(rows, model) {
  const out = new Map();
  for (const r of rows) {
    let s = 0;
    for (let j = 0; j < r.x.length; j++) s += model.w[j] * ((r.x[j] - model.mu[j]) / model.sigma[j]);
    out.set(r.i, tanh(s));
  }
  return out;
}

// ═══════════════════════════════════════════════════════════════════════
// EVALUATION HARNESS (verbatim from Study 20)
// ═══════════════════════════════════════════════════════════════════════
function computeAUC(scores, labels) {
  const pos = [], neg = [];
  for (let i = 0; i < scores.length; i++) (labels[i] ? pos : neg).push(scores[i]);
  if (!pos.length || !neg.length) return null;
  const all = scores.map((s, i) => ({ s, l: labels[i] })).sort((a, b) => a.s - b.s);
  let rankSum = 0, idx = 0;
  while (idx < all.length) {
    let j = idx; while (j < all.length && all[j].s === all[idx].s) j++;
    const avgRank = (idx + 1 + j) / 2;
    for (let k = idx; k < j; k++) if (all[k].l) rankSum += avgRank;
    idx = j;
  }
  return (rankSum - pos.length * (pos.length + 1) / 2) / (pos.length * neg.length);
}

function blockBootstrapP(outcomes, blockLen, iters = 300) {
  const n = outcomes.length;
  if (n < 30) return null;
  const observed = mean(outcomes) - 0.5;
  let seed = 313131;
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
  const n = scores.length;
  const topMask = new Array(n).fill(false);
  const REFRESH = 200;
  const seen = []; let hi = null, sinceRefresh = 0;
  for (let i = 0; i < n; i++) {
    if (scores[i] != null && seen.length >= minHistory) {
      if (hi == null || sinceRefresh >= REFRESH) { const sorted = seen.slice().sort((a, b) => a - b); hi = sorted[Math.floor(sorted.length * (1 - frac))]; sinceRefresh = 0; }
      if (scores[i] >= hi) topMask[i] = true;
    }
    if (scores[i] != null) { seen.push(scores[i]); sinceRefresh++; }
  }
  return topMask;
}

/** Evaluates a directional (LONG or SHORT) score against its OWN success
 * outcome (never the symmetric complement). expR is in units of the SL
 * distance, since alpha != beta makes 1R ambiguous otherwise: a win pays
 * alpha/beta R, a loss costs 1R. */
function evaluateDirectional(scoreByIdx, outcomeByIdx, costR) {
  const idxs = [...scoreByIdx.keys()].filter(i => outcomeByIdx.has(i));
  if (idxs.length < 300) return null;
  const scoresArr = new Array(Math.max(...idxs) + 1).fill(null);
  for (const i of idxs) scoresArr[i] = scoreByIdx.get(i);
  const auc = computeAUC(idxs.map(i => scoreByIdx.get(i)), idxs.map(i => outcomeByIdx.get(i)));
  const topMask = causalTopMask(scoresArr, 0.10, 200);
  const topIdx = idxs.filter(i => topMask[i]);
  if (topIdx.length < 30) return { n: idxs.length, auc, topN: topIdx.length, topWR: null };
  const topWR = mean(topIdx.map(i => outcomeByIdx.get(i)));
  const rewardR = ALPHA_TP / BETA_SL; // a win reaches alpha*B; a loss costs beta*B -- ratio in units of SL
  const expR = topWR * rewardR - (1 - topWR) * 1 - costR;
  const sorted = topIdx.slice().sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  const first = sorted.slice(0, mid).map(i => outcomeByIdx.get(i));
  const second = sorted.slice(mid).map(i => outcomeByIdx.get(i));
  const blockLen = 24;
  const p = blockBootstrapP(topIdx.map(i => outcomeByIdx.get(i)), blockLen);
  return {
    n: idxs.length, auc, topN: topIdx.length, topWR, p, expR,
    first: first.length >= 10 ? mean(first) : null, second: second.length >= 10 ? mean(second) : null
  };
}

// ═══════════════════════════════════════════════════════════════════════
function main() {
  const args = {};
  for (let i = 2; i < process.argv.length; i += 2) args[process.argv[i].replace(/^--/, '')] = process.argv[i + 1];
  const costBps = parseFloat(args.cost || String(COST_BPS_DEFAULT));
  const line = '─'.repeat(114);

  console.log(`\n${line}\n  STUDY 21 — TESTING THE UPLOADED "CME-X2" UNIVERSAL INVARIANT BLUEPRINT\n${line}`);
  console.log(`  6 coins: ${COINS.join(', ')} · volatility-relative barrier (alpha=${ALPHA_TP}, beta=${BETA_SL}) · H = ${H_LIST.join('/')} bars (15m)`);
  console.log(`  Core equation under test (section 49): I = tanh(w1*D*C*POT + w2*F*D + w3*RR*SYNC + w4*P*A - w5*X*L + w6*E*C)`);
  console.log(`  Separate P_LONG / P_SHORT models (section 25). Cost floor: ${costBps}bps.\n`);

  console.log('  Loading, aligning, building universal state + cross-asset field...');
  const rawBars = {}; for (const s of COINS) rawBars[s] = loadBars(s);
  const { bars: alignedBars } = alignAll(rawBars);
  const n = alignedBars[COINS[0]].length;
  const state = {}; for (const s of COINS) state[s] = buildAssetState(alignedBars[s]);
  const cross = buildCrossAsset(alignedBars);
  const coreRows = {}; for (const s of COINS) coreRows[s] = buildCoreTerms(s, state, cross);
  console.log(`  ${n} aligned bars. Core-term rows per coin: ${COINS.map(s => `${s} ${coreRows[s].length}`).join(', ')}\n`);

  function outcomesFor(symbol, horizon, dir) {
    const bars = alignedBars[symbol];
    const sigmaMed = state[symbol].sigmaMed;
    const m = new Map();
    const fn = dir === 'long' ? longOutcome : shortOutcome;
    for (const r of coreRows[symbol]) {
      const y = fn(bars, r.i, horizon, sigmaMed[r.i]);
      if (y != null) m.set(r.i, y);
    }
    return m;
  }

  const costR = costBps / 100 / BETA_SL; // cost in bps -> fraction -> units of SL distance (a rough scale conversion, documented)

  function runOneDirection(dir, trainSymbols, testSymbols, label) {
    const results = [];
    for (const h of H_LIST) {
      const trainRows = [];
      for (const s of trainSymbols) { const y = outcomesFor(s, FIT_HORIZON, dir); for (const r of coreRows[s]) if (y.has(r.i)) trainRows.push({ i: r.i, x: r.x, y: y.get(r.i) }); }
      if (trainRows.length < 500) continue;
      const model = fitRidge(trainRows, RIDGE_LAMBDA);
      for (const s of testSymbols) {
        const scoreByIdx = predict(coreRows[s], model);
        const outcomeByIdx = outcomesFor(s, h, dir);
        const res = evaluateDirectional(scoreByIdx, outcomeByIdx, costR);
        if (res) results.push({ coin: s, horizon: h, dir, ...res, weights: model.w });
      }
    }
    return results;
  }

  function report(results) {
    if (!results.length) { console.log('  (nothing cleared the n>=300 / topN>=30 floor)\n'); return; }
    console.log(`  ${'coin'.padEnd(10)}${'dir'.padEnd(6)}${'h'.padStart(3)}${'AUC'.padStart(8)}${'topN'.padStart(7)}${'topWR'.padStart(8)}${'p'.padStart(9)}${'expR'.padStart(8)}${'1st/2nd'.padStart(14)}`);
    for (const t of results) {
      const split = (t.first != null && t.second != null) ? `${(t.first * 100).toFixed(0)}%/${(t.second * 100).toFixed(0)}%` : '—';
      console.log(`  ${t.coin.padEnd(10)}${t.dir.padEnd(6)}${String(t.horizon).padStart(3)}${(t.auc != null ? t.auc.toFixed(3) : '—').padStart(8)}${String(t.topN).padStart(7)}${(t.topWR != null ? (t.topWR * 100).toFixed(1) + '%' : '—').padStart(8)}${(t.p != null ? t.p.toFixed(3) : '—').padStart(9)}${(t.expR != null ? t.expR.toFixed(3) : '—').padStart(8)}${split.padStart(14)}`);
    }
    console.log('');
  }

  // ── Section 31: Universal Asset Test ──
  console.log(`${line}\n  UNIVERSAL ASSET TEST A — train BTC+ETH+SOL, test DOGE+LINK+AVAX\n${line}`);
  const uA_long = runOneDirection('long', TRAIN_SET_A, TEST_SET_A);
  const uA_short = runOneDirection('short', TRAIN_SET_A, TEST_SET_A);
  report(uA_long); report(uA_short);
  if (uA_long[0]) console.log(`  LONG fitted weights (${CORE_TERM_NAMES.join(',')}): ${uA_long[0].weights.map(w => w.toFixed(3)).join(', ')}\n`);

  console.log(`${line}\n  UNIVERSAL ASSET TEST B — train DOGE+LINK+AVAX, test BTC+ETH+SOL\n${line}`);
  const uB_long = runOneDirection('long', TEST_SET_A, TRAIN_SET_A);
  const uB_short = runOneDirection('short', TEST_SET_A, TRAIN_SET_A);
  report(uB_long); report(uB_short);
  if (uB_long[0]) console.log(`  LONG fitted weights (${CORE_TERM_NAMES.join(',')}): ${uB_long[0].weights.map(w => w.toFixed(3)).join(', ')}\n`);

  // ── Section 32: Leave-one-asset-out, every coin individually ──
  console.log(`${line}\n  LEAVE-ONE-ASSET-OUT — train on the other 5, test on each coin alone\n${line}`);
  const looResults = [];
  for (const held of COINS) {
    const trainSymbols = COINS.filter(c => c !== held);
    const rL = runOneDirection('long', trainSymbols, [held]);
    const rS = runOneDirection('short', trainSymbols, [held]);
    looResults.push(...rL, ...rS);
  }
  report(looResults);

  // ── Section 33: Time transfer, 60/20/20 per coin ──
  console.log(`${line}\n  TIME TRANSFER TEST — per coin, train first 60%, report on the final 20% (untouched)\n${line}`);
  const timeResults = [];
  for (const s of COINS) {
    const rows = coreRows[s];
    const cut1 = Math.floor(rows.length * 0.6), cut2 = Math.floor(rows.length * 0.8);
    const trainRows = rows.slice(0, cut1);
    const testRows = rows.slice(cut2); // the 20% validation slice (cut1..cut2) is intentionally not used for anything
    for (const dir of ['long', 'short']) {
      const y = outcomesFor(s, FIT_HORIZON, dir);
      const trainRowsF = trainRows.filter(r => y.has(r.i)).map(r => ({ i: r.i, x: r.x, y: y.get(r.i) }));
      if (trainRowsF.length < 500) continue;
      const model = fitRidge(trainRowsF, RIDGE_LAMBDA);
      const scoreByIdx = predict(testRows, model);
      for (const h of H_LIST) {
        const outcomeByIdx = outcomesFor(s, h, dir);
        const res = evaluateDirectional(scoreByIdx, outcomeByIdx, costR);
        if (res) timeResults.push({ coin: s, horizon: h, dir, ...res });
      }
    }
  }
  report(timeResults);

  // ── FDR across everything ──
  const all = [...uA_long, ...uA_short, ...uB_long, ...uB_short, ...looResults, ...timeResults].filter(t => t.p != null);
  const { survivors, tested, threshold } = S.fdr(all, 0.10);
  console.log(`${line}\n  FDR ACROSS ALL ${tested} TESTS (both universal-asset experiments, leave-one-out x6, time-transfer, both directions, all horizons)\n${line}`);
  if (!survivors.length) {
    console.log('  Nothing survived FDR correction.\n');
  } else {
    const solid = survivors.filter(s => s.topWR > 0.5 && s.expR > 0 && (s.first == null || s.second == null || Math.sign(s.first - 0.5) === Math.sign(s.second - 0.5)));
    console.log(`  ${survivors.length} of ${tested} passed FDR (p <= ${threshold.toExponential(2)}); ${solid.length} also clear the cost floor with a stable split-sample sign.\n`);
    for (const t of solid) console.log(`    ${t.coin.padEnd(10)}${t.dir.padEnd(6)} h=${String(t.horizon).padStart(2)}  n=${String(t.topN).padStart(5)}  WR=${(t.topWR * 100).toFixed(1)}%  expR=${t.expR.toFixed(3)}  p=${t.p.toExponential(1)}`);
  }

  // ── Against CME-X2's own stated benchmarks (section 43) ──
  console.log(`\n${line}\n  AGAINST CME-X2's OWN BENCHMARKS (section 43): 55% weak / 58-61% current baseline / >62% target\n${line}`);
  const byCoin = {};
  for (const t of [...uA_long, ...uA_short, ...uB_long, ...uB_short]) {
    if (t.topWR == null) continue;
    byCoin[t.coin] = byCoin[t.coin] || [];
    byCoin[t.coin].push(t.topWR);
  }
  for (const c of COINS) {
    if (!byCoin[c]) continue;
    const best = Math.max(...byCoin[c]);
    const verdict = best >= 0.62 ? 'MEETS TARGET' : best >= 0.58 ? 'AT BASELINE' : best >= 0.55 ? 'WEAK' : 'RANDOM';
    console.log(`  ${c.padEnd(10)} best cross-asset-tested WR: ${(best * 100).toFixed(1)}%  -> ${verdict}`);
  }

  console.log(`\n${line}\n`);
  fs.mkdirSync(path.join(__dirname, 'results'), { recursive: true });
  fs.writeFileSync(path.join(__dirname, 'results', 'cme-x2-test.json'),
    JSON.stringify({ generatedAt: new Date().toISOString(), costBps, ridgeLambda: RIDGE_LAMBDA, alpha: ALPHA_TP, beta: BETA_SL, coreTermNames: CORE_TERM_NAMES, uA_long, uA_short, uB_long, uB_short, looResults, timeResults }, null, 2));
}

main();
