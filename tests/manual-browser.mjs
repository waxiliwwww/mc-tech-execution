// Optional documentation check. Requires Playwright and its Chromium; no product runtime.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { checkStandaloneToolkit } from './manual-toolkit-standalone.mjs';
const { chromium } = await import(process.env.MC_PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(process.argv[2] || '.');
const out = path.resolve(process.argv[3] || 'artifacts/manual-browser');
await fs.mkdir(out, {recursive:true});
const browser = await chromium.launch({headless:true, ...(process.env.MC_CHROMIUM_EXECUTABLE ? {executablePath:process.env.MC_CHROMIUM_EXECUTABLE} : {})});
const context = await browser.newContext({viewport:{width:1400,height:1000},reducedMotion:'reduce'});
const page = await context.newPage();
const problems = [], remoteRequests = [], screenshots = [];
page.on('pageerror', error => problems.push(error.message));
page.on('console', message => {if(message.type()==='error')problems.push(message.text());});
page.on('request', request => {if(/^https?:/.test(request.url()))remoteRequests.push(request.url());});
const url = name => pathToFileURL(path.join(root,'docs',name)).href;
const shot = async name => {await page.screenshot({path:path.join(out,name+'.png')});screenshots.push(name+'.png');};
try {
  await page.goto(url('index.html'));
  await shot('01-entry');
  await page.locator('#demo-options > summary').click();
  assert.equal(await page.locator('#demo-options').getAttribute('open'),'');
  assert.match(await page.locator('#demo-options').innerText(),/--particles/);
  await page.locator('#demo-options').scrollIntoViewIfNeeded();await shot('02-demo-options');
  await page.locator('nav a[href="compute.html"]').click();
  await page.locator('.jump a[href="#change"]').click();
  await page.waitForFunction(()=>Math.abs(document.getElementById('change').getBoundingClientRect().top-28)<4);
  await shot('03-input-change');
  const command = page.locator('pre[data-run="change"]');
  const original = await command.locator('code').textContent();
  await command.locator('button').click();
  await page.waitForFunction(()=>/已复制|已选中/.test(document.querySelector('pre[data-run="change"] button').textContent));
  assert.match(await command.locator('button').innerText(),/已复制|已选中/);
  assert.match(original,/settings\.particles.*400/);
  // Exercise selection fallback too; file viewers may not grant clipboard access.
  await page.evaluate(() => Object.defineProperty(navigator,'clipboard',{value:undefined,configurable:true}));
  await command.locator('button').click();
  assert.equal(await page.evaluate(()=>window.getSelection().toString()),original);
  await page.locator('.jump a[href="#results"]').click();
  await page.waitForFunction(()=>Math.abs(document.getElementById('results').getBoundingClientRect().top-28)<4);
  await shot('04-results-location');
  await page.locator('#report-selection > summary').click();
  assert.match(await page.locator('#report-selection').innerText(),/算术中点/);
  assert.match(await page.locator('#report-selection').innerText(),/全部所选点/);
  await page.locator('#report-selection').scrollIntoViewIfNeeded();
  await shot('04b-selected-report');
  await page.locator('nav a[href="inputs.html"]').click();
  await shot('05-model-layers');
  await page.locator('#native > summary').click();
  assert.match(await page.locator('#native').innerText(),/pincell_multigroup|官方多群例题源码/);
  await page.goto(url('domain-workflows.html'));
  await page.locator('.jump a[href="#build"]').click();
  assert.match(await page.locator('main').innerText(),/openmc_build_model/);
  assert.match(await page.locator('main').innerText(),/model-spec.json/);
  await shot('05b-composable-model');
  await page.locator('nav a[href="development-plan.html"]').click();
  assert.equal(await page.locator('table tbody tr').count(),5);
  assert.match(await page.locator('main').innerText(),/Windows DSH/);
  assert.equal(await page.evaluate(()=>getComputedStyle(document.documentElement).colorScheme),'dark');
  await page.locator('nav a[href="development-details.html"]').click();
  assert.match(await page.locator('main').innerText(),/openmc_import_model/);
  assert.match(await page.locator('main').innerText(),/微调/);
  await shot('05c-development-details');
  await page.goto(url('development-plan.html'));await shot('05d-delivery-overview');
  await page.locator('nav a[href="dsh.html"]').click();
  assert.match(await page.locator('main').innerText(),/PowerShell/);
  await shot('05e-windows-dsh');
  await page.goto(url('index.html'));
  await page.locator('a[href="toolkit.html"]').click();
  assert.match(await page.locator('h1').innerText(),/架构设计/);
  for(const id of ['model-details','job-details','result-details','runtime-details']) {
    assert.equal(await page.locator('#'+id).getAttribute('open'),null,
      'Core implementation detail must start folded: '+id);
  }
  assert.equal(await page.locator('.diagram img').count(),5,
    'Architecture needs overview, three core modules and process timing.');
  await page.locator('.diagram').first().screenshot({path:path.join(out,'05f0-architecture-overview.png')});
  screenshots.push('05f0-architecture-overview.png');
  for (const [index, id] of ['model-details','job-details','result-details','runtime-details'].entries()) {
    const link = page.locator('a[href="#'+id+'"]').first();
    if(await link.count()) await link.click();
    else await page.locator('#'+id+' > summary').click();
    await page.waitForFunction(id=>document.getElementById(id).open,id);
    assert.equal(await page.locator('#'+id).getAttribute('open'),'');
    const diagram=page.locator('#'+id+' > figure .diagram');
    await diagram.scrollIntoViewIfNeeded();
    await page.waitForFunction(()=>[...document.querySelectorAll('.diagram img')]
      .every(img=>img.complete&&img.naturalWidth>0));
    const name='05f'+(index+1)+'-architecture-'+id+'.png';
    await diagram.screenshot({path:path.join(out,name)});screenshots.push(name);
    if(id==='model-details') {
      const optional=page.locator('#python-entry');
      assert.equal(await optional.getAttribute('open'),null,'Existing Python code is an optional folded entrance.');
      await optional.locator('summary').click();
      assert.equal(await optional.getAttribute('open'),'');
      await optional.screenshot({path:path.join(out,'05f1b-optional-python-entry.png')});
      screenshots.push('05f1b-optional-python-entry.png');
      await optional.locator('summary').click();
    }
  }
  // The scene link must reveal both a folded parent and the nested HPC design.
  await page.locator('#job-details > summary').click();
  assert.equal(await page.locator('#job-details').getAttribute('open'),null);
  await page.locator('#scenarios a[href="#hpc-design"]').click();
  await page.waitForFunction(()=>document.getElementById('hpc-design').open&&document.getElementById('job-details').open);
  assert.equal(await page.locator('#job-details').getAttribute('open'),'');
  assert.equal(await page.locator('#hpc-design').getAttribute('open'),'');
  assert.match(await page.locator('#hpc-design > summary').innerText(),/未实现/);
  assert.ok(await page.locator('#hpc-design > summary').evaluate(e=>{
    const r=e.getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight;
  }),'A deep link must reveal its folded implementation detail.');
  await shot('05f5-hpc-design-reveal');
  await page.locator('.diagram a').first().click();
  assert.equal(await page.evaluate(()=>document.documentElement.localName),'svg');
  assert.match(await page.locator('svg > title').textContent(),/总体架构/);
  await page.goBack();
  await page.locator('nav a[href="toolkit-usage.html"]').click();
  assert.match(await page.locator('h1').innerText(),/使用说明/);
  await page.locator('.jump a[href="#using"]').click();
  assert.ok(await page.locator('#using-title').evaluate(e=>{
    const r=e.getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight;
  }));
  await shot('05f6-usage-entrance');
  await page.locator('.jump a[href="#results"]').click();
  await shot('05f7-scientific-result-reading');
  await page.locator('.jump a[href="#files"]').click();
  await shot('05f8-file-formats');
  await page.locator('nav a[href="toolkit.html"]').click();
  assert.match(await page.locator('h1').innerText(),/架构设计/);
  await page.locator('a[href="reference.html"]').first().click();
  assert.equal(await page.locator('#tool-search').isVisible(),true);
  await page.goto(url('toolkit.html'));await shot('05f-toolkit-overview');
  await page.locator('a[href="expert-trial.html"]').click();
  assert.match(await page.locator('h1').innerText(),/专家试用清单/);
  const firstCheck=page.locator('input[type="checkbox"]').first();
  assert.equal(await firstCheck.isChecked(),false);
  await firstCheck.check();assert.equal(await firstCheck.isChecked(),true);
  await page.reload();assert.equal(await firstCheck.isChecked(),false);
  await page.locator('.jump a[href="#test"]').click();await shot('05f9-expert-trial');
  await page.locator('nav a[href="toolkit.html#job-details"]').click();
  await page.waitForFunction(()=>document.getElementById('job-details').open);

  await page.goto(url('index.html'));
  await page.locator('a[href="cad.html"]').click();
  assert.match(await page.locator('#setup').innerText(),/环境/);
  assert.match(await page.locator('main').innerText(),/--cad-only/);
  assert.match(await page.locator('main').innerText(),/--mcp-url/);
  await shot('05g-cad-setup');
  await page.locator('#tools').scrollIntoViewIfNeeded();await shot('05h-cad-tool-flow');
  await page.goto(url('inputs.html'));
  await page.locator('nav a[href="mcp.html"]').click();
  await page.locator('.jump a[href="#register"]').click();
  await page.waitForFunction(()=>Math.abs(document.getElementById('register').getBoundingClientRect().top-28)<4);
  await shot('06-own-tools');
  await page.locator('#program > summary').click();
  assert.match(await page.locator('#program').innerText(),/stdin/);
  await page.goto(url('reference.html'));
  await page.locator('#tool-search').fill('this_tool_does_not_exist');
  assert.equal(await page.locator('#no-results').isVisible(),true);
  await page.locator('#tool-search').fill('openmc_prepare_case');
  assert.equal(await page.locator('#openmc_prepare_case').isVisible(),true);
  await page.locator('#openmc_prepare_case > summary').click();
  assert.match(await page.locator('#openmc_prepare_case').innerText(),/target_dir/);
  assert.match(await page.locator('#openmc_prepare_case').innerText(),/新副本目录/);
  await shot('07-parameter-search');
  await page.goto(url('reference.html')+'#openmc_plot_results');
  assert.equal(await page.locator('#openmc_plot_results').getAttribute('open'),'');
  await page.locator('#openmc_plot_results details').first().locator('summary').click();
  assert.match(await page.locator('#openmc_plot_results details').first().innerText(),/filter_selection/);
  await shot('08-schema-anchor');
  await page.goto(url('official-examples.html'));
  assert.equal(await page.locator('main table').last().locator('tbody tr').count(),50);
  assert.match(await page.locator('main').innerText(),/本轮完成其余48项/);
  assert.match(await page.locator('main').innerText(),/calculation 0002/);
  await shot('08b-official-examples');
  await page.locator('nav a[href="reference.html"]').click();
  await page.locator('#tool-search').fill('openmc_read_example_results');
  await page.locator('#openmc_read_example_results > summary').click();
  assert.match(await page.locator('#openmc_read_example_results').innerText(),/calculation_id/);
  await shot('08c-stage-selection');
  await page.setViewportSize({width:390,height:844});
  for(const name of ['index.html','compute.html','inputs.html','mcp.html','reference.html','official-examples.html','domain-workflows.html','development-plan.html','development-details.html','dsh.html','toolkit.html','toolkit-usage.html','cad.html','expert-trial.html']) {
    await page.goto(url(name));
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'horizontal page overflow: '+name);
  }
  await page.goto(url('index.html'));await shot('09-mobile');
  assert.deepEqual(problems,[]);assert.deepEqual(remoteRequests,[]);
  const standalone = await checkStandaloneToolkit(browser,root,path.join(out,'standalone-toolkit'));
  await fs.writeFile(path.join(out,'receipt.json'),JSON.stringify({passed:true,pages:14,problems,remoteRequests,screenshots,standaloneToolkit:standalone.passed,entry:url('index.html'),checks:['offline file navigation','demo explanation disclosure','exact command copy/selection fallback','source/model layers','composable model instructions','two-week interface plan','separate user manual and developer design navigation','five embedded architecture diagrams and original SVG navigation','folded core details and nested deep-link reveal','scientific-result and file-format reading sections','single-file copied toolkit without any asset access','CAD setup and tool-flow navigation','own program disclosure','search/no-match','deep-link reveals schema','mobile no page overflow']},null,2));
  process.stdout.write('HTML browser checks passed; screenshots: '+out+'\n');
} finally {await browser.close();}
