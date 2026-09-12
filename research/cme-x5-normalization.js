#!/usr/bin/env node
/**
 * STUDY 24 — CME-X5: does Part XVII's mechanism have an asset-normalized
 * representation that transfers?
 *
 * Part XVII (research/cme-x4-forensic.js) took the one real result this
 * project has produced and dissected it. What came back:
 *
 *   - The edge is real (label-shuffle placebo collapses to 0.50; the
 *     deliberately-leaked forward-shift produces an absurd 96% WR that the
 *     actual 62.6% result looks nothing like).
 *   - It is BTC/ETH-specific, confirmed the clean way: refits on each
 *     coin's OWN history alone, zero cross-asset pooling, still show it on
 *     BTC (p=0.076) and ETH (p=0.007) and nowhere else.
 *   - It lives in POSITIVE-momentum states only (BTC 62.0% WR) and
 *     vanishes in negative-momentum states (47.8% WR, AUC 0.502).
 *   - Surprise: shuffling BA (breadth acceleration -- how fast the whole
 *     market's momentum-breadth is changing) hurts MORE than shuffling
 *     the asset's own momentum (AUC -0.040 vs -0.026), and BA carries the
 *     largest fitted weight in the frozen model.
 *
 * Two questions follow directly, and this study asks exactly those two:
 *
 * PART A — IS BA REALLY THE ENGINE?
 *   Shuffle-sensitivity says BA matters most; it has never been isolated.
 *   Explicit removal, single-feature models, a minimal 2-feature model,
 *   and a lag sweep over BA's own 16-bar differencing window.
 *
 * PART B — IS THIS A "BTC/ETH FACT" OR A "LARGE-CAP FACT"?
 *   The decisive confound in every prior part: with only 6 coins, BTC and
 *   ETH are ALWAYS the two most liquid, so "coin identity" and "liquidity
 *   rank" are perfectly collinear and cannot be told apart. This part
 *   breaks the confound by widening the universe to 16 coins spanning
 *   mega-cap to mid-cap, then asking whether edge strength is a monotone
 *   function of liquidity RANK (an asset-normalized representation --
 *   "this is a large-cap effect") or whether BTC/ETH remain outliers even
 *   against same-rank peers ("this is a BTC/ETH identity effect").
 *
 *   If it tracks rank: the mechanism generalizes, and the right production
 *   rule is "trade the top-k by liquidity", which survives the universe
 *   changing next cycle.
 *   If BTC/ETH are outliers beyond rank: it does not generalize, and per
 *   the user's own standing instruction -- do not force universality --
 *   it stays labelled asset-specific.
 *
 * NOTE ON WINDOWS: Part A runs on the original 6-coin/418-day series. Part
 * B's 16-coin inner join is bounded by the shortest series (~208 days), so
 * its absolute numbers are NOT directly comparable to Part A's -- BTC/ETH
 * are therefore re-reported inside Part B's own window as the internal
 * reference point.
 */
const fs = require('fs');
const path = require('path');
const S = require('./lib-stats.js');

const DATA = path.join(__dirname, '..', 'backtest', 'data');
const CORE_COINS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'DOGEUSDT', 'LINKUSDT', 'AVAXUSDT'];
const WIDE_EXTRA = ['XRPUSDT', 'ADAUSDT', 'DOTUSDT', 'LTCUSDT', 'BCHUSDT', 'ATOMUSDT', 'NEARUSDT', 'APTUSDT', 'ARBUSDT', 'OPUSDT'];
const K = [1, 2, 4, 8, 16, 32, 64, 128];
const FIT_HORIZON = 4;
const EVAL_HORIZON = 2;
const BARRIER = 0.005;
const RIDGE_LAMBDA = 5.0;
const COST_BPS_DEFAULT = 4;
const DEFAULT_BA_LAG = 16; // the lag Part XIV/XVII used, and the sweep's reference point

function mean(a) { return a.length ? a.reduce((x, y) => x + y, 0) / a.length : 0; }
function sd(a) { if (a.length < 2) return 0; const m = mean(a); return Math.sqrt(a.reduce((x, y) => x + (y - m) ** 2, 0) / (a.length - 1)); }
function median(a) { const s = a.slice().sort((x, y) => x - y); return s[Math.floor(s.length / 2)]; }
function mad(a) { const m = median(a); return median(a.map(v => Math.abs(v - m))) * 1.4826; }
function tanh(x) { return Math.tanh(x); }
function sigmoid(x) { return 1 / (1 + Math.exp(-x)); }
function clamp(x, lo, hi) { return Math.max(lo, Math.min(hi, x)); }

function loadBars(symbol) {
  const f = path.join(DATA, `${symbol}.json`);
  if (!fs.existsSync(f)) return null;
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
// Feature construction -- verbatim from Study 20/23, with ONE addition:
// buildCrossAsset now takes a `baLag` so Part A can sweep BA's own
// differencing window. baLag = 16 reproduces the original exactly.
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
        sumMR += tanh(r / s); sumSign += Math.sign(r); cnt++;
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
      out.TREND_GATE[i] = sigmoid((out.EFF[i] + tanh(out.VA[i]) + tanh(comp)) / 3);
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

/**
 * baOpts (Part C only; null reproduces Parts A/B bit-for-bit) controls how the
 * BREADTH_M series that BA differences is composed:
 *   weight:  'dvw' (trailing-200-bar median dollar volume, the original) | 'eqw'
 *   exclude: 'self' to drop the target coin from its own breadth, or an array of
 *            symbols to drop for everyone
 *   include: an array restricting contributors to just those symbols
 */
function buildCrossAsset(alignedBars, perAsset, baLag = DEFAULT_BA_LAG, baOpts = null) {
  const symbols = Object.keys(alignedBars);
  const n = alignedBars[symbols[0]].length;
  const out = {};
  for (const s of symbols) out[s] = { BREADTH_M: new Array(n).fill(null), RV: new Array(n).fill(null), LL: new Array(n).fill(null) };
  const BREADTH = new Array(n).fill(null);
  const SYNC = new Array(n).fill(null), SYNC_A = new Array(n).fill(null);
  const H = new Array(n).fill(null), DH = new Array(n).fill(null);
  const medDollarVol = {};

  const dollarVol = {};
  for (const s of symbols) dollarVol[s] = alignedBars[s].map(b => b.close * b.volume);
  for (const s of symbols) medDollarVol[s] = median(dollarVol[s].filter(v => isFinite(v) && v > 0));
  const rets1 = {};
  for (const s of symbols) { rets1[s] = new Array(n).fill(null); for (let i = 1; i < n; i++) rets1[s][i] = Math.log(alignedBars[s][i].close / alignedBars[s][i - 1].close); }

  for (let i = 0; i < n; i++) {
    if (i < 200) continue;
    const weights = {}; let wSum = 0;
    for (const s of symbols) { const w = median(dollarVol[s].slice(i - 200, i)); weights[s] = w; wSum += w; }

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
    H[i] = -[upFrac, downFrac, neutFrac].reduce((a, p) => a + (p > 0 ? p * Math.log(p + 1e-9) : 0), 0);
    if (!baOpts) {
      for (const s of symbols) if (perAsset[s].M[i] != null) out[s].BREADTH_M[i] = bmSum;
    } else {
      // Recompose the breadth-momentum average under the requested contributor
      // set and weighting, separately for each target coin (exclude:'self'
      // makes the series genuinely different per coin).
      const base = baOpts.include ? symbols.filter(s => baOpts.include.includes(s)) : symbols;
      const dropped = Array.isArray(baOpts.exclude) ? baOpts.exclude : [];
      for (const tgt of symbols) {
        if (perAsset[tgt].M[i] == null) continue;
        const contrib = base.filter(s => s !== (baOpts.exclude === 'self' ? tgt : null) && !dropped.includes(s));
        let num = 0, den = 0;
        for (const s of contrib) {
          const m = perAsset[s].M[i]; if (m == null) continue;
          const w = baOpts.weight === 'eqw' ? 1 : weights[s];
          num += w * m; den += w;
        }
        out[tgt].BREADTH_M[i] = den > 0 ? num / den : null;
      }
    }

    const logCs = symbols.map(s => Math.log(alignedBars[s][i].close));
    const avgLogC = mean(logCs);
    symbols.forEach((s, si) => { out[s].RV_raw = out[s].RV_raw || new Array(n).fill(null); out[s].RV_raw[i] = logCs[si] - avgLogC; });

    if (i >= 1) {
      for (const s of symbols) {
        const others = symbols.filter(x => x !== s);
        const vals = others.map(o => perAsset[o].M[i - 1]).filter(v => v != null);
        if (vals.length === others.length) out[s].LL[i] = mean(vals);
      }
    }
  }

  for (let i = 200; i < n; i++) {
    if (i >= baLag) {
      for (const s of symbols) {
        if (out[s].BREADTH_M[i] != null && out[s].BREADTH_M[i - baLag] != null) {
          out[s].BA = out[s].BA || new Array(n).fill(null);
          out[s].BA[i] = out[s].BREADTH_M[i] - out[s].BREADTH_M[i - baLag];
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
  return { perAssetCross: out, BREADTH, SYNC, SYNC_A, H, DH, medDollarVol };
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
const IDX = Object.fromEntries(FEATURE_NAMES.map((n, i) => [n, i]));

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
    rows.push({ i, x: vals, raw: { M: a.M[i], BA: c.BA ? c.BA[i] : null } });
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
  let seed = 141421;
  const rand = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; };
  const nBlocks = Math.ceil(n / blockLen);
  let extreme = 0;
  for (let it = 0; it < iters; it++) {
    const resample = [];
    for (let b = 0; b < nBlocks; b++) { const start = Math.floor(rand() * Math.max(1, n - blockLen)); for (let k = 0; k < blockLen && resample.length < n; k++) resample.push(outcomes[start + k]); }
    if (Math.abs(mean(resample) - mean(outcomes)) >= Math.abs(observed)) extreme++;
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
function evaluate(bars, scoreByIdx, costBps) {
  const idxs = [...scoreByIdx.keys()];
  const validPairs = [];
  for (const i of idxs) { const y = barrierOutcome(bars, i, EVAL_HORIZON); if (y != null) validPairs.push({ i, score: scoreByIdx.get(i), y }); }
  if (validPairs.length < 300) return null;
  const auc = computeAUC(validPairs.map(p => p.score), validPairs.map(p => p.y));
  const scoresArr = new Array(Math.max(...validPairs.map(p => p.i)) + 1).fill(null);
  for (const p of validPairs) scoresArr[p.i] = p.score;
  const topMask = causalTopMask(scoresArr, 0.10, 200);
  const topPairs = validPairs.filter(p => topMask[p.i]);
  if (topPairs.length < 30) return { n: validPairs.length, auc, topN: topPairs.length, topWR: null };
  const topWR = mean(topPairs.map(p => p.y));
  const expR = topWR - (1 - topWR) - costBps / 100;
  return { n: validPairs.length, auc, topN: topPairs.length, topWR, expR, p: blockBootstrapP(topPairs.map(p => p.y), Math.max(8, EVAL_HORIZON * 3)) };
}
function fmt(r) {
  if (!r) return 'n<300';
  if (r.topWR == null) return `AUC=${r.auc != null ? r.auc.toFixed(3) : '—'} (topN<30)`;
  return `AUC=${r.auc.toFixed(3)}  n=${String(r.topN).padStart(5)}  WR=${(r.topWR * 100).toFixed(1)}%  expR=${r.expR.toFixed(3)}  p=${r.p != null ? r.p.toFixed(3) : '—'}`;
}
/** Spearman rank correlation. */
function spearman(xs, ys) {
  const rank = arr => {
    const sorted = arr.map((v, i) => ({ v, i })).sort((a, b) => a.v - b.v);
    const r = new Array(arr.length);
    for (let k = 0; k < sorted.length; k++) r[sorted[k].i] = k + 1;
    return r;
  };
  return S.corr(rank(xs), rank(ys));
}

// ═══════════════════════════════════════════════════════════════════════
function buildUniverse(coins, baLag = DEFAULT_BA_LAG, baOpts = null) {
  const rawBars = {};
  for (const s of coins) { const b = loadBars(s); if (b) rawBars[s] = b; }
  const present = Object.keys(rawBars);
  const { bars: alignedBars } = alignAll(rawBars);
  const perAsset = {}; for (const s of present) perAsset[s] = buildAssetSeries(alignedBars[s]);
  const cross = buildCrossAsset(alignedBars, perAsset, baLag, baOpts);
  const temporal = {};
  for (const s of present) {
    const mCoh = perAsset[s].M.map((m, i) => m != null && perAsset[s].COH[i] != null && perAsset[s].TREND_GATE[i] != null
      ? m * (0.5 + 0.5 * perAsset[s].COH[i]) * perAsset[s].TREND_GATE[i] : null);
    temporal[s] = buildTemporal(mCoh);
  }
  const rows = {}; for (const s of present) rows[s] = buildFeatureRows(s, perAsset, cross, temporal, alignedBars[s]);
  return { coins: present, alignedBars, perAsset, cross, rows };
}

function main() {
  const args = {};
  for (let i = 2; i < process.argv.length; i += 2) args[process.argv[i].replace(/^--/, '')] = process.argv[i + 1];
  const costBps = parseFloat(args.cost || String(COST_BPS_DEFAULT));
  const line = '─'.repeat(114);
  const results = {};

  console.log(`\n${line}\n  STUDY 24 — CME-X5: IS BA THE ENGINE, AND IS THIS A LARGE-CAP EFFECT?\n${line}`);

  // ══════════════════ PART A — BA isolation (6-coin, full depth) ══════════════════
  console.log(`\n${line}\n  PART A — IS BREADTH ACCELERATION REALLY THE ENGINE? (6 coins, full history)\n${line}`);
  const uni = buildUniverse(CORE_COINS);
  const nBars = uni.alignedBars[uni.coins[0]].length;
  console.log(`  ${uni.coins.length} coins, ${nBars} aligned bars, rows/coin ~${uni.rows[uni.coins[0]].length}\n`);

  const TEST_COINS = ['BTCUSDT', 'ETHUSDT'];
  const TRAIN_COINS = ['DOGEUSDT', 'LINKUSDT', 'AVAXUSDT'];

  function outcomesFor(u, symbol, horizon) {
    const m = new Map();
    for (const r of u.rows[symbol]) { const y = barrierOutcome(u.alignedBars[symbol], r.i, horizon); if (y != null) m.set(r.i, y); }
    return m;
  }
  /** Fit on TRAIN_COINS using a chosen subset of feature indices, evaluate on each test coin. */
  function fitAndEval(u, featureIdx, trainCoins, testCoins, label) {
    const trainRows = [];
    for (const s of trainCoins) {
      const y = outcomesFor(u, s, FIT_HORIZON);
      for (const r of u.rows[s]) if (y.has(r.i)) trainRows.push({ i: r.i, x: featureIdx.map(j => r.x[j]), y: y.get(r.i) });
    }
    if (trainRows.length < 500) return null;
    const model = fitRidge(trainRows, RIDGE_LAMBDA);
    const out = {};
    for (const s of testCoins) {
      const scoreByIdx = new Map(u.rows[s].map(r => [r.i, scoreVector(featureIdx.map(j => r.x[j]), model)]));
      out[s] = evaluate(u.alignedBars[s], scoreByIdx, costBps);
    }
    return { model, out };
  }

  const ALL = FEATURE_NAMES.map((_, j) => j);
  const CROSS_ASSET_IDX = ['BREADTH', 'BREADTH_M', 'BA', 'RV', 'LL', 'SYNC', 'SYNC_A', 'DH'].map(n => IDX[n]);

  console.log('  Reference + explicit removals (full 15-feature model, refit each time):');
  const partA = {};
  const variants = {
    'full 15-feature (reference)': ALL,
    'minus BA': ALL.filter(j => j !== IDX.BA),
    'minus momentum (M_COH)': ALL.filter(j => j !== IDX.M_COH),
    'minus ALL cross-asset (8 terms)': ALL.filter(j => !CROSS_ASSET_IDX.includes(j)),
    'BA only (1 feature)': [IDX.BA],
    'momentum only (1 feature)': [IDX.M_COH],
    'momentum + BA (2 features)': [IDX.M_COH, IDX.BA]
  };
  for (const [label, idxs] of Object.entries(variants)) {
    const res = fitAndEval(uni, idxs, TRAIN_COINS, TEST_COINS, label);
    partA[label] = res ? res.out : null;
    const parts = TEST_COINS.map(s => `${s.replace('USDT', '')}: ${res && res.out[s] ? fmt(res.out[s]) : 'n/a'}`);
    console.log(`    ${label.padEnd(32)} ${parts[0]}`);
    console.log(`    ${''.padEnd(32)} ${parts[1]}`);
  }
  results.partA = partA;

  // BA lag sweep -- rebuild the universe per lag (BA is the only thing that changes)
  console.log('\n  BA differencing-lag sweep (momentum + BA, 2-feature model):');
  results.baLagSweep = {};
  for (const lag of [4, 8, 16, 32, 64]) {
    const u = lag === DEFAULT_BA_LAG ? uni : buildUniverse(CORE_COINS, lag);
    const res = fitAndEval(u, [IDX.M_COH, IDX.BA], TRAIN_COINS, TEST_COINS, `lag${lag}`);
    results.baLagSweep[lag] = res ? res.out : null;
    const btc = res && res.out.BTCUSDT, eth = res && res.out.ETHUSDT;
    console.log(`    BA lag ${String(lag).padStart(2)} bars  BTC ${btc ? `AUC=${btc.auc.toFixed(3)} WR=${(btc.topWR * 100).toFixed(1)}%` : 'n/a'}   ETH ${eth ? `AUC=${eth.auc.toFixed(3)} WR=${(eth.topWR * 100).toFixed(1)}%` : 'n/a'}`);
  }

  // ══════════════════ PART B — liquidity-rank transfer test ══════════════════
  console.log(`\n${line}\n  PART B — "BTC/ETH FACT" OR "LARGE-CAP FACT"? (wide universe, leave-one-asset-out)\n${line}`);
  const wideCoins = CORE_COINS.concat(WIDE_EXTRA);
  const available = wideCoins.filter(s => loadBars(s));
  if (available.length < 10) {
    console.log(`  Only ${available.length} coins available -- run: node backtest/fetch-klines.js ${WIDE_EXTRA.join(',')} 15 20000`);
  } else {
    const wide = buildUniverse(available);
    const nWide = wide.alignedBars[wide.coins[0]].length;
    const firstTs = wide.alignedBars[wide.coins[0]][0].start, lastTs = wide.alignedBars[wide.coins[0]][nWide - 1].start;
    console.log(`  ${wide.coins.length} coins, ${nWide} aligned bars (${new Date(firstTs).toISOString().slice(0, 10)} -> ${new Date(lastTs).toISOString().slice(0, 10)})`);
    console.log(`  NOTE: shorter window than Part A -- absolute numbers are not comparable across parts.\n`);

    const liq = wide.cross.medDollarVol;
    const ranked = wide.coins.slice().sort((a, b) => liq[b] - liq[a]);
    console.log('  Liquidity ranking (median dollar volume per 15m bar, causal descriptive):');
    ranked.forEach((s, k) => console.log(`    ${String(k + 1).padStart(2)}. ${s.padEnd(11)} $${Math.round(liq[s]).toLocaleString()}`));
    console.log('');

    console.log('  Leave-one-asset-out (train on all others, test on held-out), 2-feature momentum+BA model:');
    console.log(`  ${'rank'.padStart(4)}  ${'coin'.padEnd(11)}${'AUC'.padStart(8)}${'topN'.padStart(7)}${'WR'.padStart(8)}${'expR'.padStart(9)}${'p'.padStart(8)}`);
    const looRows = [];
    for (let k = 0; k < ranked.length; k++) {
      const held = ranked[k];
      const others = wide.coins.filter(c => c !== held);
      const res = fitAndEval(wide, [IDX.M_COH, IDX.BA], others, [held], `loo-${held}`);
      const r = res && res.out[held];
      looRows.push({ rank: k + 1, coin: held, liq: liq[held], ...(r || {}) });
      console.log(`  ${String(k + 1).padStart(4)}  ${held.padEnd(11)}${(r && r.auc != null ? r.auc.toFixed(3) : '—').padStart(8)}${String(r && r.topN != null ? r.topN : '—').padStart(7)}${(r && r.topWR != null ? (r.topWR * 100).toFixed(1) + '%' : '—').padStart(8)}${(r && r.expR != null ? r.expR.toFixed(3) : '—').padStart(9)}${(r && r.p != null ? r.p.toFixed(3) : '—').padStart(8)}`);
    }
    results.liquidityLOO = looRows;

    const withAuc = looRows.filter(r => r.auc != null);
    const rhoRankAuc = spearman(withAuc.map(r => r.rank), withAuc.map(r => r.auc));
    const rhoRankWr = spearman(withAuc.filter(r => r.topWR != null).map(r => r.rank), withAuc.filter(r => r.topWR != null).map(r => r.topWR));
    console.log(`\n  Spearman(liquidity rank, AUC) = ${rhoRankAuc != null ? rhoRankAuc.toFixed(3) : '—'}   (negative = more liquid -> higher AUC)`);
    console.log(`  Spearman(liquidity rank, win rate) = ${rhoRankWr != null ? rhoRankWr.toFixed(3) : '—'}`);
    results.spearman = { rankVsAuc: rhoRankAuc, rankVsWr: rhoRankWr };

    // Is BTC/ETH an outlier beyond its rank? Compare top-2 vs the rest.
    const top2 = withAuc.filter(r => r.rank <= 2), rest = withAuc.filter(r => r.rank > 2);
    const top2Auc = mean(top2.map(r => r.auc)), restAuc = mean(rest.map(r => r.auc));
    const top5 = withAuc.filter(r => r.rank <= 5), bottom5 = withAuc.filter(r => r.rank > withAuc.length - 5);
    console.log(`\n  Mean AUC, top 2 by liquidity (${top2.map(r => r.coin.replace('USDT', '')).join('/')}):  ${top2Auc.toFixed(4)}`);
    console.log(`  Mean AUC, all others:                       ${restAuc.toFixed(4)}`);
    console.log(`  Mean AUC, top 5 by liquidity:               ${mean(top5.map(r => r.auc)).toFixed(4)}`);
    console.log(`  Mean AUC, bottom 5 by liquidity:            ${mean(bottom5.map(r => r.auc)).toFixed(4)}`);
    results.groupMeans = { top2Auc, restAuc, top5Auc: mean(top5.map(r => r.auc)), bottom5Auc: mean(bottom5.map(r => r.auc)) };
  }

  // ═══════ PART C — is BA market-wide information, or laundered mega-cap momentum? ═══════
  //
  // A construction detail that Part XVII never examined, and that decides how to
  // read its headline result: BA differences BREADTH_M, a DOLLAR-VOLUME-WEIGHTED
  // average of every coin's momentum -- including the momentum of the very coin
  // being predicted. Dollar-volume weighting is dominated by BTC and ETH. So
  // "shuffling BA hurts most" has two readings that the shuffle test cannot tell
  // apart: (1) market-wide breadth genuinely carries the information, or (2) BA is
  // a smoothed, 16-bar-differenced restatement of BTC/ETH's own momentum, and the
  // BTC/ETH-specificity found in Part XVII is partly built into the feature.
  //
  // There is no temporal leak either way -- everything is contemporaneous at bar i
  // and the weights are trailing-only -- but the two readings imply opposite
  // conclusions, so they have to be separated directly.
  console.log(`\n${line}\n  PART C — BREADTH PROVENANCE: is BA market-wide info, or BTC/ETH momentum in disguise?\n${line}`);
  {
    const liqA = uni.cross.medDollarVol;
    const totalLiq = uni.coins.reduce((a, s) => a + liqA[s], 0);
    const megaShare = (liqA.BTCUSDT + liqA.ETHUSDT) / totalLiq;
    console.log(`  BTC+ETH are ${(megaShare * 100).toFixed(1)}% of the dollar-volume weight in the reference BREADTH_M average.`);
    console.log(`  Each variant below recomposes that average, refits momentum+BA on DOGE/LINK/AVAX, tests on BTC/ETH.\n`);

    const baVariants = {
      'dollar-vol wtd, ALL coins (reference)': null,
      'dollar-vol wtd, EXCLUDING the test coin': { weight: 'dvw', exclude: 'self' },
      'EQUAL wtd, all coins': { weight: 'eqw' },
      'EQUAL wtd, excluding the test coin': { weight: 'eqw', exclude: 'self' },
      'BTC+ETH ONLY (pure mega-cap momentum)': { weight: 'dvw', include: ['BTCUSDT', 'ETHUSDT'] },
      'ALTS ONLY (BTC+ETH removed entirely)': { weight: 'dvw', exclude: ['BTCUSDT', 'ETHUSDT'] }
    };
    results.baProvenance = { megaCapDollarVolShare: megaShare, variants: {} };
    for (const [label, opts] of Object.entries(baVariants)) {
      const u = opts == null ? uni : buildUniverse(CORE_COINS, DEFAULT_BA_LAG, opts);
      const res = fitAndEval(u, [IDX.M_COH, IDX.BA], TRAIN_COINS, TEST_COINS, `prov-${label}`);
      results.baProvenance.variants[label] = res ? res.out : null;
      const b = res && res.out.BTCUSDT, e = res && res.out.ETHUSDT;
      const cell = r => r ? `AUC=${r.auc.toFixed(3)} WR=${r.topWR != null ? (r.topWR * 100).toFixed(1) + '%' : '—'}` : 'n/a';
      console.log(`    ${label.padEnd(42)} BTC ${cell(b).padEnd(24)} ETH ${cell(e)}`);
    }
  }

  console.log(`\n${line}\n`);
  fs.mkdirSync(path.join(__dirname, 'results'), { recursive: true });
  fs.writeFileSync(path.join(__dirname, 'results', 'cme-x5-normalization.json'),
    JSON.stringify({ generatedAt: new Date().toISOString(), costBps, results }, null, 2));
}

main();
