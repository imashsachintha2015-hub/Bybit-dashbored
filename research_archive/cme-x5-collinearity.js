// Why did Part XVII's shuffle test say BA was the biggest lever while Part A's
// refit-and-remove test says it contributes nothing? Collinearity is the obvious
// suspect: BREADTH_M is 89.5% BTC/ETH by dollar-volume weight, so for BTC and ETH
// the BA term is built largely out of the same momentum the M_COH term measures.
// Ridge splits weight across a collinear pair, which makes the output highly
// sensitive to EACH of them individually (big shuffle effect) while neither carries
// independent information (no removal cost). Measure the correlation directly.
const M = require('./cme-x5-normalization.js');
const S = require('./lib-stats.js');
const uni = M.buildUniverse(M.CORE_COINS);
const { IDX } = M;
console.log('corr(M_COH, BA) per coin, 6-coin universe, 418 days:');
for (const s of uni.coins) {
  const rows = uni.rows[s];
  const a = rows.map(r => r.x[IDX.M_COH]), b = rows.map(r => r.x[IDX.BA]);
  const raw = rows.map(r => r.raw.M);
  console.log(`  ${s.padEnd(10)} n=${String(rows.length).padStart(6)}  corr(M_COH,BA)=${S.corr(a,b).toFixed(3)}   corr(rawM,BA)=${S.corr(raw,b).toFixed(3)}`);
}
