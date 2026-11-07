// Render index.html to an A4-landscape PDF (and optional PNG previews of every page).
// usage, from the brandbook/ folder:
//   node tools/build-pdf.js                 -> ALOQA-radio-brandbook.pdf
//   node tools/build-pdf.js --png <dir>     -> also <dir>/page-01.png ...
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

(async () => {
  const root = path.resolve(__dirname, '..');
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1300, height: 900 } });
  // index.html is an Artifact body (no doctype/head); wrap it in a standards-mode document for printing
  const tmp = path.join(root, '.print.html');
  fs.writeFileSync(tmp, '<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body>\n'
    + fs.readFileSync(path.join(root, 'index.html'), 'utf8') + '\n</body></html>');
  await page.goto('file://' + tmp, { waitUntil: 'networkidle' });
  await page.evaluate(() => document.fonts.ready);
  await page.pdf({ path: path.join(root, 'ALOQA-radio-brandbook.pdf'), preferCSSPageSize: true, printBackground: true });

  const i = process.argv.indexOf('--png');
  if (i > -1) {
    const out = path.resolve(process.argv[i + 1] || root);
    await page.emulateMedia({ media: 'print' });
    const sheets = await page.$$('.page');
    for (let n = 0; n < sheets.length; n++) {
      await sheets[n].screenshot({ path: path.join(out, `page-${String(n + 1).padStart(2, '0')}.png`) });
    }
  }
  await browser.close();
  fs.unlinkSync(tmp);
  console.log('pdf ok');
})();
