/**
 * Shared statistical machinery for the research studies.
 *
 * These four gates are applied to every candidate, because every one of them
 * has already caught a false positive in this project:
 *
 *   1. Newey-West HAC standard errors — overlapping forward windows inflated
 *      t-statistics by a median 1.44x and produced 18 phantom "anomalies".
 *   2. Benjamini-Hochberg FDR — testing many slices guarantees some look
 *      significant under a pure null.
 *   3. Split-sample — an effect present in only one half is noise that
 *      happened to average positive.
 *   4. A cost floor — an edge smaller than the round trip that harvests it is
 *      a curiosity, not an edge.
 */
'use strict';

function mean(a) { return a.length ? a.reduce((x, y) => x + y, 0) / a.length : 0; }

function stdev(a) {
  if (a.length < 2) return 0;
  const m = mean(a);
  return Math.sqrt(a.reduce((x, y) => x + (y - m) ** 2, 0) / (a.length - 1));
}

/** Normal approximation to the two-sided p-value (Abramowitz & Stegun 26.2.17). */
function twoSidedP(t) {
  const z = Math.abs(t);
  const p = 0.2316419, b = [0.319381530, -0.356563782, 1.781477937, -1.821255978, 1.330274429];
  const tt = 1 / (1 + p * z);
  const phi = Math.exp(-z * z / 2) / Math.sqrt(2 * Math.PI);
  let poly = 0;
  for (let i = 0; i < b.length; i++) poly += b[i] * Math.pow(tt, i + 1);
  return 2 * phi * poly;
}

/** One-sample test against zero with Bartlett-kernel HAC standard errors. */
function hacT(values, lag = 0) {
  const n = values.length;
  if (n < 25) return null;
  const m = mean(values);
  const dev = values.map(v => v - m);
  let lrv = dev.reduce((a, d) => a + d * d, 0) / n;
  for (let L = 1; L <= Math.min(lag, n - 2); L++) {
    let g = 0;
    for (let i = L; i < n; i++) g += dev[i] * dev[i - L];
    g /= n;
    lrv += 2 * (1 - L / (lag + 1)) * g;
  }
  if (!(lrv > 0)) return null;
  const se = Math.sqrt(lrv / n);
  const t = m / se;
  return { n, mean: m, se, t, p: twoSidedP(t), naiveT: m / (stdev(values) / Math.sqrt(n)) };
}

/** Benjamini-Hochberg step-up procedure. */
function fdr(tests, q = 0.10) {
  const valid = tests.filter(t => t && t.p != null).sort((a, b) => a.p - b.p);
  const m = valid.length;
  let cutoff = -1;
  for (let i = 0; i < m; i++) if (valid[i].p <= ((i + 1) / m) * q) cutoff = i;
  return { survivors: cutoff >= 0 ? valid.slice(0, cutoff + 1) : [], tested: m, threshold: cutoff >= 0 ? valid[cutoff].p : null };
}

/** Pearson correlation. */
function corr(a, b) {
  const n = Math.min(a.length, b.length);
  if (n < 10) return null;
  const ma = mean(a.slice(0, n)), mb = mean(b.slice(0, n));
  let num = 0, da = 0, db = 0;
  for (let i = 0; i < n; i++) {
    num += (a[i] - ma) * (b[i] - mb);
    da += (a[i] - ma) ** 2;
    db += (b[i] - mb) ** 2;
  }
  return (da && db) ? num / Math.sqrt(da * db) : null;
}

/** Verdict helper: applies all four gates and returns a one-word answer. */
function verdict(t, firstHalf, secondHalf, costFloorBps) {
  if (!t) return 'insufficient data';
  const consistent = firstHalf != null && secondHalf != null &&
    Math.sign(firstHalf) === Math.sign(secondHalf) && Math.sign(firstHalf) === Math.sign(t.mean);
  if (Math.abs(t.t) < 2) return 'not significant';
  if (!consistent) return 'fails out-of-sample';
  if (Math.abs(t.mean) <= costFloorBps) return `< ${costFloorBps}bps cost`;
  return 'SURVIVES';
}

module.exports = { mean, stdev, twoSidedP, hacT, fdr, corr, verdict };
