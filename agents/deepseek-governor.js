/**
 * DIV-07 — LLM Call Governor.
 *
 * WHY CREDIT WAS BURNING
 * The old client called DeepSeek from three places, none of them gated on
 * whether the call could change any decision:
 *   1. Every time a symbol's decision changed to BUY or SELL. The decision was
 *      recomputed every 3 seconds from 1-minute data, so it oscillated
 *      WAIT→BUY→WAIT→BUY; each re-entry fired a fresh 700-token synthesis, for
 *      five symbols in parallel.
 *   2. A blind 60-second "keep-fresh" refresh of the focused symbol — ~1,440
 *      calls a day that nobody asked for and that changed nothing.
 *   3. The news sentinel re-scored all 15 Benzinga headlines every 90 seconds,
 *      re-sending headlines it had already scored — ~960 calls a day, almost
 *      all of them duplicates.
 *
 * And the output was prose. A 700-token essay that no code reads cannot change
 * an outcome; it is pure cost.
 *
 * THE POLICY HERE
 * The model is used as a second opinion on a candidate that has ALREADY passed
 * every local gate — the one moment where its answer can actually change what
 * happens. It returns a compact structured verdict (CONFIRM / VETO / DOWNGRADE)
 * that the engine consumes, not an essay. Everything else is refused locally:
 *
 *   - candidate must be grade A or better, and locally authorised;
 *   - per-symbol cooldown;
 *   - the market state must have materially changed since the last call for
 *     that symbol (fingerprint comparison), so a re-fire on the same setup is
 *     served from cache;
 *   - a hard daily call budget, after which the engine runs purely on local
 *     logic rather than degrading.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.MasisDeepSeekGovernor = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {

  const DEFAULTS = {
    dailyBudget: 120,               // was effectively unbounded (~2,400+/day)
    perSymbolCooldownMs: 10 * 60 * 1000,
    minGradeRank: 2,                // A or better (A+ = 3, A = 2, B = 1)
    cacheTtlMs: 15 * 60 * 1000,
    reserveForOpenPositions: 20     // budget held back so open trades can always ask
  };

  const GRADE_RANK = { 'A+': 3, 'A': 2, 'B': 1, 'C': 0, 'D': 0 };

  class DeepSeekGovernor {
    constructor(options = {}) {
      this.config = Object.assign({}, DEFAULTS, options);
      this.dayKey = this._dayKey();
      this.callsToday = 0;
      this.skipped = { grade: 0, cooldown: 0, unchanged: 0, budget: 0, inFlight: 0 };
      this.lastCallAt = {};       // symbol -> ts
      this.lastFingerprint = {};  // symbol -> string
      this.cache = {};            // fingerprint -> { at, result }
      this.inFlight = false;
    }

    _dayKey(d = new Date()) {
      return `${d.getUTCFullYear()}-${d.getUTCMonth() + 1}-${d.getUTCDate()}`;
    }

    _roll() {
      const k = this._dayKey();
      if (k !== this.dayKey) {
        this.dayKey = k;
        this.callsToday = 0;
        this.skipped = { grade: 0, cooldown: 0, unchanged: 0, budget: 0, inFlight: 0 };
      }
    }

    /**
     * A coarse fingerprint of the tradeable state. Deliberately coarse: it must
     * change when the *situation* changes, and must NOT change when price
     * wiggles by a tick, otherwise we are back to paying for every oscillation.
     */
    fingerprint(candidate, context) {
      if (!candidate) return `${context.symbol}|none`;
      const bucketedScore = Math.round(candidate.score / 5) * 5;
      const priceBucket = candidate.geometry
        ? Math.round(candidate.geometry.entry / (candidate.geometry.riskDist || 1))
        : 0;
      return [
        context.symbol,
        candidate.name,
        candidate.direction,
        bucketedScore,
        context.regime,
        context.bias,
        priceBucket
      ].join('|');
    }

    /**
     * @returns {{allowed:boolean, reason:string, cached?:object}}
     */
    shouldConsult(candidate, context) {
      this._roll();
      const now = Date.now();

      if (this.inFlight) {
        this.skipped.inFlight++;
        return { allowed: false, reason: 'A consultation is already in flight' };
      }
      if (!candidate) {
        return { allowed: false, reason: 'No candidate setup — nothing worth asking about' };
      }
      if ((GRADE_RANK[candidate.grade] || 0) < this.config.minGradeRank) {
        this.skipped.grade++;
        return { allowed: false, reason: `Candidate is grade ${candidate.grade}; the model is only consulted on grade A or better` };
      }

      const fp = this.fingerprint(candidate, context);
      const cached = this.cache[fp];
      if (cached && now - cached.at < this.config.cacheTtlMs) {
        this.skipped.unchanged++;
        return { allowed: false, reason: 'Identical setup state already analysed — serving the cached verdict', cached: cached.result };
      }

      const last = this.lastCallAt[context.symbol] || 0;
      if (now - last < this.config.perSymbolCooldownMs && !context.hasOpenPosition) {
        this.skipped.cooldown++;
        return { allowed: false, reason: `${context.symbol} consulted ${Math.round((now - last) / 60000)} min ago; cooldown is ${this.config.perSymbolCooldownMs / 60000} min` };
      }

      const effectiveBudget = context.hasOpenPosition
        ? this.config.dailyBudget
        : this.config.dailyBudget - this.config.reserveForOpenPositions;
      if (this.callsToday >= effectiveBudget) {
        this.skipped.budget++;
        return { allowed: false, reason: `Daily consultation budget spent (${this.callsToday}/${this.config.dailyBudget}) — running on local logic only, which is the design, not a degradation` };
      }

      return { allowed: true, reason: 'Grade-A candidate with materially changed state', fingerprint: fp };
    }

    beginCall(symbol) {
      this.inFlight = true;
      this.callsToday++;
      this.lastCallAt[symbol] = Date.now();
    }

    completeCall(fingerprint, result) {
      this.inFlight = false;
      if (fingerprint) this.cache[fingerprint] = { at: Date.now(), result };
      // Keep the cache from growing unbounded over a long session.
      const keys = Object.keys(this.cache);
      if (keys.length > 200) delete this.cache[keys[0]];
    }

    failCall() { this.inFlight = false; }

    status() {
      this._roll();
      const totalSkipped = Object.values(this.skipped).reduce((a, b) => a + b, 0);
      return {
        callsToday: this.callsToday,
        dailyBudget: this.config.dailyBudget,
        remaining: Math.max(0, this.config.dailyBudget - this.callsToday),
        skipped: Object.assign({}, this.skipped),
        totalSkipped,
        savedRatio: (this.callsToday + totalSkipped) > 0
          ? +(totalSkipped / (this.callsToday + totalSkipped)).toFixed(3)
          : 0
      };
    }
  }

  return { DeepSeekGovernor, DEFAULTS, GRADE_RANK };
});
