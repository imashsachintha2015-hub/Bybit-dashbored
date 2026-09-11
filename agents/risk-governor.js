/**
 * DIV-08 — Risk Governor.
 *
 * In the previous build this agent existed only as a card in the UI reading
 * "Max Loss: 3.0% / Drawdown: 0.0%". There was no code behind it: no daily loss
 * limit, no consecutive-loss breaker, no cap on concurrent positions, no
 * cooldown after a stop-out, and position size was a flat dollar margin per
 * trade regardless of how far away the stop was. A fixed $50 of margin behind a
 * 0.15%-away stop and behind a 1.2%-away stop are two completely different
 * risks wearing the same label.
 *
 * Everything here is enforced before an order can be sent.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.MasisRiskGovernor = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {

  const DEFAULTS = {
    riskPerTradePct: 0.5,        // % of equity risked between entry and stop
    maxDailyLossPct: 3.0,        // hard stop for the session
    maxConcurrentPositions: 2,
    maxCorrelatedPositions: 2,   // BTC/ETH/SOL move together — they are one bet, not three
    consecutiveLossLimit: 3,     // pause after this many losses in a row
    cooldownAfterLossMs: 20 * 60 * 1000,
    cooldownAfterWinMs: 5 * 60 * 1000,
    pauseDurationMs: 60 * 60 * 1000,
    minEquity: 50
  };

  // Everything in this bucket is effectively one directional bet on crypto beta.
  const CORRELATION_GROUPS = {
    MAJORS: ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'DOGEUSDT']
  };

  class RiskGovernor {
    constructor(options = {}) {
      this.config = Object.assign({}, DEFAULTS, options);
      // Injectable clock — cooldowns, pauses and the daily roll must all be
      // measured on the same clock the rest of the system runs on, which under
      // replay is simulated time, not wall time.
      this.now = options.now || (() => Date.now());
      this.sessionStartEquity = 0;
      this.currentEquity = 0;
      this.dayKey = this._dayKey();
      this.realisedPnlToday = 0;
      this.consecutiveLosses = 0;
      this.pausedUntil = 0;
      this.pauseReason = null;
      this.symbolCooldownUntil = {};   // symbol -> timestamp
      this.tradesToday = 0;
      this.rMultiples = [];            // realised R per closed trade, for expectancy
    }

    _dayKey(d) {
      const dt = d || new Date(this.now());
      return `${dt.getUTCFullYear()}-${dt.getUTCMonth() + 1}-${dt.getUTCDate()}`;
    }

    _rollDayIfNeeded() {
      const key = this._dayKey();
      if (key !== this.dayKey) {
        this.dayKey = key;
        this.realisedPnlToday = 0;
        this.tradesToday = 0;
        this.sessionStartEquity = this.currentEquity;
        if (this.pauseReason === 'DAILY_LOSS_LIMIT') {
          this.pausedUntil = 0;
          this.pauseReason = null;
        }
      }
    }

    updateEquity(equity) {
      const e = parseFloat(equity) || 0;
      if (!e) return;
      this.currentEquity = e;
      if (!this.sessionStartEquity) this.sessionStartEquity = e;
      this._rollDayIfNeeded();
    }

    /** Called on every closed trade (including partial scale-outs, with the R
     * attributable to that slice) so the breaker reacts to real outcomes. */
    recordOutcome({ symbol, pnl, rMultiple }) {
      this._rollDayIfNeeded();
      this.realisedPnlToday += pnl;
      this.tradesToday++;
      if (typeof rMultiple === 'number' && isFinite(rMultiple)) this.rMultiples.push(rMultiple);
      if (this.rMultiples.length > 200) this.rMultiples.shift();

      const now = this.now();
      if (pnl < 0) {
        this.consecutiveLosses++;
        this.symbolCooldownUntil[symbol] = now + this.config.cooldownAfterLossMs;
        if (this.consecutiveLosses >= this.config.consecutiveLossLimit) {
          this.pausedUntil = now + this.config.pauseDurationMs;
          this.pauseReason = 'CONSECUTIVE_LOSSES';
        }
      } else {
        this.consecutiveLosses = 0;
        this.symbolCooldownUntil[symbol] = now + this.config.cooldownAfterWinMs;
      }

      // Daily loss limit measured against equity at the start of the day.
      const base = this.sessionStartEquity || this.currentEquity;
      if (base > 0 && this.realisedPnlToday < 0) {
        const lossPct = Math.abs(this.realisedPnlToday) / base * 100;
        if (lossPct >= this.config.maxDailyLossPct) {
          this.pausedUntil = Math.max(this.pausedUntil, this._endOfDayTs());
          this.pauseReason = 'DAILY_LOSS_LIMIT';
        }
      }
    }

    _endOfDayTs() {
      const d = new Date(this.now());
      d.setUTCHours(24, 0, 0, 0);
      return d.getTime();
    }

    /** Expectancy in R — the number that actually says whether the system makes
     * money. Win rate on its own says nothing without average win/loss size. */
    expectancy() {
      if (!this.rMultiples.length) return { sampleSize: 0, expectancyR: 0, winRate: 0, avgWinR: 0, avgLossR: 0 };
      const wins = this.rMultiples.filter(r => r > 0);
      const losses = this.rMultiples.filter(r => r <= 0);
      const avgWin = wins.length ? wins.reduce((a, b) => a + b, 0) / wins.length : 0;
      const avgLoss = losses.length ? Math.abs(losses.reduce((a, b) => a + b, 0) / losses.length) : 0;
      const winRate = this.rMultiples.length ? wins.length / this.rMultiples.length : 0;
      return {
        sampleSize: this.rMultiples.length,
        winRate: +(winRate * 100).toFixed(1),
        avgWinR: +avgWin.toFixed(2),
        avgLossR: +avgLoss.toFixed(2),
        expectancyR: +(winRate * avgWin - (1 - winRate) * avgLoss).toFixed(3)
      };
    }

    /**
     * The gate. Returns { allowed, reasons[] } — every refusal names itself so
     * the operator can see exactly why the system stood down.
     */
    canOpen({ symbol, openPositions = [] }) {
      this._rollDayIfNeeded();
      const now = this.now();
      const reasons = [];

      if (this.pausedUntil > now) {
        const mins = Math.ceil((this.pausedUntil - now) / 60000);
        reasons.push(`Trading paused (${this.pauseReason}) for another ${mins} min`);
      }
      if (this.currentEquity > 0 && this.currentEquity < this.config.minEquity) {
        reasons.push(`Equity ${this.currentEquity.toFixed(2)} is below the ${this.config.minEquity} minimum`);
      }
      const cd = this.symbolCooldownUntil[symbol] || 0;
      if (cd > now) {
        reasons.push(`${symbol} is in cooldown for another ${Math.ceil((cd - now) / 60000)} min after its last trade`);
      }
      if (openPositions.length >= this.config.maxConcurrentPositions) {
        reasons.push(`Already holding ${openPositions.length} positions (limit ${this.config.maxConcurrentPositions})`);
      }
      if (openPositions.some(p => p.symbol === symbol)) {
        reasons.push(`Already holding ${symbol} — no pyramiding`);
      }

      const group = Object.values(CORRELATION_GROUPS).find(g => g.includes(symbol));
      if (group) {
        const inGroup = openPositions.filter(p => group.includes(p.symbol)).length;
        if (inGroup >= this.config.maxCorrelatedPositions) {
          reasons.push(`${inGroup} correlated majors already open — these move together, so that is one bet, not ${inGroup}`);
        }
      }

      const base = this.sessionStartEquity || this.currentEquity;
      if (base > 0 && this.realisedPnlToday < 0) {
        const lossPct = Math.abs(this.realisedPnlToday) / base * 100;
        if (lossPct >= this.config.maxDailyLossPct) {
          reasons.push(`Daily loss limit reached (${lossPct.toFixed(2)}% of starting equity)`);
        }
      }

      return { allowed: reasons.length === 0, reasons };
    }

    /**
     * Size by risk, not by margin. Quantity is set so that entry-to-stop equals
     * exactly `riskPerTradePct` of equity — a wide stop gets a small position,
     * a tight stop gets a larger one, and every trade loses the same amount when
     * it is wrong. That single change is what makes a win rate meaningful.
     */
    sizePosition({ entry, stop, equity, symbol, qtyStep, minQty, maxLeverage = 10 }) {
      const eq = equity || this.currentEquity;
      const riskDist = Math.abs(entry - stop);
      if (!eq || !riskDist || !entry) {
        return { qty: 0, rejected: 'Missing equity, entry or stop' };
      }
      const riskAmount = eq * (this.config.riskPerTradePct / 100);
      let qty = riskAmount / riskDist;

      // Never let risk-sizing push notional past the leverage ceiling.
      const maxNotional = eq * maxLeverage;
      if (qty * entry > maxNotional) {
        qty = maxNotional / entry;
      }

      const step = qtyStep || 0.001;
      qty = Math.floor(qty / step) * step;
      qty = +qty.toFixed(8);

      if (minQty && qty < minQty) {
        return {
          qty: 0,
          rejected: `Risk-based size (${qty}) is below ${symbol}'s minimum order size (${minQty}). Raising size would mean risking more than ${this.config.riskPerTradePct}% of equity on this trade, so the trade is skipped instead.`
        };
      }
      if (qty <= 0) {
        return { qty: 0, rejected: `Risk-based size rounds to zero at ${symbol}'s step size — equity too small for this stop distance` };
      }

      return {
        qty,
        riskAmount: +riskAmount.toFixed(2),
        notional: +(qty * entry).toFixed(2),
        effectiveLeverage: +((qty * entry) / eq).toFixed(2)
      };
    }

    status() {
      const now = this.now();
      const base = this.sessionStartEquity || this.currentEquity;
      return {
        paused: this.pausedUntil > now,
        pauseReason: this.pauseReason,
        pausedForMin: this.pausedUntil > now ? Math.ceil((this.pausedUntil - now) / 60000) : 0,
        consecutiveLosses: this.consecutiveLosses,
        realisedPnlToday: +this.realisedPnlToday.toFixed(2),
        dailyLossPct: base > 0 ? +((Math.abs(Math.min(this.realisedPnlToday, 0)) / base) * 100).toFixed(2) : 0,
        maxDailyLossPct: this.config.maxDailyLossPct,
        tradesToday: this.tradesToday,
        riskPerTradePct: this.config.riskPerTradePct,
        expectancy: this.expectancy()
      };
    }
  }

  return { RiskGovernor, DEFAULTS, CORRELATION_GROUPS };
});
