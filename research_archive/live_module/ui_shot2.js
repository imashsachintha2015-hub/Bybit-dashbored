const { chromium } = require('/opt/node22/lib/node_modules/playwright');
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
(async () => {
  const b = await chromium.launch();
  const p = await (await b.newContext({ viewport: { width: 1440, height: 1000 }, locale: 'en-US' })).newPage();
  await p.goto('http://localhost:8070/', { waitUntil: 'domcontentloaded' }); await sleep(3500);
  await p.evaluate(() => window.setDashboardStrategyMode('kalman')); await sleep(2500);
  await p.evaluate(() => { document.getElementById('a5').scrollIntoView({ block: 'start' }); }); await sleep(1200);
  await p.screenshot({ path: 'ui_shots/ui_panel_view.png' });
  await p.evaluate(() => { var e = document.getElementById('ktRadar'); e.closest('.kt-scroll').scrollIntoView({ block: 'start' }); }); await sleep(800);
  await p.screenshot({ path: 'ui_shots/ui_panel_radar.png' });
  const op = await p.evaluate(() => { var e = document.getElementById('a5'); var cs = getComputedStyle(e); var r = e.getBoundingClientRect(); return { opacity: cs.opacity, filter: cs.filter, top: r.top, h: r.height }; });
  console.log(JSON.stringify(op));
  await b.close();
})();
