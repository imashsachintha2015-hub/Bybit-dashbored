#!/usr/bin/env node
/**
 * STUDY 20 — Testing two user-supplied "universal crypto market equation"
 * specifications: CME-Ultimate and CME-X.
 *
 * Both documents describe the same architecture (CME-X's own section 48 says
 * as much: "the structure itself discovered from data... the final discovered
 * equation may be completely different" from its own worked example) — a
 * multi-scale momentum/coherence/regime core, a cross-asset field (breadth,
 * relative value, lead-lag, synchronization, entropy), a temporal-geometry
 * layer (persistence, acceleration, exhaustion), a liquidity-stress
 * discount, and a derivatives-reflexivity term, all combined and passed
 * through tanh. Both explicitly demand the same decisive test before
 * trusting any of it (CME-X section 22, called "the most important test"):
 * train on one set of coins, test on a COMPLETELY DIFFERENT set, and reject
 * anything that doesn't generalize.
 *
 * This script builds the shared core structure from real OHLCV (all fields
 * both documents mark UNIVERSAL — momentum, coherence, regime, absorption,
 * cross-asset breadth/relative-value/lead-lag/sync/entropy, persistence,
 * acceleration, exhaustion, liquidity stress) across all 6 coins the CME-X
 * spec names (BTC/ETH/SOL/DOGE/LINK/AVAX), fits ONE ridge-regularised linear
 * combination on it (per both documents' own MDL principle: discover
 * structure with fixed, non-overfit sub-weights first — see the code
 * comments below for exactly which weights are fixed vs fitted), and runs:
 *
 *   1. CROSS-ASSET INVARIANCE (CME-X section 22, "the most important test").
 *      Fit on BTC+ETH+SOL, freeze the weights, test on DOGE+LINK+AVAX never
 *      seen during fit -- and the reverse. A real invariant should survive
 *      this; this project's own research (Part XIII) already found a
 *      similar signal did NOT.
 *   2. TIME INVARIANCE (CME-X section 23). Same-asset train/test split in
 *      time only, to separate "fails because of a different asset" from
 *      "fails because of a different period".
 *   3. The same four gates as every other study here: HAC-consistent
 *      significance (a moving-block bootstrap, since a barrier-race outcome
 *      is heavily overlapping -- Part II's exact false-positive trap),
 *      FDR correction, split-half stability, and a cost floor.
 *
 * The derivatives-reflexivity term (RF: OFI x OI x funding) is NOT included
 * in this core test -- it needs derivatives history, which OKX only retains
 * for ~30 days (this project's own recurring caveat since Part VI). Folding
 * a 30-day-bounded term into a 400+-day cross-asset/cross-time test would
 * silently gate the whole test's sample size down to whatever the shortest
 * feature allows. It is tested separately, on its own honestly-labeled
 * short window, matching this project's established practice (Part X's
 * liquidation-feed handling).
 *
 * WHAT IS FIXED VS FITTED, stated up front because both source documents
 * ask for "learnable" coefficients everywhere and that is not what happens
 * here for all of them:
 *   FIXED (equal-weight or the documents' own literal formula, not fit to
 *   this data): the K={1..128} momentum blend weights w_k, the regime-gate
 *   blend (EFF/VA/COMP), the liquidity-stress blend (LIQ1/LIQ2/LIQ3), the
 *   lead-lag lag weights. Fitting all of these AND the top-level combination
 *   from ~4M rows of 6-asset history without a held-out test would be
 *   exactly the "immediately optimize thousands of coefficients" both
 *   documents themselves warn against (CME-X section 20).
 *   FITTED (ridge regression, expanding-window on TRAIN data only): the
 *   top-level combination weights across the ~17 named state features into
 *   one score -- this is where "let the data decide the weighting" actually
 *   happens, and it is the only place a train/test boundary matters.
 */
const fs = require('fs');
const path = require('path');
const S = require('./lib-stats.js');

const DATA = path.join(__dirname, '..', 'backtest', 'data');
const COINS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'DOGEUSDT', 'LINKUSDT', 'AVAXUSDT'];
const TRAIN_SET_A = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT'];
const TEST_SET_A = ['DOGEUSDT', 'LINKUSDT', 'AVAXUSDT'];
const K = [1, 2, 4, 8, 16, 32, 64, 128];
const HORIZONS = [2, 4, 8, 16]; // bars on 15m: 30min, 1h, 2h, 4h
const FIT_HORIZON = 4;          // the single horizon the ridge is fit against; all 4 are still evaluated
const BARRIER = 0.005;          // +/-0.5%, same convention as Study 15
const RIDGE_LAMBDA = 5.0;

function mean(a) { return a.length ? a.reduce((x, y) => x + y, 0) / a.length : 0; }
function sd(a) { if (a.length < 2) return 0; const m = mean(a); return Math.sqrt(a.reduce((x, y) => x + (y - m) ** 2, 0) / (a.length - 1)); }
function median(a) { const s = a.slice().sort((x, y) => x - y); return s[Math.floor(s.length / 2)]; }
function mad(a) { const m = median(a); return median(a.map(v => Math.abs(v - m))) * 1.4826; }
function tanh(x) { return Math.tanh(x); }
function sigmoid(x) { return 1 / (1 + Math.exp(-x)); }
function clamp(x, lo, hi) { return Math.max(lo, Math.min(hi, x)); }

// ─── Load and time-align all 6 coins ───
function loadBars(symbol) {
  const f = path.join(DATA, `${symbol}.json`);
  if (!fs.existsSync(f)) throw new Error(`missing ${f} -- run: node backtest/fetch-klines.js ${symbol} 15 40000`);
  return JSON.parse(fs.readFileSync(f, 'utf8')).series['15'];
}

/** Inner-join all coins' bars on the `start` timestamp so cross-asset features
 * (breadth, sync, relative value...) compare the same instant everywhere. */
function alignAll(barsBySymbol) {
  const symbols = Object.keys(barsBySymbol);
  const maps = symbols.map(s => { const m = new Map(); for (const b of barsBySymbol[s]) m.set(b.start, b); return m; });
  const common = [...maps[0].keys()].filter(ts => maps.every(m => m.has(ts))).sort((a, b) => a - b);
  const out = {};
  symbols.forEach((s, si) => { out[s] = common.map(ts => maps[si].get(ts)); });
  return { timestamps: common, bars: out };
}

// ═══════════════════════════════════════════════════════════════════════
// PER-ASSET UNIVERSAL FEATURES (Parts III-VIII / sections 4-11, price+volume
// only -- everything both documents mark as available to every asset)
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

  // Rolling sigma per horizon k, needed to normalise R_k into MR_k=tanh(R_k/sigma_k).
  const sigmaWin = 200;
  const rk = {}; for (const k of K) rk[k] = new Array(n).fill(null);
  for (const k of K) for (let i = k; i < n; i++) rk[k][i] = Math.log(closes[i] / closes[i - k]);

  for (let i = 0; i < n; i++) {
    // ── Multi-scale momentum field: M = mean(tanh(R_k/sigma_k)), equal weight
    // (fixed, not fit -- see file header) ──
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
      if (cnt === K.length) {
        out.M[i] = sumMR / cnt;
        out.COH[i] = Math.abs(sumSign / cnt);
      }
    }

    // ── Volatility geometry / regime gate ──
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
      // COMP = -Z(VR); reuse the VA window's spread as a stand-in scale for VR's
      // own z-score to avoid a second 200-bar rescan per bar.
      const histVR2 = [];
      for (let j = Math.max(0, i - 200); j < i; j++) if (out.VR[j] != null) histVR2.push(out.VR[j]);
      const mv = mean(histVR2), sv = sd(histVR2) || 1e-9;
      const comp = -((out.VR[i] - mv) / sv);
      // r1=r2=r3=1/3, fixed (see file header).
      const reg = (out.EFF[i] + tanh(out.VA[i]) + tanh(comp)) / 3;
      out.TREND_GATE[i] = sigmoid(reg);
    }

    // ── Volume / absorption ──
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

    // ── Liquidity stress: LIQ1=Z(Range/Volume), LIQ2=Z(|Ret|/Volume), LIQ3=Z(volatility) ──
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
      if (parts.length === 3) out.LIQ[i] = sigmoid(mean(parts)); // l1=l2=l3=1/3, fixed
    }
  }
  return out;
}

// ═══════════════════════════════════════════════════════════════════════
// CROSS-ASSET FIELD (Part IX-XIII / sections 12-16) -- needs all 6 aligned
// ═══════════════════════════════════════════════════════════════════════
function buildCrossAsset(alignedBars, perAsset) {
  const symbols = Object.keys(alignedBars);
  const n = alignedBars[symbols[0]].length;
  const out = {};
  for (const s of symbols) out[s] = { BREADTH_M: new Array(n).fill(null), RV: new Array(n).fill(null), LL: new Array(n).fill(null) };
  const BREADTH = new Array(n).fill(null), BA = new Array(n).fill(null);
  const SYNC = new Array(n).fill(null), SYNC_A = new Array(n).fill(null);
  const H = new Array(n).fill(null), DH = new Array(n).fill(null);

  // Causal liquidity weight per coin per bar: rolling median dollar volume
  // over the trailing 200 bars, using only past data.
  const dollarVol = {};
  for (const s of symbols) dollarVol[s] = alignedBars[s].map(b => b.close * b.volume);

  const rets1 = {};
  for (const s of symbols) {
    rets1[s] = new Array(n).fill(null);
    for (let i = 1; i < n; i++) rets1[s][i] = Math.log(alignedBars[s][i].close / alignedBars[s][i - 1].close);
  }

  for (let i = 0; i < n; i++) {
    if (i < 200) continue;
    const weights = {}; let wSum = 0;
    for (const s of symbols) {
      const hist = dollarVol[s].slice(i - 200, i);
      const w = median(hist);
      weights[s] = w; wSum += w;
    }

    // BREADTH / BREADTH_M / entropy: classify each coin's 1-bar return.
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

    // Relative value: each coin's log-price momentum vs the equal-weight peer index.
    const logCs = symbols.map(s => Math.log(alignedBars[s][i].close));
    const avgLogC = mean(logCs);
    for (let si = 0; si < symbols.length; si++) {
      const s = symbols[si];
      out[s].RV_raw = out[s].RV_raw || new Array(n).fill(null);
      out[s].RV_raw[i] = logCs[si] - avgLogC;
    }

    // Lead-lag: for each coin, the average of the OTHER coins' momentum one
    // bar (15min) ago -- the smallest, least-overfit lead-lag construction
    // (a full learned tau/coin weight matrix is exactly the "immediately
    // optimize thousands of coefficients" both documents warn against).
    if (i >= 1) {
      for (const s of symbols) {
        const others = symbols.filter(x => x !== s);
        const vals = others.map(o => perAsset[o].M[i - 1]).filter(v => v != null);
        if (vals.length === others.length) out[s].LL[i] = mean(vals);
      }
    }
  }

  // BA, SYNC, SYNC_A, DH need lag-16 or rolling-window derived values, done
  // in a second pass so BREADTH_M/H above are already filled.
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

    // Relative-value momentum (RV_i = RV_raw(t) - RV_raw(t-n), n=16).
    for (const s of symbols) {
      if (out[s].RV_raw && out[s].RV_raw[i] != null && out[s].RV_raw[i - 16] != null) {
        out[s].RV[i] = out[s].RV_raw[i] - out[s].RV_raw[i - 16];
      }
    }

    // Synchronization: average pairwise correlation of 1-bar returns over a
    // trailing 64-bar window. O(pairs * window) per bar -- fine at 6 coins.
    let corrSum = 0, corrCnt = 0;
    for (let a = 0; a < symbols.length; a++) {
      for (let b = a + 1; b < symbols.length; b++) {
        const ra = rets1[symbols[a]].slice(i - 63, i + 1);
        const rb = rets1[symbols[b]].slice(i - 63, i + 1);
        if (ra.some(v => v == null) || rb.some(v => v == null)) continue;
        const c = S.corr(ra, rb);
        if (c != null) { corrSum += c; corrCnt++; }
      }
    }
    if (corrCnt) SYNC[i] = corrSum / corrCnt;
    if (i >= 16 && SYNC[i] != null && SYNC[i - 16] != null) SYNC_A[i] = SYNC[i] - SYNC[i - 16];
  }

  return { perAssetCross: out, BREADTH, BA, SYNC, SYNC_A, H, DH };
}

// ═══════════════════════════════════════════════════════════════════════
// TEMPORAL GEOMETRY (persistence / acceleration / exhaustion) on M -- the
// same construction both documents specify (Part XIV-XVI / section 17)
// ═══════════════════════════════════════════════════════════════════════
function buildTemporal(M) {
  const n = M.length;
  const PC = new Array(n).fill(null), ACC = new Array(n).fill(null), EX = new Array(n).fill(null), BE = new Array(n).fill(null);
  for (let i = 32; i < n; i++) {
    if (M[i] == null || M[i - 1] == null || M[i - 2] == null) continue;
    const persist = mean([Math.abs(M[i]), Math.abs(M[i - 1]), Math.abs(M[i - 2])]);
    const dp = Math.abs(Math.sign(M[i]) + Math.sign(M[i - 1]) + Math.sign(M[i - 2])) / 3;
    PC[i] = tanh(persist) * dp;

    const acc1 = M[i] - M[i - 1];
    const acc1prev = M[i - 1] - M[i - 2];
    ACC[i] = tanh(acc1 + 0.5 * (acc1 - acc1prev));

    const win = [];
    for (let j = i - 31; j <= i; j++) if (M[j] != null) win.push(M[j]);
    const peak = Math.max(...win.map(Math.abs));
    EX[i] = tanh((peak - Math.abs(M[i])) / (peak + 1e-9));
  }
  return { PC, ACC, EX };
}

// ═══════════════════════════════════════════════════════════════════════
// FEATURE MATRIX + RIDGE FIT (structure fixed above; only the top-level
// combination is learned, and only ever from TRAIN rows)
// ═══════════════════════════════════════════════════════════════════════
const FEATURE_NAMES = ['M_COH', 'TREND_GATE', 'ABSD', 'BREADTH', 'BREADTH_M', 'BA', 'RV', 'LL', 'SYNC', 'SYNC_A', 'DH', 'PC', 'ACC', 'EX', 'LIQ'];

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
    rows.push({ i, x: vals });
  }
  return rows;
}

/** Ridge regression via normal equations: (X'X + lambda*I) w = X'y.
 * Features are standardised using TRAIN-only mean/std, frozen and reused
 * for every subsequent prediction (including on held-out assets). */
// CORRECTION (see research/README.md's correction note): rows must carry
// their own `.y` embedded, not be looked up afterward via a shared
// `Map<index, y>` keyed by `r.i` alone. Every coin's row array uses its
// own 0..n-1 index into the SAME aligned timeline, so BTC's row i=5000 and
// DOGE's row i=5000 are different bars sharing the same index -- pooling
// multiple coins into one lookup map let whichever coin was processed
// last silently overwrite every earlier coin's label at each shared
// index. This affected Experiments A and B (multi-coin training) but not
// Experiment C (single-coin per fit, no collision possible).
function fitRidge(rows, lambda) {
  const p = rows[0].x.length;
  const mu = new Array(p).fill(0), sigma = new Array(p).fill(1);
  for (let j = 0; j < p; j++) {
    const col = rows.map(r => r.x[j]);
    mu[j] = mean(col); sigma[j] = sd(col) || 1;
  }
  const X = rows.map(r => r.x.map((v, j) => (v - mu[j]) / sigma[j]));
  const y = rows.map(r => r.y);

  const XtX = Array.from({ length: p }, () => new Array(p).fill(0));
  const Xty = new Array(p).fill(0);
  for (let n = 0; n < X.length; n++) {
    for (let a = 0; a < p; a++) {
      Xty[a] += X[n][a] * y[n];
      for (let b = 0; b < p; b++) XtX[a][b] += X[n][a] * X[n][b];
    }
  }
  for (let a = 0; a < p; a++) XtX[a][a] += lambda;

  const w = solveLinearSystem(XtX, Xty);
  return { w, mu, sigma };
}

function solveLinearSystem(A, b) {
  const n = b.length;
  const M = A.map((row, i) => [...row, b[i]]);
  for (let col = 0; col < n; col++) {
    let piv = col;
    for (let r = col + 1; r < n; r++) if (Math.abs(M[r][col]) > Math.abs(M[piv][col])) piv = r;
    [M[col], M[piv]] = [M[piv], M[col]];
    if (Math.abs(M[col][col]) < 1e-12) continue;
    for (let r = 0; r < n; r++) {
      if (r === col) continue;
      const f = M[r][col] / M[col][col];
      for (let c2 = col; c2 <= n; c2++) M[r][c2] -= f * M[col][c2];
    }
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

// ─── Barrier-race outcome + evaluation harness (verbatim from Study 15) ───
function barrierOutcome(bars, i, horizon, barrier = BARRIER) {
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

function computeAUC(scores, labels) {
  const pos = [], neg = [];
  for (let i = 0; i < scores.length; i++) (labels[i] ? pos : neg).push(scores[i]);
  if (!pos.length || !neg.length) return null;
  const all = scores.map((s, i) => ({ s, l: labels[i] })).sort((a, b) => a.s - b.s);
  let rankSum = 0, idx = 0;
  while (idx < all.length) {
    let j = idx;
    while (j < all.length && all[j].s === all[idx].s) j++;
    const avgRank = (idx + 1 + j) / 2;
    for (let k = idx; k < j; k++) if (all[k].l) rankSum += avgRank;
    idx = j;
  }
  const nPos = pos.length, nNeg = neg.length;
  return (rankSum - nPos * (nPos + 1) / 2) / (nPos * nNeg);
}

function blockBootstrapP(outcomes, blockLen, iters = 300) {
  const n = outcomes.length;
  if (n < 30) return null;
  const observed = mean(outcomes) - 0.5;
  let seed = 909090;
  const rand = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; };
  const nBlocks = Math.ceil(n / blockLen);
  let extreme = 0;
  for (let it = 0; it < iters; it++) {
    const resample = [];
    for (let b = 0; b < nBlocks; b++) {
      const start = Math.floor(rand() * Math.max(1, n - blockLen));
      for (let k = 0; k < blockLen && resample.length < n; k++) resample.push(outcomes[start + k]);
    }
    const stat = mean(resample) - mean(outcomes);
    if (Math.abs(stat) >= Math.abs(observed)) extreme++;
  }
  return (extreme + 1) / (iters + 1);
}

function causalTopBottomMask(scores, frac, minHistory) {
  const n = scores.length;
  const topMask = new Array(n).fill(false), botMask = new Array(n).fill(false);
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

function evaluateScore(bars, scoreByIdx, horizon, costBps) {
  const n = bars.length;
  const scores = new Array(n).fill(null);
  for (const [i, s] of scoreByIdx) scores[i] = s;
  const outcomes = new Array(n).fill(null);
  for (let i = 0; i < n - horizon; i++) if (scores[i] != null) outcomes[i] = barrierOutcome(bars, i, horizon);

  const validIdx = [];
  for (let i = 0; i < n; i++) if (scores[i] != null && outcomes[i] != null) validIdx.push(i);
  if (validIdx.length < 300) return null;

  const auc = computeAUC(validIdx.map(i => scores[i]), validIdx.map(i => outcomes[i]));
  const { topMask, botMask } = causalTopBottomMask(scores, 0.10, 200);
  const topIdx = validIdx.filter(i => topMask[i]);
  const botIdx = validIdx.filter(i => botMask[i]);
  const topWR = topIdx.length >= 30 ? mean(topIdx.map(i => outcomes[i])) : null;
  const botWR = botIdx.length >= 30 ? 1 - mean(botIdx.map(i => outcomes[i])) : null;

  const posInValid = new Map(); validIdx.forEach((idx, pos) => posInValid.set(idx, pos));
  const mid = Math.floor(validIdx.length / 2);
  const topFirst = topIdx.filter(i => posInValid.get(i) < mid).map(i => outcomes[i]);
  const topSecond = topIdx.filter(i => posInValid.get(i) >= mid).map(i => outcomes[i]);
  const botFirstRaw = botIdx.filter(i => posInValid.get(i) < mid).map(i => outcomes[i]);
  const botSecondRaw = botIdx.filter(i => posInValid.get(i) >= mid).map(i => outcomes[i]);

  const blockLen = Math.max(8, horizon * 3);
  const topP = topIdx.length >= 30 ? blockBootstrapP(topIdx.map(i => outcomes[i]), blockLen) : null;
  const botP = botIdx.length >= 30 ? blockBootstrapP(botIdx.map(i => outcomes[i]).map(v => 1 - v), blockLen) : null;

  const costR = costBps / 50;
  return {
    n: validIdx.length, auc,
    topN: topIdx.length, topWR, topP, topExpR: topWR != null ? (2 * topWR - 1) - costR : null,
    topFirst: topFirst.length >= 10 ? mean(topFirst) : null, topSecond: topSecond.length >= 10 ? mean(topSecond) : null,
    botN: botIdx.length, botWR, botP, botExpR: botWR != null ? (2 * botWR - 1) - costR : null,
    botFirst: botFirstRaw.length >= 10 ? 1 - mean(botFirstRaw) : null, botSecond: botSecondRaw.length >= 10 ? 1 - mean(botSecondRaw) : null
  };
}

// ═══════════════════════════════════════════════════════════════════════
function main() {
  const args = {};
  for (let i = 2; i < process.argv.length; i += 2) args[process.argv[i].replace(/^--/, '')] = process.argv[i + 1];
  const costBps = parseFloat(args.cost || '4');
  const line = '─'.repeat(112);

  console.log(`\n${line}\n  STUDY 20 — TESTING THE UPLOADED "CME-ULTIMATE" / "CME-X" UNIVERSAL EQUATIONS\n${line}`);
  console.log(`  6 coins: ${COINS.join(', ')} · barrier +/-${(BARRIER * 100).toFixed(1)}% · horizons ${HORIZONS.join('/')} bars (15m)`);
  console.log(`  Fit horizon for the ridge: ${FIT_HORIZON} bars. Cost floor: ${costBps}bps.\n`);

  console.log('  Loading and aligning bars...');
  const rawBars = {};
  for (const s of COINS) rawBars[s] = loadBars(s);
  const { bars: alignedBars } = alignAll(rawBars);
  const n = alignedBars[COINS[0]].length;
  console.log(`  ${n} aligned 15m bars across all 6 coins (${new Date(alignedBars[COINS[0]][0].start).toISOString()} -> ${new Date(alignedBars[COINS[0]][n - 1].start).toISOString()})\n`);

  console.log('  Building per-asset universal features...');
  const perAsset = {};
  for (const s of COINS) perAsset[s] = buildAssetSeries(alignedBars[s]);

  console.log('  Building cross-asset field (breadth/relative-value/lead-lag/sync/entropy)...');
  const cross = buildCrossAsset(alignedBars, perAsset);

  console.log('  Building temporal geometry (persistence/acceleration/exhaustion) on M_COH...');
  const temporal = {};
  for (const s of COINS) {
    const mCoh = perAsset[s].M.map((m, i) => m != null && perAsset[s].COH[i] != null && perAsset[s].TREND_GATE[i] != null
      ? m * (0.5 + 0.5 * perAsset[s].COH[i]) * perAsset[s].TREND_GATE[i] : null);
    temporal[s] = buildTemporal(mCoh);
  }

  const featureRows = {};
  for (const s of COINS) featureRows[s] = buildFeatureRows(s, perAsset, cross, temporal, alignedBars[s]);
  console.log(`  Feature rows per coin: ${COINS.map(s => `${s} ${featureRows[s].length}`).join(', ')}\n`);

  function outcomesFor(symbol, horizon) {
    const bars = alignedBars[symbol];
    const m = new Map();
    for (const r of featureRows[symbol]) {
      const y = barrierOutcome(bars, r.i, horizon);
      if (y != null) m.set(r.i, y);
    }
    return m;
  }

  function runExperiment(label, trainSymbols, testSymbols) {
    console.log(`${line}\n  ${label}\n${line}`);
    const trainRows = [];
    for (const s of trainSymbols) {
      const y = outcomesFor(s, FIT_HORIZON);
      for (const r of featureRows[s]) if (y.has(r.i)) trainRows.push({ i: r.i, x: r.x, y: y.get(r.i) });
    }
    console.log(`  Train: ${trainSymbols.join('+')} (${trainRows.length} rows) -> Test: ${testSymbols.join('+')} (never seen during fit)`);
    const model = fitRidge(trainRows, RIDGE_LAMBDA);
    console.log(`  Fitted weights: ${FEATURE_NAMES.map((nm, j) => `${nm}=${model.w[j].toFixed(3)}`).join(', ')}\n`);

    const allTests = [];
    for (const s of testSymbols) {
      const scoreByIdx = predict(featureRows[s], model);
      for (const h of HORIZONS) {
        const res = evaluateScore(alignedBars[s], scoreByIdx, h, costBps);
        if (res) allTests.push({ coin: s, horizon: h, ...res });
      }
    }
    reportTests(allTests, costBps);
    return allTests;
  }

  // ── THE decisive test: cross-asset invariance (CME-X section 22) ──
  const expA = runExperiment('EXPERIMENT A — Train BTC+ETH+SOL, test DOGE+LINK+AVAX', TRAIN_SET_A, TEST_SET_A);
  const expB = runExperiment('EXPERIMENT B — Train DOGE+LINK+AVAX, test BTC+ETH+SOL', TEST_SET_A, TRAIN_SET_A);

  // ── Time invariance (CME-X section 23): same-asset train/test split in time ──
  console.log(`${line}\n  EXPERIMENT C — TIME INVARIANCE: same coins, first 60% in time -> test on last 40%\n${line}`);
  const timeTests = [];
  for (const s of TRAIN_SET_A.concat(TEST_SET_A)) {
    const rows = featureRows[s];
    const cut = Math.floor(rows.length * 0.6);
    const trainRows = rows.slice(0, cut), testRows = rows.slice(cut);
    const y = outcomesFor(s, FIT_HORIZON);
    const trainRowsFiltered = trainRows.filter(r => y.has(r.i)).map(r => ({ i: r.i, x: r.x, y: y.get(r.i) }));
    if (trainRowsFiltered.length < 500) continue;
    const model = fitRidge(trainRowsFiltered, RIDGE_LAMBDA);
    const scoreByIdx = predict(testRows, model);
    for (const h of HORIZONS) {
      const res = evaluateScore(alignedBars[s], scoreByIdx, h, costBps);
      if (res) timeTests.push({ coin: s, horizon: h, ...res });
    }
  }
  reportTests(timeTests, costBps);

  // ── FDR across everything from all three experiments together ──
  const combined = [...expA, ...expB, ...timeTests];
  const topT = combined.filter(t => t.topP != null).map(t => ({ ...t, p: t.topP, tail: 'long' }));
  const botT = combined.filter(t => t.botP != null).map(t => ({ ...t, p: t.botP, tail: 'short' }));
  const { survivors, tested, threshold } = S.fdr([...topT, ...botT], 0.10);
  console.log(`${line}\n  FDR ACROSS ALL ${tested} DECILE TESTS (both invariance experiments + time-split, both tails, all coins/horizons)\n${line}`);
  if (!survivors.length) {
    console.log('  Nothing survived FDR correction.\n');
  } else {
    const solid = survivors.filter(s => {
      const wr = s.tail === 'long' ? s.topWR : s.botWR;
      const expR = s.tail === 'long' ? s.topExpR : s.botExpR;
      const first = s.tail === 'long' ? s.topFirst : s.botFirst, second = s.tail === 'long' ? s.topSecond : s.botSecond;
      const stable = first != null && second != null ? Math.sign(first - 0.5) === Math.sign(second - 0.5) : true;
      return wr > 0.5 && expR > 0 && stable;
    });
    console.log(`  ${survivors.length} of ${tested} passed FDR (p <= ${threshold.toExponential(2)}); ${solid.length} also clear the cost floor with a stable split-sample sign.\n`);
    for (const s of solid) {
      const wr = s.tail === 'long' ? s.topWR : s.botWR, expR = s.tail === 'long' ? s.topExpR : s.botExpR, N = s.tail === 'long' ? s.topN : s.botN;
      console.log(`    ${s.coin.padEnd(10)} h=${String(s.horizon).padStart(2)} ${s.tail.padStart(5)}  n=${String(N).padStart(5)}  WR=${(wr * 100).toFixed(1)}%  expR@cost=${expR.toFixed(3)}  p=${s.p.toExponential(1)}`);
    }
  }
  console.log(`\n${line}\n`);

  fs.mkdirSync(path.join(__dirname, 'results'), { recursive: true });
  fs.writeFileSync(path.join(__dirname, 'results', 'cme-invariant-test.json'),
    JSON.stringify({ generatedAt: new Date().toISOString(), costBps, ridgeLambda: RIDGE_LAMBDA, featureNames: FEATURE_NAMES, expA, expB, timeTests }, null, 2));
}

function reportTests(tests, costBps) {
  if (!tests.length) { console.log('  (no test cleared the n>=300 floor)\n'); return; }
  console.log(`  ${'coin'.padEnd(10)}${'h'.padStart(4)}${'AUC'.padStart(8)}${'topN'.padStart(7)}${'topWR'.padStart(8)}${'p'.padStart(9)}${'expR'.padStart(8)}${'1st/2nd'.padStart(14)}`);
  for (const t of tests) {
    const split = (t.topFirst != null && t.topSecond != null) ? `${(t.topFirst * 100).toFixed(0)}%/${(t.topSecond * 100).toFixed(0)}%` : '—';
    console.log(`  ${t.coin.padEnd(10)}${String(t.horizon).padStart(4)}${(t.auc != null ? t.auc.toFixed(3) : '—').padStart(8)}${String(t.topN).padStart(7)}${(t.topWR != null ? (t.topWR * 100).toFixed(1) + '%' : '—').padStart(8)}${(t.topP != null ? t.topP.toFixed(3) : '—').padStart(9)}${(t.topExpR != null ? t.topExpR.toFixed(3) : '—').padStart(8)}${split.padStart(14)}`);
  }
  console.log('');
}

main();
