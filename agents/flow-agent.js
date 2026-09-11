/**
 * DIV-02/04 — Order Flow & Microstructure Agent.
 *
 * Owns everything derived from the live tape and book: cumulative delta, taker
 * aggression, passive absorption, distributed flow bursts and spoofed-wall
 * detection. Kept separate from the decision engine so the engine can run
 * headless in the backtest (where there is no tape) with flow evidence simply
 * marked UNAVAILABLE instead of silently defaulting to "confirmed".
 *
 * That distinction matters: the old engine treated a missing/neutral flow
 * reading the same as a confirming one in several branches, which let setups
 * through on price positioning alone.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.MasisFlowAgent = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {

  class FlowAgent {
    constructor(options = {}) {
      this.maxTrades = options.maxTrades || 1200;
      this.trades = [];
      this.book = { bids: [], asks: [] };
      this.bookHistory = [];   // top-of-book size snapshots, for spoof detection
      this.deltaHistory = [];  // { t, cumDelta } for divergence analysis
      this.cumDelta = 0;
    }

    reset() {
      this.trades = [];
      this.book = { bids: [], asks: [] };
      this.bookHistory = [];
      this.deltaHistory = [];
      this.cumDelta = 0;
    }

    ingestTrades(arr) {
      if (!Array.isArray(arr) || !arr.length) return;
      const now = Date.now();
      for (const t of arr) {
        const price = parseFloat(t.p || 0);
        const size = parseFloat(t.v || 0);
        if (!price || !size) continue;
        const notional = price * size;
        const isBuy = String(t.S || '').toLowerCase() === 'buy';
        this.trades.push({ t: parseInt(t.T) || now, price, size, notional, isBuy });
        this.cumDelta += isBuy ? notional : -notional;
      }
      if (this.trades.length > this.maxTrades) {
        this.trades.splice(0, this.trades.length - this.maxTrades);
      }
      this.deltaHistory.push({ t: now, cumDelta: this.cumDelta });
      if (this.deltaHistory.length > 600) this.deltaHistory.shift();
    }

    ingestBook(book) {
      if (!book) return;
      this.book = { bids: book.bids || [], asks: book.asks || [] };
      const bid = this.book.bids.length ? parseFloat(this.book.bids[0][1]) : 0;
      const ask = this.book.asks.length ? parseFloat(this.book.asks[0][1]) : 0;
      this.bookHistory.push({ t: Date.now(), bid, ask });
      if (this.bookHistory.length > 40) this.bookHistory.shift();
    }

    /** Taker aggression over a time window, returned as both a ratio and a
     * notional delta so callers can judge *conviction* as well as direction. */
    windowFlow(windowMs = 60000) {
      const cutoff = Date.now() - windowMs;
      const recent = this.trades.filter(t => t.t >= cutoff);
      let buy = 0, sell = 0, buyCount = 0, sellCount = 0, maxSingle = 0;
      for (const t of recent) {
        if (t.isBuy) { buy += t.notional; buyCount++; } else { sell += t.notional; sellCount++; }
        if (t.notional > maxSingle) maxSingle = t.notional;
      }
      const total = buy + sell;
      return {
        available: recent.length >= 10,
        sampleSize: recent.length,
        buyNotional: buy,
        sellNotional: sell,
        delta: buy - sell,
        ratio: total > 0 ? buy / total : 0.5,
        concentration: total > 0 ? maxSingle / total : 0,
        buyCount, sellCount
      };
    }

    /** Order book imbalance across the top N levels, plus the spread — the two
     * execution-quality inputs. Depth-weighted, not level-count-weighted. */
    bookState(levels = 20) {
      const bids = this.book.bids || [];
      const asks = this.book.asks || [];
      if (!bids.length || !asks.length) {
        return { available: false, imbalance: 0.5, spreadPct: 0, bestBid: 0, bestAsk: 0, mid: 0 };
      }
      let bidVol = 0, askVol = 0;
      for (const b of bids.slice(0, levels)) bidVol += parseFloat(b[1]);
      for (const a of asks.slice(0, levels)) askVol += parseFloat(a[1]);
      const total = bidVol + askVol;
      const bestBid = parseFloat(bids[0][0]);
      const bestAsk = parseFloat(asks[0][0]);
      const mid = (bestBid + bestAsk) / 2;
      return {
        available: true,
        imbalance: total > 0 ? bidVol / total : 0.5,
        bidDepth: bidVol,
        askDepth: askVol,
        bestBid, bestAsk, mid,
        spread: bestAsk - bestBid,
        spreadPct: mid > 0 ? ((bestAsk - bestBid) / mid) * 100 : 0
      };
    }

    /**
     * Passive absorption: aggressive flow is hitting one side hard but price is
     * not displacing, and the book on the opposing side is holding. That is a
     * large passive participant taking the other side of the aggression — the
     * highest-quality reversal evidence available from public data.
     *
     * Requires a CONFIRMED candle so "price didn't move" is a fact, not a
     * half-formed bar that still has 40 seconds to move.
     */
    detectAbsorption(lastConfirmedCandle, atrValue) {
      const flow = this.windowFlow(90000);
      const book = this.bookState();
      const none = { detected: false, side: 'NONE', strength: 0, evidence: [] };
      if (!flow.available || !book.available || !lastConfirmedCandle || !atrValue) return none;

      const body = Math.abs(lastConfirmedCandle.close - lastConfirmedCandle.open);
      const range = lastConfirmedCandle.high - lastConfirmedCandle.low;
      const displaced = body / atrValue;
      if (displaced > 0.35) return none; // price DID move — nothing was absorbed

      // Sellers hitting the bid, price holding, bids stacked → bullish absorption
      if (flow.ratio <= 0.38 && book.imbalance >= 0.55) {
        const strength = Math.min(100, Math.round((0.38 - flow.ratio) * 200 + (book.imbalance - 0.55) * 150 + (0.35 - displaced) * 60));
        return {
          detected: true, side: 'BULLISH', strength,
          evidence: [`Sell aggression ${((1 - flow.ratio) * 100).toFixed(0)}% of tape over 90s displaced price only ${displaced.toFixed(2)} ATR while bids hold ${(book.imbalance * 100).toFixed(0)}% of top-20 depth — passive bid absorbing the offer`]
        };
      }
      // Buyers lifting the offer, price holding, asks stacked → bearish absorption
      if (flow.ratio >= 0.62 && book.imbalance <= 0.45) {
        const strength = Math.min(100, Math.round((flow.ratio - 0.62) * 200 + (0.45 - book.imbalance) * 150 + (0.35 - displaced) * 60));
        return {
          detected: true, side: 'BEARISH', strength,
          evidence: [`Buy aggression ${(flow.ratio * 100).toFixed(0)}% of tape over 90s displaced price only ${displaced.toFixed(2)} ATR while asks hold ${((1 - book.imbalance) * 100).toFixed(0)}% of top-20 depth — passive offer absorbing the bid`]
        };
      }
      void range;
      return none;
    }

    /**
     * Distributed flow burst: many independent prints hitting one side with no
     * single print dominating. This is the honest public-tape proxy for "a lot
     * of participants are doing the same thing at once", as distinct from one
     * large order (which the whale tracker handles separately).
     */
    detectFlowBurst(windowMs = 45000) {
      const f = this.windowFlow(windowMs);
      const none = { detected: false, side: 'NEUTRAL', count: 0, concentration: 0 };
      if (!f.available) return none;
      const dominantCount = Math.max(f.buyCount, f.sellCount);
      const totalCount = f.buyCount + f.sellCount;
      const share = totalCount > 0 ? dominantCount / totalCount : 0;
      const detected = dominantCount >= 25 && share >= 0.68 && f.concentration < 0.22;
      return {
        detected,
        side: detected ? (f.buyCount > f.sellCount ? 'BUY' : 'SELL') : 'NEUTRAL',
        count: dominantCount,
        share: +share.toFixed(2),
        concentration: +f.concentration.toFixed(2)
      };
    }

    /**
     * Spoof guard. Never claims proven manipulation — flags the *pattern*: a
     * resting wall several multiples of the recent median appears at the top of
     * book and then disappears without being traded through. Leaning on a level
     * that was never real is a reliable way to get stopped out.
     */
    detectSpoof() {
      if (this.bookHistory.length < 8) return { detected: false, side: null };
      const med = (arr) => {
        const s = arr.slice().sort((a, b) => a - b);
        const m = Math.floor(s.length / 2);
        return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
      };
      const bids = this.bookHistory.map(h => h.bid);
      const asks = this.bookHistory.map(h => h.ask);
      const medBid = med(bids.slice(0, -2)) || 1e-9;
      const medAsk = med(asks.slice(0, -2)) || 1e-9;
      const recentMaxBid = Math.max(...bids.slice(-5, -1));
      const recentMaxAsk = Math.max(...asks.slice(-5, -1));
      const lastBid = bids[bids.length - 1];
      const lastAsk = asks[asks.length - 1];

      if (recentMaxBid > medBid * 5 && lastBid < medBid * 1.25) return { detected: true, side: 'BID' };
      if (recentMaxAsk > medAsk * 5 && lastAsk < medAsk * 1.25) return { detected: true, side: 'ASK' };
      return { detected: false, side: null };
    }

    /**
     * Delta divergence: price makes a new extreme but cumulative delta does not
     * follow. Classic exhaustion tell, and one of the few flow signals that
     * genuinely leads price rather than confirming it after the fact.
     */
    detectDeltaDivergence(confirmedCandles, lookback = 12) {
      const none = { detected: false, side: 'NONE' };
      if (!confirmedCandles || confirmedCandles.length < lookback) return none;
      if (this.deltaHistory.length < 30) return none;

      const recent = confirmedCandles.slice(-lookback);
      const prices = recent.map(c => c.close);
      const newHigh = prices[prices.length - 1] >= Math.max(...prices);
      const newLow = prices[prices.length - 1] <= Math.min(...prices);

      const half = Math.floor(this.deltaHistory.length / 2);
      const earlyDelta = this.deltaHistory[half].cumDelta;
      const nowDelta = this.deltaHistory[this.deltaHistory.length - 1].cumDelta;

      if (newHigh && nowDelta < earlyDelta) {
        return { detected: true, side: 'BEARISH', evidence: ['Price at a new local high while cumulative delta is falling — the move is not being paid for by aggressive buyers'] };
      }
      if (newLow && nowDelta > earlyDelta) {
        return { detected: true, side: 'BULLISH', evidence: ['Price at a new local low while cumulative delta is rising — sellers are not following through'] };
      }
      return none;
    }

    /** Everything a decision needs, in one call. */
    snapshot(lastConfirmedCandle, atrValue, confirmedCandles) {
      const flow1m = this.windowFlow(60000);
      const flow5m = this.windowFlow(300000);
      return {
        available: flow1m.available,
        flow1m,
        flow5m,
        book: this.bookState(),
        absorption: this.detectAbsorption(lastConfirmedCandle, atrValue),
        burst: this.detectFlowBurst(),
        spoof: this.detectSpoof(),
        divergence: this.detectDeltaDivergence(confirmedCandles),
        cumDelta: this.cumDelta
      };
    }
  }

  return { FlowAgent };
});
