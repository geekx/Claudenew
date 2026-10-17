// Renders laptop.png (shell) and glare.png (screen reflection) from laptop.html.
const { chromium } = require('playwright');
const path = require('path');
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  for (const [q, out] of [['', 'laptop.png'], ['?glare', 'glare.png']]) {
    await page.goto('file://' + path.join(__dirname, 'laptop.html') + q);
    await page.waitForFunction(() => window.READY === true);
    await page.screenshot({ path: path.join(__dirname, out), omitBackground: true });
  }
  await browser.close();
})();
