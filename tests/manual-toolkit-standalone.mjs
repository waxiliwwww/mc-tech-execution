// Visible documentation behavior: one copied HTML must display its own figures and theme.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';

export async function checkStandaloneToolkit(browser, root, out) {
  await fs.mkdir(out,{recursive:true});
  const html = await fs.readFile(path.join(root,'docs/toolkit.html'),'utf8');
  const temporary = await fs.mkdtemp(path.join(os.tmpdir(),'mc-toolkit-standalone-'));
  const screenshots = [];
  async function inspect(name, content) {
    const directory = path.join(temporary,name);
    await fs.mkdir(directory);
    const target = path.join(directory,'toolkit.html');
    await fs.writeFile(target,content);
    const allowedDocument = pathToFileURL(target).href;
    const context = await browser.newContext({viewport:{width:1400,height:1000},reducedMotion:'reduce'});
    const blocked = [],errors=[];
    const page = await context.newPage();
    page.on('pageerror',e=>errors.push(e.message));
    page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
    await context.route('**/*',route=>{
      const request=route.request();
      if(request.isNavigationRequest() && request.url()===allowedDocument) return route.continue();
      blocked.push({url:request.url(),type:request.resourceType()});
      return route.abort('blockedbyclient');
    });
    try {
      await page.goto(allowedDocument,{waitUntil:'load'});
      // Detail diagrams are intentionally folded for developer overview reading.
      await page.locator('details').evaluateAll(nodes=>nodes.forEach(node=>{node.open=true;}));
      const images=await page.locator('.diagram img').evaluateAll(nodes=>nodes.map(img=>({
        complete:img.complete,width:img.naturalWidth,height:img.naturalHeight,
        embedded:img.src.startsWith('data:image/png;base64,')})));
      const theme=await page.evaluate(()=>({colorScheme:getComputedStyle(document.documentElement).colorScheme,
        background:getComputedStyle(document.body).backgroundColor}));
      const filename=name+'-top.png';await page.screenshot({path:path.join(out,filename)});screenshots.push(filename);
      if(name==='embedded') {
        assert.equal(images.length,5,'Copied architecture must retain overview, M1, M2, M3 and process diagrams.');
        assert.ok(images.every(img=>img.complete&&img.width>0&&img.height>0&&img.embedded),
          'Each diagram must decode from an embedded PNG without assets.');
        assert.equal(theme.colorScheme,'dark','The copied HTML must retain its theme without a stylesheet file.');
        assert.notEqual(theme.background,'rgba(0, 0, 0, 0)');assert.notEqual(theme.background,'rgb(255, 255, 255)');
        assert.equal(await page.locator('.diagram-title').count(),5);
        for(let index=0;index<5;index++) {
          const name='embedded-diagram-'+(index+1)+'.png';
          await page.locator('.diagram').nth(index).screenshot({path:path.join(out,name)});screenshots.push(name);
        }
        await page.setViewportSize({width:390,height:844});
        assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),
          'The isolated document must keep diagrams inside its own horizontal scrollers.');
        const mobile='embedded-mobile.png';await page.screenshot({path:path.join(out,mobile)});screenshots.push(mobile);
        assert.deepEqual(blocked,[],'The self-contained document must not attempt any asset requests.');
        assert.deepEqual(errors,[]);
      }
      return {images,theme,blockedRequests:blocked,consoleErrors:errors};
    } finally {await context.close();}
  }
  try {
    // Reproduce the previous external-resource form while keeping the same text.
    // Neither fixture receives its assets; all non-document requests are denied.
    const external = html.replace(/<style data-toolkit-inline>[\s\S]*?<\/style>/,
      '<link rel="stylesheet" href="assets/architecture.css">').replace(/<img\b[^>]*>/g,tag=>{
        const source=tag.match(/\bdata-diagram-source="([^"]+)"/);
        return source?tag.replace(/\bsrc="[^"]*"/,'src="'+source[1]+'"'):tag;
      });
    const baseline=await inspect('external-assets',external);
    assert.equal(baseline.images.length,5);
    assert.ok(baseline.images.every(img=>img.width===0),
      'The external-asset control must reproduce missing diagrams under the same restrictions.');
    assert.ok(baseline.blockedRequests.some(request=>request.type==='image'));
    const embedded=await inspect('embedded',html);
    const receipt={passed:true,scope:'Single copied toolkit.html, no asset files, all non-document requests blocked',
      externalAssetControl:baseline,embedded,screenshots,
      boundary:'Playwright file:// verification, not a claim of Codex in-app visual acceptance.'};
    await fs.writeFile(path.join(out,'receipt.json'),JSON.stringify(receipt,null,2)+'\n');
    return receipt;
  } finally {await fs.rm(temporary,{recursive:true,force:true});}
}

if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url)) {
  const {chromium}=await import(process.env.MC_PLAYWRIGHT_MODULE||'playwright');
  const browser=await chromium.launch({headless:true,
    ...(process.env.MC_CHROMIUM_EXECUTABLE?{executablePath:process.env.MC_CHROMIUM_EXECUTABLE}:{})});
  try {
    const root=path.resolve(process.argv[2]||'.');
    const out=path.resolve(process.argv[3]||'artifacts/toolkit-standalone');
    await checkStandaloneToolkit(browser,root,out);
    process.stdout.write('Standalone toolkit HTML passed; missing-asset control reproduced. Screenshots: '+out+'\n');
  } finally {await browser.close();}
}
