/**
 * SWARM — Meta-Learner.
 *
 * Tracks which analyst is actually worth listening to, and in which regime.
 *
 * This is the one part of the system that genuinely learns, and it is worth
 * being precise about what it does and does not do. It does not discover new
 * patterns, tune indicator parameters, or optimise anything. It keeps a running
 * score of how often each analyst's read agreed with the direction that turned
 * out to be profitable, bucketed by regime, and uses that to weight their vote
 * in future debates.
 *
 * Why bucketed by regime: a positioning read is genuinely informative in a
 * trending market and close to noise in chop; a volume-profile mean-reversion
 * read is the reverse. A single global weight per analyst would average those
 * into mush, and the averaged number would be wrong in both regimes.
 *
 * Two deliberate conservatism choices:
 *
 *  1. WEIGHTS MOVE SLOWLY AND ARE BOUNDED to [0.4, 1.6]. An analyst cannot be
 *     switched off by a bad week, and cannot come to dominate the panel after a
 *     good one. Over-reacting to a small sample is how a system talks itself
 *     into a concentrated bet.
 *
 *  2. NOTHING MOVES UNTIL THERE IS A SAMPLE. Below `minSample` observations in
 *     a regime, the weight stays at 1.0 exactly. An analyst with three correct
 *     calls has not earned extra influence, and pretending otherwise is how
 *     backtest-fitted systems are born.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.SwarmMetaLearner = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {

  const DEFAULTS = {
    minSample: 15,      // observations in a regime before a weight may move
    maxWeight: 1.6,
    minWeight: 0.4,
    halfLife: 60        // observations after which an old result counts half
  };

  class MetaLearner {
    constructor(options = {}) {
      this.config = Object.assign({}, DEFAULTS, options);
      // regime -> analystId -> { hits, misses, weightedHits, weightedTotal }
      this.record = {};
    }

    _bucket(regime, id) {
      this.record[regime] = this.record[regime] || {};
      this.record[regime][id] = this.record[regime][id] || { hits: 0, misses: 0, weightedHits: 0, weightedTotal: 0 };
      return this.record[regime][id];
    }

    /**
     * Called when a trade closes. `reads` is the analyst panel captured AT
     * ENTRY — scoring them against a panel taken later would be marking their
     * homework with the answer sheet.
     *
     * @param {string} regime
     * @param {Array}  reads     analyst reads snapshotted at entry
     * @param {string} tradeSide 'LONG' | 'SHORT'
     * @param {number} rMultiple realised R
     */
    recordOutcome(regime, reads, tradeSide, rMultiple) {
      const profitable = rMultiple > 0;
      // The direction that would have been right, regardless of what was traded.
      const correctSide = profitable ? tradeSide : (tradeSide === 'LONG' ? 'SHORT' : 'LONG');

      for (const r of reads || []) {
        const side = r.read === 'BULLISH' || r.read === 'DRAW_UP' ? 'LONG'
                   : r.read === 'BEARISH' || r.read === 'DRAW_DOWN' ? 'SHORT' : null;
        if (!side || !r.conviction) continue;   // no opinion, nothing to score

        const b = this._bucket(regime, r.id);
        // Decay older observations so the weight tracks current behaviour
        // rather than an average over conditions that no longer exist.
        const decay = Math.pow(0.5, 1 / this.config.halfLife);
        b.weightedHits *= decay;
        b.weightedTotal *= decay;

        // Credit is scaled by how strongly the analyst committed. Being loudly
        // wrong should cost more than being quietly wrong.
        if (side === correctSide) { b.hits++; b.weightedHits += r.conviction; }
        else { b.misses++; }
        b.weightedTotal += r.conviction;
      }
    }

    /** Weights for the current regime. 1.0 means "no evidence either way yet". */
    weights(regime) {
      const out = {};
      const bucket = this.record[regime] || {};
      for (const [id, b] of Object.entries(bucket)) {
        const n = b.hits + b.misses;
        // Below the live-sample threshold, fall back to the seeded weight if
        // one exists, otherwise stay strictly neutral.
        if (n < this.config.minSample || b.weightedTotal <= 0) {
          out[id] = b.seeded != null ? +b.seeded.toFixed(3) : 1.0;
          continue;
        }
        const accuracy = b.weightedHits / b.weightedTotal;   // 0..1, 0.5 = coin flip
        // Map accuracy around the 0.5 coin-flip point onto the weight band.
        const w = 1 + (accuracy - 0.5) * 2 * 0.6;
        out[id] = +Math.max(this.config.minWeight, Math.min(this.config.maxWeight, w)).toFixed(3);
      }
      return out;
    }

    /** Human-readable reliability table for the dashboard. */
    report() {
      const rows = [];
      for (const [regime, bucket] of Object.entries(this.record)) {
        for (const [id, b] of Object.entries(bucket)) {
          const n = b.hits + b.misses;
          rows.push({
            regime, analyst: id, sample: n,
            accuracy: n ? +((b.hits / n) * 100).toFixed(1) : null,
            weight: this.weights(regime)[id] || 1.0,
            established: n >= this.config.minSample
          });
        }
      }
      return rows.sort((a, b) => b.sample - a.sample);
    }

    /**
     * Seeds weights from an offline predictive-value study
     * (backtest/evaluate-analysts.js), which measures each analyst against
     * forward returns over thousands of observations rather than over the few
     * dozen trades a backtest produces.
     *
     * This is priced honestly: seeded weights are FITTED to the study window.
     * They are a better starting point than assuming every analyst is equally
     * good, but they are not an out-of-sample result, and the study must be
     * re-run on fresh data before they are trusted. Seeds are deliberately
     * compressed toward 1.0 so a fitted number cannot dominate the panel.
     */
    seedFromStudy(rows, options = {}) {
      const compress = options.compress != null ? options.compress : 0.5;
      for (const row of rows || []) {
        if (!row.byRegime) continue;
        for (const [regime, b] of Object.entries(row.byRegime)) {
          if (!b || b.n < 100) continue;             // too thin to seed from
          const edge = b.sumMove / b.n;              // avg forward move, ATR
          // Map measured edge onto a weight. +0.2 ATR is a strong analyst here,
          // so that is where the top of the band sits.
          const raw = 1 + Math.max(-1, Math.min(1, edge / 0.2)) * 0.6;
          const w = 1 + (raw - 1) * compress;
          const bucket = this._bucket(regime, row.analyst);
          bucket.seeded = Math.max(this.config.minWeight, Math.min(this.config.maxWeight, w));
        }
      }
      return this;
    }

    serialise() { return JSON.stringify({ config: this.config, record: this.record }); }
    static restore(json, options) {
      const m = new MetaLearner(options);
      try {
        const parsed = typeof json === 'string' ? JSON.parse(json) : json;
        if (parsed && parsed.record) m.record = parsed.record;
      } catch (e) { /* a corrupt store just means starting from neutral weights */ }
      return m;
    }
  }

  return { MetaLearner, DEFAULTS };
});
