/**
 * Candle-derived order-flow proxy for the backtest.
 *
 * The live engine reads the real trade tape and order book. Historical klines
 * carry neither, so the backtest substitutes proxies derived from bar shape and
 * volume. This is stated plainly in the report because it matters for how the
 * results should be read:
 *
 *   - taker ratio is approximated by where the close sits within the bar's
 *     range, volume-weighted. A bar closing on its high on heavy volume is
 *     treated as buyer-dominated. This is a reasonable but imperfect stand-in;
 *     real delta can diverge from bar shape, which is exactly why the delta
 *     divergence signal exists in the live engine.
 *   - absorption is approximated by high relative volume with a small body —
 *     a lot of trading that went nowhere.
 *   - the order book has no historical analogue at all, so imbalance is
 *     reported as neutral and spread as a fixed typical value.
 *
 * Net effect: backtest scores for flow-weighted components are noisier than
 * live ones, and the absorption-dependent playbooks (RANGE_FADE especially)
 * fire less accurately here than they will on real tape. Treat the backtest as
 * a test of the STRUCTURE and RISK logic, which is where the previous system's
 * losses actually came from, rather than as a forecast of live win rate.
 */
const I = require('../agents/indicators.js');

function barTakerRatio(candle) {
  const range = candle.high - candle.low;
  if (range <= 0) return 0.5;
  return I.clamp((candle.close - candle.low) / range, 0, 1);
}

class FlowProxy {
  constructor(typicalSpreadPct = 0.02) {
    this.typicalSpreadPct = typicalSpreadPct;
    this.mtf = [];
    this.cumDelta = 0;
  }

  /** Called once per confirmed MTF bar as the backtest walks forward. */
  push(candle) {
    this.mtf.push(candle);
    if (this.mtf.length > 300) this.mtf.shift();
    const ratio = barTakerRatio(candle);
    this.cumDelta += (ratio - 0.5) * 2 * candle.volume * candle.close;
  }

  _windowRatio(bars) {
    const slice = this.mtf.slice(-bars);
    if (!slice.length) return 0.5;
    let weighted = 0, vol = 0;
    for (const c of slice) {
      weighted += barTakerRatio(c) * c.volume;
      vol += c.volume;
    }
    return vol > 0 ? weighted / vol : 0.5;
  }

  bookState() {
    // Historical klines carry no book. A synthetic two-sided quote is derived
    // from the last close and a typical spread so execution-quality checks
    // behave the same way they will live, rather than seeing an absent book.
    const last = this.mtf.length ? this.mtf[this.mtf.length - 1].close : 0;
    const half = last * (this.typicalSpreadPct / 100) / 2;
    return {
      available: last > 0, proxy: true,
      imbalance: 0.5,
      bestBid: last > 0 ? last - half : 0,
      bestAsk: last > 0 ? last + half : 0,
      mid: last,
      bidDepth: 0, askDepth: 0,
      spread: half * 2,
      spreadPct: this.typicalSpreadPct
    };
  }

  detectAbsorption(lastCandle, atrValue) {
    const none = { detected: false, side: 'NONE', strength: 0, evidence: [] };
    if (!lastCandle || !atrValue || this.mtf.length < 20) return none;
    const avgVol = I.sma(this.mtf.slice(-20).map(c => c.volume), 20);
    if (!avgVol) return none;

    const volRatio = lastCandle.volume / avgVol;
    const range = lastCandle.high - lastCandle.low;
    if (range <= 0 || volRatio < 1.3) return none;

    // Wick analysis is the honest candle-based proxy for absorption: a long
    // tail on heavy volume means price was pushed to a level and pushed back
    // from it, which is what a passive defender at that level looks like when
    // you can only see bars. Bar-close position alone (the first version of
    // this) misses it whenever the bar closes mid-range after a deep rejection.
    const upperWick = lastCandle.high - Math.max(lastCandle.open, lastCandle.close);
    const lowerWick = Math.min(lastCandle.open, lastCandle.close) - lastCandle.low;
    const body = Math.abs(lastCandle.close - lastCandle.open);
    const displaced = body / atrValue;
    if (displaced > 0.5) return none; // price genuinely moved — nothing absorbed

    const lowerShare = lowerWick / range;
    const upperShare = upperWick / range;

    if (lowerShare >= 0.45) {
      const strength = Math.min(100, Math.round(lowerShare * 70 + (volRatio - 1.3) * 30));
      return { detected: true, side: 'BULLISH', strength, proxy: true,
        evidence: [`Proxy: ${(lowerShare * 100).toFixed(0)}% of the bar's range is lower wick on ${volRatio.toFixed(1)}x average volume — sellers pushed price down and were pushed back out`] };
    }
    if (upperShare >= 0.45) {
      const strength = Math.min(100, Math.round(upperShare * 70 + (volRatio - 1.3) * 30));
      return { detected: true, side: 'BEARISH', strength, proxy: true,
        evidence: [`Proxy: ${(upperShare * 100).toFixed(0)}% of the bar's range is upper wick on ${volRatio.toFixed(1)}x average volume — buyers pushed price up and were pushed back out`] };
    }
    return none;
  }

  detectFlowBurst() {
    if (this.mtf.length < 20) return { detected: false, side: 'NEUTRAL', count: 0, concentration: 0 };
    const avgVol = I.sma(this.mtf.slice(-20).map(c => c.volume), 20);
    const last = this.mtf[this.mtf.length - 1];
    const ratio = barTakerRatio(last);
    const detected = avgVol > 0 && last.volume / avgVol > 2.2 && (ratio > 0.7 || ratio < 0.3);
    return {
      detected, proxy: true,
      side: detected ? (ratio > 0.5 ? 'BUY' : 'SELL') : 'NEUTRAL',
      count: 0, share: 0, concentration: 0
    };
  }

  detectSpoof() { return { detected: false, side: null, proxy: true }; }

  detectDeltaDivergence(candles, lookback = 12) {
    const none = { detected: false, side: 'NONE' };
    if (!candles || candles.length < lookback + 12) return none;
    const recent = candles.slice(-lookback);
    const prices = recent.map(c => c.close);
    const newHigh = prices[prices.length - 1] >= Math.max(...prices);
    const newLow = prices[prices.length - 1] <= Math.min(...prices);

    const deltaOf = (bars) => bars.reduce((s, c) => s + (barTakerRatio(c) - 0.5) * 2 * c.volume, 0);
    const recentDelta = deltaOf(recent);
    const priorDelta = deltaOf(candles.slice(-(lookback * 2), -lookback));

    if (newHigh && recentDelta < priorDelta) {
      return { detected: true, side: 'BEARISH', proxy: true, evidence: ['Proxy: new local high on weaker buy-side bar structure than the prior leg'] };
    }
    if (newLow && recentDelta > priorDelta) {
      return { detected: true, side: 'BULLISH', proxy: true, evidence: ['Proxy: new local low on weaker sell-side bar structure than the prior leg'] };
    }
    return none;
  }

  snapshot(lastCandle, atrValue, candles) {
    const r1 = this._windowRatio(3);
    const r5 = this._windowRatio(12);
    return {
      available: true,
      proxy: true,
      flow1m: { available: true, ratio: r1, delta: 0, sampleSize: 3, concentration: 0, buyCount: 0, sellCount: 0 },
      flow5m: { available: true, ratio: r5, delta: 0, sampleSize: 12, concentration: 0, buyCount: 0, sellCount: 0 },
      book: this.bookState(),
      absorption: this.detectAbsorption(lastCandle, atrValue),
      burst: this.detectFlowBurst(),
      spoof: this.detectSpoof(),
      divergence: this.detectDeltaDivergence(candles),
      cumDelta: this.cumDelta
    };
  }
}

module.exports = { FlowProxy, barTakerRatio };

// No-op ingestion hooks so a FlowProxy can be dropped into the engine in place
// of the live FlowAgent without the engine needing to know which it holds.
FlowProxy.prototype.ingestTrades = function () {};
FlowProxy.prototype.ingestBook = function () {};
FlowProxy.prototype.reset = function () { this.mtf = []; this.cumDelta = 0; };
