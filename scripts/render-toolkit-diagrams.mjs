#!/usr/bin/env node
// Documentation maintenance only. Readers need neither Node nor Playwright.
// SVG sources stay editable; the HTML contains raster diagrams and all its CSS.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';

const root = path.resolve(process.argv[2] || path.join(path.dirname(fileURLToPath(import.meta.url)), '..'));
const output = path.resolve(process.argv[3] || path.join(root, 'artifacts/toolkit-diagram-render'));
const documentPath = path.join(root, 'docs/toolkit.html');
const original = await fs.readFile(documentPath, 'utf8');
const sha256 = value => createHash('sha256').update(value).digest('hex');
const definitions = [
  ['toolkit-architecture.svg', '图1 · 分层架构'],
  ['toolkit-inputs.svg', '图2 · 三种模型输入流程'],
  ['toolkit-sequence.svg', '图3 · 计算与结果调用时序'],
];
const {chromium} = await import(process.env.MC_PLAYWRIGHT_MODULE || 'playwright');
const browser = await chromium.launch({headless:true,
  ...(process.env.MC_CHROMIUM_EXECUTABLE ? {executablePath:process.env.MC_CHROMIUM_EXECUTABLE} : {})});
await fs.mkdir(output, {recursive:true});
let html = original;
const diagrams = [];
try {
  const context = await browser.newContext({deviceScaleFactor:2, reducedMotion:'reduce'});
  const page = await context.newPage();
  const errors = [], unexpectedRequests = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => {
    if (/^(https?|file):/.test(request.url())) unexpectedRequests.push(request.url());
  });
  for (const [name, title] of definitions) {
    const source = await fs.readFile(path.join(root, 'docs/assets', name), 'utf8');
    await page.setContent('<!doctype html><html><head><meta charset="utf-8"><style>html,body{margin:0;padding:0;background:#111820}svg{display:block}</style></head><body>'+source+'</body></html>');
    await page.evaluate(() => document.fonts.ready);
    const svg = page.locator('svg');
    const size = await svg.boundingBox();
    assert.ok(size && size.width > 0 && size.height > 0, 'SVG must have a visible canvas: '+name);
    await page.setViewportSize({width:Math.ceil(size.width),height:Math.ceil(size.height)});
    const png = await svg.screenshot({type:'png',animations:'disabled'});
    const relative = 'assets/'+name;
    let matches = 0;
    html = html.replace(/<img\b[^>]*>/g, tag => {
      const sourceAttribute = tag.match(/\bdata-diagram-source="([^"]+)"/);
      const sourceUri = tag.match(/\bsrc="([^"]+)"/);
      if ((sourceAttribute?.[1] || sourceUri?.[1]) !== relative) return tag;
      matches++;
      return tag.replace(/\sdata-diagram-(?:source|sha256)="[^"]*"/g, '')
        .replace(/\bsrc="[^"]*"/, 'src="data:image/png;base64,'+png.toString('base64')+'"')
        .replace(/\s*\/?\s*>$/, ' data-diagram-source="'+relative+'" data-diagram-sha256="'+sha256(source)+'">');
    });
    assert.equal(matches,1,'Expected one diagram for '+name);
    html = html.replace(/<figure\b[^>]*>[\s\S]*?<\/figure>/g, figure => {
      if (!figure.includes('data-diagram-source="'+relative+'"')) return figure;
      const heading = '<h3 class="diagram-title">'+title+'</h3>';
      return /<h3 class="diagram-title">[\s\S]*?<\/h3>/.test(figure)
        ? figure.replace(/<h3 class="diagram-title">[\s\S]*?<\/h3>/,heading)
        : figure.replace(/(<figure\b[^>]*>)/,'$1'+heading);
    });
    const preview = path.join(output, name.replace(/\.svg$/,'.png'));
    await fs.writeFile(preview,png);
    diagrams.push({source:relative,sourceSha256:sha256(source),pngSha256:sha256(png),
      pngBytes:png.length,pngWidth:png.readUInt32BE(16),pngHeight:png.readUInt32BE(20),preview});
  }
  assert.deepEqual(errors,[]);
  assert.deepEqual(unexpectedRequests,[]);
  const baseCss = await fs.readFile(path.join(root,'docs/assets/overview.css'),'utf8');
  const architectureCss = (await fs.readFile(path.join(root,'docs/assets/architecture.css'),'utf8'))
    .replace(/^\s*@import\s+url\(['"]?overview\.css['"]?\);\s*/,'');
  const css = baseCss+'\n'+architectureCss+'\n.diagram-title{margin:24px 0 8px;font-size:18px;color:var(--accent)}\n';
  assert.ok(!/@import|url\(/i.test(css),'Standalone toolkit CSS must not request external resources.');
  const style = '<style data-toolkit-inline>'+css+'</style>';
  if (/<style data-toolkit-inline>[\s\S]*?<\/style>/.test(html)) {
    html = html.replace(/<style data-toolkit-inline>[\s\S]*?<\/style>/,style);
  } else {
    const link = '<link rel="stylesheet" href="assets/architecture.css">';
    assert.ok(html.includes(link),'Expected toolkit stylesheet link or existing inline style.');
    html = html.replace(link,style);
  }
  assert.equal(await fs.readFile(documentPath,'utf8'),original,
    'toolkit.html changed during rendering. Retry after preserving the latest edits.');
  await fs.writeFile(documentPath,html);
  const receipt = {passed:true,document:documentPath,htmlBytes:Buffer.byteLength(html),
    htmlSha256:sha256(html),stylesEmbedded:true,diagramCount:diagrams.length,diagrams,
    note:'Documentation rasterization only; no model, CAD conversion or OpenMC calculation.'};
  await fs.writeFile(path.join(output,'receipt.json'),JSON.stringify(receipt,null,2)+'\n');
  process.stdout.write('已更新toolkit.html：3张PNG与样式内嵌；SVG源保留。\n');
} finally {await browser.close();}
