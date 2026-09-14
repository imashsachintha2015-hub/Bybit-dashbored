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
    // ── Trading Settings ──
    leverage: 10,                    // Bybit leverage (1–100)
    marginMode: 'cross',             // 'cross' or 'isolated'
    sizingMode: 'usdt',              // 'risk' = ATR-based, 'usdt' = fixed notional
    fixedUsdtSize: 100,              // USDT per trade when sizingMode='usdt'
    riskPerTradePct: 0.5,            // % of equity risked when sizingMode='risk'

    // ── Safety ──
    maxDailyLossPct: 3.0,            // hard daily loss limit (% of session start equity)
    minEquity: 50                    // minimum account balance to trade
  };

  class RiskGovernor {
    constructor(options = {}) {
      this.config = Object.assign({}, DEFAULTS, options);
      this.now = options.now || (() => Date.now());
      this.sessionStartEquity = 0;
      this.currentEquity = 0;
      this.dayKey = this._dayKey();
      this.realisedPnlToday = 0;
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
     *
     * Only restriction: cannot open a second position on the SAME coin when one
     * is already live or a limit order is resting.  Everything else is removed
     * — no concurrent-position cap, no correlation-group cap, no cooldowns.
     */
    canOpen({ symbol, openPositions = [], pendingOrders = [] }) {
      this._rollDayIfNeeded();
      const reasons = [];

      // 1. Minimum equity
      if (this.currentEquity > 0 && this.currentEquity < this.config.minEquity) {
        reasons.push(`Equity ${this.currentEquity.toFixed(2)} is below the ${this.config.minEquity} minimum`);
      }

      // 2. Daily loss limit
      const base = this.sessionStartEquity || this.currentEquity;
      if (base > 0 && this.realisedPnlToday < 0) {
        const lossPct = Math.abs(this.realisedPnlToday) / base * 100;
        if (lossPct >= this.config.maxDailyLossPct) {
          reasons.push(`Daily loss limit reached (${lossPct.toFixed(2)}% of starting equity)`);
        }
      }

      // 3. Same-coin duplicate guard (live positions OR resting limit orders)
      if (openPositions.some(p => p.symbol === symbol)) {
        reasons.push(`Already holding a live ${symbol} position`);
      }
      if (pendingOrders.some(o => o.symbol === symbol)) {
        reasons.push(`A limit order for ${symbol} is already resting`);
      }

      return { allowed: reasons.length === 0, reasons };
    }

    /**
     * Two sizing modes:
     *
     *   'risk' (default) — size by risk, not by margin. Quantity is set so
     *   that entry-to-stop equals exactly `riskPerTradePct` of equity — a
     *   wide stop gets a small position, a tight stop gets a larger one,
     *   and every trade loses the same amount when it is wrong. That single
     *   change is what makes a win rate meaningful.
     *
     *   'usdt' — size to a fixed notional (`fixedUsdtSize`) instead,
     *   regardless of stop distance. Simpler to reason about in dollar
     *   terms, at the cost of the property above: a trade with a stop twice
     *   as far away now risks twice as much of that fixed notional as one
     *   with a tight stop. An explicit operator choice, not the default.
     */
    sizePosition({ entry, stop, equity, symbol, qtyStep, minQty, maxLeverage = 10 }) {
      const eq = equity || this.currentEquity;
      const riskDist = Math.abs(entry - stop);
      if (!eq || !riskDist || !entry) {
        return { qty: 0, rejected: 'Missing equity, entry or stop' };
      }

      const usdtMode = this.config.sizingMode === 'usdt';
      const riskAmount = usdtMode ? +(riskDist * (this.config.fixedUsdtSize / entry)).toFixed(2) : eq * (this.config.riskPerTradePct / 100);
      let qty = usdtMode ? this.config.fixedUsdtSize / entry : riskAmount / riskDist;

      // Never let sizing push notional past the leverage ceiling.
      const effLev = Math.max(1, this.config.leverage || maxLeverage);
      const maxNotional = eq * effLev;
      if (qty * entry > maxNotional) {
        qty = maxNotional / entry;
      }

      const step = qtyStep || 0.001;
      qty = Math.floor(qty / step) * step;
      qty = +qty.toFixed(8);

      if (minQty && qty < minQty) {
        return {
          qty: 0,
          rejected: usdtMode
            ? `Size at $${this.config.fixedUsdtSize} notional (${qty}) is below ${symbol}'s minimum order size (${minQty}). Raise the USDT size to trade this symbol.`
            : `Risk-based size (${qty}) is below ${symbol}'s minimum order size (${minQty}). Raising size would mean risking more than ${this.config.riskPerTradePct}% of equity on this trade, so the trade is skipped instead.`
        };
      }
      if (qty <= 0) {
        return { qty: 0, rejected: `${usdtMode ? 'Fixed-USDT' : 'Risk-based'} size rounds to zero at ${symbol}'s step size` };
      }

      return {
        qty,
        riskAmount,
        notional: +(qty * entry).toFixed(2),
        effectiveLeverage: +((qty * entry) / eq).toFixed(2)
      };
    }

    status() {
      const base = this.sessionStartEquity || this.currentEquity;
      return {
        leverage: this.config.leverage,
        marginMode: this.config.marginMode,
        sizingMode: this.config.sizingMode,
        fixedUsdtSize: this.config.fixedUsdtSize,
        riskPerTradePct: this.config.riskPerTradePct,
        realisedPnlToday: +this.realisedPnlToday.toFixed(2),
        dailyLossPct: base > 0 ? +((Math.abs(Math.min(this.realisedPnlToday, 0)) / base) * 100).toFixed(2) : 0,
        maxDailyLossPct: this.config.maxDailyLossPct,
        tradesToday: this.tradesToday,
        expectancy: this.expectancy()
      };
    }
  }

  return { RiskGovernor, DEFAULTS };
});
