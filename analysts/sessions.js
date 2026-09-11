/**
 * ANALYST — Session & Time.
 *
 * Crypto never closes, which is exactly why time matters more than people
 * assume rather than less. The same chart pattern at 03:00 UTC in a thin Asian
 * book and at 14:30 UTC as New York opens are not the same trade: one is far
 * more likely to be a stop-run through an empty book, the other has the
 * participation to sustain a move.
 *
 * Neither V2 nor V3 had any concept of time of day. Both would take a breakout
 * at the thinnest hour of the week with the same confidence as one at the
 * busiest.
 *
 * Reads:
 *  - Which session is active, and its typical character.
 *  - "Killzones": the first hours of London and New York, where the day's
 *    displacement most often originates.
 *  - Thin-book hours and the weekend, where false breaks concentrate.
 *  - The daily/weekly open, which is the reference price for most desks.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.AnalystSessions = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {

  function analyse(ctx) {
    const now = new Date(ctx.now || Date.now());
    const hour = now.getUTCHours();
    const day = now.getUTCDay(); // 0 Sun .. 6 Sat

    let session, character, liquidity;
    if (hour >= 0 && hour < 7) {
      session = 'ASIA'; liquidity = 0.5;
      character = 'Asian hours: typically range-bound and lower participation. Ranges set here are frequently used as the liquidity that London raids.';
    } else if (hour >= 7 && hour < 12) {
      session = 'LONDON'; liquidity = 0.9;
      character = 'London hours: the first real expansion of the day, and where the Asian range is usually resolved.';
    } else if (hour >= 12 && hour < 16) {
      session = 'LONDON_NY_OVERLAP'; liquidity = 1.0;
      character = 'London/New York overlap: the deepest book of the day. Moves initiated here have the participation to sustain.';
    } else if (hour >= 16 && hour < 21) {
      session = 'NEW_YORK'; liquidity = 0.85;
      character = 'New York hours: good participation, though the afternoon often reverses the morning.';
    } else {
      session = 'LATE_US'; liquidity = 0.4;
      character = 'Late US into Asia: the thinnest book of the day. Displacement here is cheap to manufacture and frequently retraces.';
    }

    const inKillzone = (hour >= 7 && hour < 10) || (hour >= 12 && hour < 15);
    const weekend = day === 0 || day === 6;
    const thin = liquidity <= 0.5 || weekend;

    const evidence = [character];
    if (inKillzone) evidence.push('Inside a killzone (first hours of London or the NY overlap) — the window where the day\'s directional move most often originates.');
    if (weekend) evidence.push('Weekend session: participation is structurally lower and moves are more easily pushed. Weekend breaks are reversed during the Monday open more often than they continue.');

    // Time is never an argument for a direction, so it never joins the
    // opposition. It scales how much the panel's conclusion should be trusted.
    const vetoes = [];
    const concerns = [];
    if (thin) {
      concerns.push({ side: null, weight: 0.3,
        note: `${weekend ? 'Weekend' : session} liquidity is low, so breakouts and stop-runs here are cheap to manufacture and carry a materially higher false-break rate` });
    }

    return {
      id: 'sessions', name: 'Session & Time',
      // Time is never a directional opinion — it is a confidence modifier.
      read: 'NEUTRAL',
      conviction: 0,
      evidence, levels: [], vetoes, concerns,
      confidenceScale: thin ? 0.75 : (inKillzone ? 1.1 : 1.0),
      facts: { session, hourUtc: hour, liquidityFactor: liquidity, inKillzone, weekend, thin }
    };
  }

  return { analyse };
});
