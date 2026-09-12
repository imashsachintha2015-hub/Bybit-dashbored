#!/usr/bin/env node
/**
 * LOSS FORENSICS — why does the live configuration lose the trades it loses?
 *
 * The engine's deployed config (swarm off, 20bps patient maker) wins 47.7% of
 * its trades and is still profitable, because winners are larger than losers
 * and the maker offset saves the spread on every fill. So "remove the losers"
 * is not automatically an improvement -- Part IX already measured what happens
 * when the analyst swarm filters them: win rate rises 47.7% -> 55.4% and total
 * profit falls from +46.5R to +17.8R, because the winners leave with them.
 *
 * The useful question is not how many trades lose but WHICH KIND of loss they
 * are, because the two kinds have opposite fixes and until now were
 * indistinguishable in the output:
 *
 *   DEAD ON ARRIVAL — the trade never went meaningfully into profit (MFE below
 *     the scratch threshold). The entry was wrong. Fixable only by entry
 *     selection, and Part IX says entry filtering costs more than it saves.
 *
 *   SURRENDERED — the trade reached real profit (MFE >= +0.5R, +1R, ...) and
 *     then gave it all back. The entry was RIGHT. No entry filter addresses
 *     this; it is an exit-management defect, and it is money the system had
 *     already earned and handed back.
 *
 * The split between those two decides what is worth building. It also bounds
 * the prize: the R that surrendered losers reached is the ceiling on what
 * better exit management could recover, and that number is reported directly.
 */
const fs = require('fs');
const path = require('path');

const file = process.argv[2] || path.join(__dirname, 'results', 'loss-forensics.json');
const d = JSON.parse(fs.readFileSync(file, 'utf8'));
const trades = [];
for (const r of d.results) for (const t of (r.v3.trades || [])) trades.push(t);

const withX = trades.filter(t => t.mfeR != null && t.maeR != null);
if (!withX.length) { console.log('No trades carry MFE/MAE — re-run backtest/run-backtest.js after the instrumentation change.'); process.exit(0); }

const wins = withX.filter(t => t.r > 0), losses = withX.filter(t => t.r <= 0);
const sum = a => a.reduce((x, y) => x + y, 0);
const mean = a => a.length ? sum(a) / a.length : 0;
const pct = (a, b) => b ? (100 * a / b).toFixed(1) + '%' : '—';
const line = '─'.repeat(92);

console.log(`\n${line}\n  LOSS FORENSICS — ${withX.length} trades, ${d.results.map(r => r.v3.symbol).join('/')}\n${line}`);
console.log(`  win rate ${pct(wins.length, withX.length)}   avg win ${mean(wins.map(t => t.r)).toFixed(3)}R   avg loss ${mean(losses.map(t => t.r)).toFixed(3)}R`);
console.log(`  total ${sum(withX.map(t => t.r)).toFixed(1)}R   expectancy ${mean(withX.map(t => t.r)).toFixed(3)}R\n`);

// ── The central split ────────────────────────────────────────────────────
console.log(`  ${'─'.repeat(88)}`);
console.log(`  WHAT KIND OF LOSS? (${losses.length} losing trades)`);
console.log(`  ${'─'.repeat(88)}`);
const BANDS = [0.25, 0.5, 0.75, 1.0, 1.5];
const dead = losses.filter(t => t.mfeR < 0.25);
console.log(`  dead on arrival (never reached +0.25R)   ${String(dead.length).padStart(4)}  ${pct(dead.length, losses.length).padStart(7)}  avg ${mean(dead.map(t => t.r)).toFixed(3)}R  — entry was wrong`);
for (const b of BANDS) {
  const g = losses.filter(t => t.mfeR >= b);
  if (!g.length) continue;
  const recoverable = sum(g.map(t => Math.min(t.mfeR, 1.0) - t.r));
  console.log(`  reached +${b.toFixed(2)}R then lost           ${String(g.length).padStart(4)}  ${pct(g.length, losses.length).padStart(7)}  avg ${mean(g.map(t => t.r)).toFixed(3)}R  — gave back up to ${recoverable.toFixed(1)}R`);
}
const surrendered = losses.filter(t => t.mfeR >= 0.5);
console.log(`\n  => ${pct(surrendered.length, losses.length)} of losses were trades that had ALREADY reached +0.5R.`);
console.log(`     Those are exit-management losses, not entry losses. Entry filtering cannot recover them.`);

// ── Are stops being hit by noise? ─────────────────────────────────────────
console.log(`\n  ${'─'.repeat(88)}`);
console.log(`  ARE STOPS TOO TIGHT? (MAE on trades that still WON)`);
console.log(`  ${'─'.repeat(88)}`);
const deepMae = wins.filter(t => t.maeR <= -0.5);
console.log(`  winners that first went to −0.5R or worse  ${String(deepMae.length).padStart(4)}  ${pct(deepMae.length, wins.length).padStart(7)}`);
console.log(`  median MAE across all winners              ${(wins.map(t => t.maeR).sort((a, b) => a - b)[Math.floor(wins.length / 2)] || 0).toFixed(3)}R`);
console.log(`  A large share here means the stop sits inside normal noise: the same distance`);
console.log(`  that stopped the losers was survived by these winners only by luck.`);

// ── Where do losses concentrate? ──────────────────────────────────────────
function breakdown(key, label) {
  console.log(`\n  ${'─'.repeat(88)}\n  LOSSES BY ${label}\n  ${'─'.repeat(88)}`);
  const keys = [...new Set(withX.map(t => t[key] ?? '—'))];
  const rows = keys.map(k => {
    const all = withX.filter(t => (t[key] ?? '—') === k);
    const l = all.filter(t => t.r <= 0);
    const surr = l.filter(t => t.mfeR >= 0.5);
    return { k, n: all.length, wr: all.length ? all.filter(t => t.r > 0).length / all.length : 0,
             totR: sum(all.map(t => t.r)), surr: surr.length, nl: l.length };
  }).sort((a, b) => a.totR - b.totR);
  console.log(`  ${String(label).padEnd(24)}${'n'.padStart(5)}${'WR'.padStart(8)}${'totalR'.padStart(9)}${'surrendered/losses'.padStart(20)}`);
  for (const r of rows) {
    console.log(`  ${String(r.k).slice(0, 23).padEnd(24)}${String(r.n).padStart(5)}${(100 * r.wr).toFixed(1).padStart(7)}%${r.totR.toFixed(1).padStart(9)}${(r.surr + '/' + r.nl).padStart(20)}`);
  }
}
breakdown('reason', 'EXIT REASON');
breakdown('setup', 'SETUP');
breakdown('regime', 'REGIME');
breakdown('grade', 'GRADE');

// ── The prize ─────────────────────────────────────────────────────────────
console.log(`\n  ${'─'.repeat(88)}\n  THE CEILING ON BETTER EXITS\n  ${'─'.repeat(88)}`);
for (const lock of [0.3, 0.5, 0.75]) {
  // Counterfactual: any trade whose MFE exceeded `lock` exits at `lock` instead.
  // Deliberately optimistic -- it assumes the level is reached and filled with
  // no slippage -- so treat it as an upper bound, not a backtest.
  let alt = 0;
  for (const t of withX) alt += t.mfeR >= lock ? lock : t.r;
  const base = sum(withX.map(t => t.r));
  console.log(`  hard exit at +${lock.toFixed(2)}R once reached:  ${alt.toFixed(1)}R vs actual ${base.toFixed(1)}R  (${(alt - base >= 0 ? '+' : '') + (alt - base).toFixed(1)}R)`);
}
console.log(`\n  This is an UPPER BOUND, not a strategy: it assumes every level that was touched`);
console.log(`  would have filled without slippage, and it caps winners as well as saving losers.`);
console.log(`  If capping winners at +0.5R still beats the actual result, the exits are leaving`);
console.log(`  money on the table. If it does not, the current exits are already close to right.\n`);
