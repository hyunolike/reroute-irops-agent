const { chromium } = require('playwright');
const fs = require('fs');
(async () => {
  fs.mkdirSync('frames', { recursive: true });
  const FPS = 30, DUR = 21.0, total = Math.round(FPS * DUR);
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1920, height: 1080 } });
  p.on('pageerror', e => console.log('ERR', e.message));
  await p.goto('file://' + __dirname + '/index.html');
  await p.evaluate(() => window.ready);
  for (let i = 0; i < total; i++) {
    await p.evaluate(t => render(t), i / FPS);
    await p.screenshot({ path: `frames/f${String(i).padStart(4, '0')}.jpg`, type: 'jpeg', quality: 95 });
  }
  await b.close();
  console.log('frames', total);
})();
