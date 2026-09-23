/**
 * DIV-07 — LLM Call Governor.
 *
 * The model is used as a second opinion on candidates that have already passed
 * local gates. The governor limits duplicate/costly consultations without
 * serialising unrelated symbols behind one global request.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.MasisDeepSeekGovernor = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {

  const DEFAULTS = {
    dailyBudget: 2000,
    perSymbolCooldownMs: 2 * 60 * 1000,
    minGradeRank: 1,
    cacheTtlMs: 3 * 60 * 1000,
    reserveForOpenPositions: 20,
    inFlightTimeoutMs: 90 * 1000
  };

  const GRADE_RANK = { 'A+': 3, 'A': 2, 'B': 1, 'C': 0, 'D': 0 };

  class DeepSeekGovernor {
    constructor(options = {}) {
      this.config = Object.assign({}, DEFAULTS, options);
      this.dayKey = this._dayKey();
      this.callsToday = 0;
      this.skipped = { grade: 0, cooldown: 0, unchanged: 0, budget: 0, inFlight: 0 };
      this.lastCallAt = {};
      this.lastFingerprint = {};
      this.cache = {};

      // IMPORTANT: consultations are independent per symbol. A slow DeepSeek
      // request for BTC must never make ARB wait forever for its own verdict.
      // Kept as a map for backward compatibility with beginCall(symbol).
      this.inFlightBySymbol = {};
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

    _clearStaleInFlight(now = Date.now()) {
      for (const [symbol, startedAt] of Object.entries(this.inFlightBySymbol)) {
        if (now - startedAt > this.config.inFlightTimeoutMs) {
          delete this.inFlightBySymbol[symbol];
        }
      }
    }

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

    shouldConsult(candidate, context) {
      this._roll();
      const now = Date.now();
      this._clearStaleInFlight(now);

      if (!candidate) {
        return { allowed: false, reason: 'No candidate setup — nothing worth asking about' };
      }

      const symbol = context.symbol;
      if (this.inFlightBySymbol[symbol]) {
        this.skipped.inFlight++;
        const ageMs = now - this.inFlightBySymbol[symbol];
        return {
          allowed: false,
          reason: `${symbol} already has a supervisor consultation in flight (${Math.round(ageMs / 1000)}s)`
        };
      }

      if ((GRADE_RANK[candidate.grade] || 0) < this.config.minGradeRank) {
        this.skipped.grade++;
        return {
          allowed: false,
          reason: `Candidate is grade ${candidate.grade}; the model is only consulted on grade A or better`
        };
      }

      const fp = this.fingerprint(candidate, context);
      const cached = this.cache[fp];
      if (cached && now - cached.at < this.config.cacheTtlMs) {
        this.skipped.unchanged++;
        return {
          allowed: false,
          reason: 'Identical setup state already analysed — serving the cached verdict',
          cached: cached.result,
          fingerprint: fp
        };
      }

      const last = this.lastCallAt[symbol] || 0;
      if (now - last < this.config.perSymbolCooldownMs && !context.hasOpenPosition) {
        this.skipped.cooldown++;
        return {
          allowed: false,
          reason: `${symbol} consulted ${Math.round((now - last) / 60000)} min ago; cooldown is ${this.config.perSymbolCooldownMs / 60000} min`
        };
      }

      const effectiveBudget = context.hasOpenPosition
        ? this.config.dailyBudget
        : this.config.dailyBudget - this.config.reserveForOpenPositions;
      if (this.config.dailyBudget > 0 && this.callsToday >= effectiveBudget) {
        this.skipped.budget++;
        return {
          allowed: false,
          reason: `Daily consultation budget spent (${this.callsToday}/${this.config.dailyBudget}) — running on local logic only`
        };
      }

      return {
        allowed: true,
        reason: 'Grade-A candidate with materially changed state',
        fingerprint: fp
      };
    }

    beginCall(symbol) {
      this.inFlightBySymbol[symbol] = Date.now();
      this.callsToday++;
      this.lastCallAt[symbol] = Date.now();
    }

    completeCall(fingerprint, result) {
      if (fingerprint) {
        const symbol = String(fingerprint).split('|')[0];
        if (symbol) delete this.inFlightBySymbol[symbol];
        this.cache[fingerprint] = { at: Date.now(), result };
      }
      const keys = Object.keys(this.cache);
      if (keys.length > 200) delete this.cache[keys[0]];
    }

    failCall(symbol) {
      if (symbol) delete this.inFlightBySymbol[symbol];
    }

    status() {
      this._roll();
      this._clearStaleInFlight();
      const totalSkipped = Object.values(this.skipped).reduce((a, b) => a + b, 0);
      return {
        callsToday: this.callsToday,
        dailyBudget: this.config.dailyBudget,
        remaining: Math.max(0, this.config.dailyBudget - this.callsToday),
        inFlightSymbols: Object.keys(this.inFlightBySymbol),
        inFlightCount: Object.keys(this.inFlightBySymbol).length,
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
