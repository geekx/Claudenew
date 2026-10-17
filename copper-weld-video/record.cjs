// Usage: node record.cjs [t1 t2 ...]   — with times: write preview stills; without: render all frames.
const { chromium } = require('playwright');
const http = require('http');
const fs = require('fs');
const path = require('path');

const ROOT = __dirname;
const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.json': 'application/json', '.bin': 'application/octet-stream' };
const server = http.createServer((req, res) => {
  const p = path.join(ROOT, decodeURIComponent(req.url.split('?')[0]));
  if (!p.startsWith(ROOT) || !fs.existsSync(p) || fs.statSync(p).isDirectory()) { res.writeHead(404); return res.end(); }
  res.writeHead(200, { 'Content-Type': TYPES[path.extname(p)] || 'application/octet-stream' });
  fs.createReadStream(p).pipe(res);
});

(async () => {
  await new Promise((r) => server.listen(8123, r));
  const browser = await chromium.launch({ args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });
  const page = await browser.newPage({ viewport: { width: 1080, height: 1920 } });
  page.on('console', (m) => console.log('[page]', m.text()));
  page.on('pageerror', (e) => console.error('[pageerror]', e.message));
  await page.goto('http://localhost:8123/scene.html');
  await page.waitForFunction(() => window.READY === true, null, { timeout: 60000 });

  const times = process.argv.slice(2).map(Number);
  if (times.length) {
    fs.mkdirSync(path.join(ROOT, 'preview'), { recursive: true });
    for (const t of times) {
      await page.evaluate((t) => window.render(t), t);
      await page.screenshot({ path: path.join(ROOT, 'preview', `t${t.toFixed(1)}.jpg`), type: 'jpeg', quality: 85 });
    }
  } else {
    const { FPS, DURATION } = await page.evaluate(() => window.META);
    const dir = path.join(ROOT, 'frames');
    fs.mkdirSync(dir, { recursive: true });
    const n = FPS * DURATION;
    const t0 = Date.now();
    for (let f = 0; f < n; f++) {
      await page.evaluate((t) => window.render(t), f / FPS);
      await page.screenshot({ path: path.join(dir, `f${String(f).padStart(4, '0')}.jpg`), type: 'jpeg', quality: 92 });
      if (f % 60 === 0) console.log(`frame ${f}/${n}  ${((Date.now() - t0) / 1000).toFixed(0)}s`);
    }
  }
  await browser.close();
  server.close();
})();
