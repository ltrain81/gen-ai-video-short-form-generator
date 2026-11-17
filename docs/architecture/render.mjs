// Renders architecture.html → architecture-light.png / architecture-dark.png (needs: npm i playwright).
import { chromium } from 'playwright';
import path from 'path';
import { fileURLToPath } from 'url';
const dir = path.dirname(fileURLToPath(import.meta.url));
const browser = await chromium.launch();
for (const theme of ['light', 'dark']) {
  const page = await browser.newPage({ viewport: { width: 1680, height: 1100 }, deviceScaleFactor: 2 });
  await page.goto(`file://${dir}/architecture.html`);
  await page.evaluate((t) => document.documentElement.setAttribute('data-theme', t), theme);
  await page.waitForTimeout(300);
  await page.locator('#root').screenshot({ path: `${dir}/architecture-${theme}.png` });
  console.log('wrote', theme);
}
await browser.close();
