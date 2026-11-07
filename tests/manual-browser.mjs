// Offline reader journeys and chapter migration; never starts a model or solver.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {checkStandaloneToolkit} from './manual-toolkit-standalone.mjs';
const {chromium} = await import(process.env.MC_PLAYWRIGHT_MODULE || 'playwright');
const root=path.resolve(process.argv[2]||'.');
const out=path.resolve(process.argv[3]||'artifacts/manual-browser');
await fs.mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:true,...(process.env.MC_CHROMIUM_EXECUTABLE?{executablePath:process.env.MC_CHROMIUM_EXECUTABLE}:{})});
const context=await browser.newContext({viewport:{width:1400,height:1000},reducedMotion:'reduce'});
const page=await context.newPage();
const problems=[],remoteRequests=[],screenshots=[],journeys=[];
page.on('pageerror',e=>problems.push(e.message));
page.on('console',m=>{if(m.type()==='error')problems.push(m.text());});
page.on('request',r=>{if(/^https?:/.test(r.url()))remoteRequests.push(r.url());});
const url=name=>{const [file,...hash]=name.split('#');return pathToFileURL(path.join(root,'docs',file)).href+(hash.length?'#'+hash.join('#'):'');};
const main=['index.html','toolkit-usage.html','deployment.html','toolkit.html','reference.html'];
async function shot(name){await page.screenshot({path:path.join(out,name+'.png')});screenshots.push(name+'.png');}
async function anchor(id){
  const target=page.locator('#'+id);await target.waitFor();
  if(await target.evaluate(e=>e.tagName==='DETAILS'))assert.equal(await target.getAttribute('open'),'');
  assert.ok(await target.evaluate(e=>{for(let p=e.parentElement;p;p=p.parentElement)if(p.tagName==='DETAILS'&&!p.open)return false;return true;}),'Folded parent must reveal the requested chapter: '+id);
  const title=target.locator(':scope > h2, :scope > summary').first();
  if(await title.count())assert.ok(await title.evaluate(e=>{const r=e.getBoundingClientRect();return r.bottom>0&&r.top<innerHeight;}),'Requested heading is visible: '+id);
}
try {
  // New user: capability -> full task -> actual result meaning and verification.
  await page.goto(url('index.html'));
  assert.match(await page.locator('h1').innerText(),/工具包总览/);
  await page.locator('.jump a[href="#scope"]').click();await anchor('scope');
  await page.locator('#capability-matrix > summary').click();
  assert.match(await page.locator('#capability-matrix').innerText(),/二维RectLattice/);
  assert.match(await page.locator('#capability-matrix').innerText(),/原生三维RectLattice/);
  await page.goto(url('index.html'));await shot('01-overview');
  assert.equal(await page.locator('#primary-workflow [data-primary-tool]').count(),12);
  assert.match(await page.locator('#quick-start').innerText(),/case_dir/);
  assert.match(await page.locator('#quick-start').innerText(),/job_id/);
  assert.match(await page.locator('#quick-start').innerText(),/tally_id/);
  await shot('01a-first-workflow');
  await page.locator('a[href="toolkit-usage.html#service-version"]').first().click({timeout:3000});
  assert.match(await page.locator('#service-version').innerText(),/VERSION/);
  assert.match(await page.locator('#service-version').innerText(),/service.version/);
  assert.match(await page.locator('#service-version').innerText(),/CLI直接调用/);
  await page.locator('#service-version a[href="deployment.html#operations"]').click();await anchor('operations');
  assert.match(await page.locator('#operations').innerText(),/serverInfo.version/);
  assert.match(await page.locator('#operations').innerText(),/cat VERSION/);
  await shot('01b-running-version');
  await page.goto(url('toolkit-usage.html'));
  await page.locator('#start a[href="deployment.html#mcp-service"]').click();await anchor('mcp-service');
  assert.match(await page.locator('#mcp-service').innerText(),/mcp\/start.sh/);
  assert.match(await page.locator('#mcp-service').innerText(),/服务启动并发现工具后/);
  await page.goto(url('toolkit-usage.html#failure'));
  await page.locator('#failure a[href="deployment.html#connection-troubleshooting"]').click();await anchor('connection-troubleshooting');
  assert.match(await page.locator('#connection-troubleshooting').innerText(),/check-mcp.ps1/);
  await page.goto(url('toolkit-usage.html'));
  assert.doesNotMatch(await page.locator('#start').innerText(),/两条完整流程待按本手册实测/);
  await page.locator('.jump a[href="#assembly"]').click();await anchor('assembly');
  assert.match(await page.locator('#assembly').innerText(),/openmc_create_model/);
  assert.match(await page.locator('#assembly').innerText(),/openmc_define_tally/);
  assert.match(await page.locator('#assembly').innerText(),/semantic.checked\/ok\/complete/);
  assert.match(await page.locator('#assembly').innerText(),/289/);
  assert.match(await page.locator('#assembly').innerText(),/manifest_complete=true/);
  assert.match(await page.locator('#assembly').innerText(),/完整字典比较/);
  assert.match(await page.locator('#assembly').innerText(),/selection_mode/);
  assert.match(await page.locator('#assembly').innerText(),/RUN_TAG/);
  assert.doesNotMatch(await page.locator('#assembly').innerText(),/assembly-001/);
  await shot('02-component-workflow');
  await page.locator('.jump a[href="#core"]').click();await anchor('core');
  assert.match(await page.locator('#core').innerText(),/64/);
  assert.match(await page.locator('#core').innerText(),/core-lattice/);
  await page.locator('#core details > summary').click();
  assert.match(await page.locator('#core details').innerText(),/不复用组件编号/);
  await page.goto(url('toolkit-usage.html#check'));await anchor('check');
  assert.match(await page.locator('#check').innerText(),/job.json/);
  assert.match(await page.locator('#check').innerText(),/input_files/);
  assert.match(await page.locator('#check').innerText(),/未保留预览就不能补证/);
  assert.match(await page.locator('#check').innerText(),/Python浮点序列化精度/);
  assert.match(await page.locator('#check').innerText(),/所选报告计分预览表使用9位有效数字/);
  assert.match(await page.locator('#check').innerText(),/热图mesh-\*\.csv/);
  assert.match(await page.locator('#check').innerText(),/正文按非零值优先展示至多20行/);
  assert.match(await page.locator('#check').innerText(),/series_csv/);
  await page.locator('#check details > summary').click();
  assert.match(await page.locator('#check').innerText(),/assert_allclose/);await shot('03-data-crosscheck');
  await page.locator('#check a[href="project-records.html#reproduction-reference"]').click();await anchor('reproduction-reference');
  assert.match(await page.locator('#reproduction-reference').innerText(),/1.17112/);
  assert.match(await page.locator('#reproduction-reference').innerText(),/0.93804/);
  assert.match(await page.locator('#reproduction-reference').innerText(),/观测记录，不是验收阈值/);
  assert.match(await page.locator('#reproduction-reference').innerText(),/seed=1/);
  await shot('03b-conditional-reference');
  await page.goto(url('reference.html#openmc_list_artifacts'));await anchor('openmc_list_artifacts');
  assert.match(await page.locator('#openmc_list_artifacts').innerText(),/matches_recorded_output = null/);
  assert.match(await page.locator('#openmc_list_artifacts').innerText(),/没有保存的基线可对照/);
  await shot('03c-artifact-integrity');
  journeys.push('new user: scope -> assembly/core workflow -> native data/chart/report checks');
  // Existing-model reader: no need to reconstruct the input from an example ID.
  await page.goto(url('toolkit-usage.html#existing'));await anchor('existing');
  assert.match(await page.locator('#existing').innerText(),/openmc_import_model/);
  assert.match(await page.locator('#existing').innerText(),/不会静默删除/);
  await page.locator('#import-sample > summary').click();
  assert.match(await page.locator('#import-sample').innerText(),/source_dir/);
  assert.match(await page.locator('#import-sample').innerText(),/RUN_TAG/);
  await page.locator('#import-sample a[href="project-records.html#native-import-reference"]').click();await anchor('native-import-reference');
  assert.match(await page.locator('#native-import-reference').innerText(),/744870c98d07402fbb23c8e4d711c068/);
  assert.match(await page.locator('#native-import-reference').innerText(),/不随源码包分发/);
  await shot('04a-import-evidence');
  await page.locator('#native-import-reference a[href="toolkit-usage.html#import-sample"]').click();await anchor('import-sample');
  await page.locator('#import-sample a[href="#assembly-tally"]').click();await anchor('assembly-tally');
  assert.match(await page.locator('#assembly-tally').innerText(),/openmc_define_tally/);
  await page.goto(url('toolkit-usage.html#existing'));await anchor('existing');

  assert.equal(await page.locator('#python-export').getAttribute('open'),null);
  await page.goto(url('toolkit-usage.html#parameters'));await anchor('parameters');
  assert.match(await page.locator('#parameters').innerText(),/openmc_validate_inputs/);
  assert.match(await page.locator('#parameters').innerText(),/openmc_prepare_case/);
  await shot('04-own-model');
  await page.goto(url('toolkit-usage.html#view'));await anchor('view');
  assert.match(await page.locator('#view').innerText(),/有限几何包围盒/);
  assert.match(await page.locator('#view').innerText(),/原算例的XML/);
  assert.doesNotMatch(await page.locator('#view').innerText(),/这个“查看”会改写/);
  await shot('04b-geometry-isolation');
  await page.locator('#view a[href="#geometry-check"]').click();await anchor('geometry-check');
  assert.match(await page.locator('#geometry-check').innerText(),/overlapping_cells/);
  assert.match(await page.locator('#geometry-check').innerText(),/不能直接当作0个/);
  await shot('04c-geometry-judgement');
  await page.goto(url('official-examples.html#catalog-status'));await anchor('catalog-status');
  assert.match(await page.locator('#catalog-status').innerText(),/scope=included/);
  assert.match(await page.locator('#catalog-status').innerText(),/静态适配说明和占位状态/);
  assert.match(await page.locator('#catalog-status').innerText(),/本部署未验证/);
  await shot('04e-official-status');
  await page.locator('#catalog-status a[href="#coverage"]').click();await anchor('coverage');

  assert.match(await page.locator('main').innerText(),/returned_values/);
  assert.doesNotMatch(await page.locator('main').innerText(),/不做数值截断/);
  await page.goto(url('toolkit-usage.html#cad'));await anchor('cad');
  assert.match(await page.locator('#cad').innerText(),/openmc_convert_cad/);
  assert.match(await page.locator('#cad').innerText(),/scale_factor/);
  await page.goto(url('toolkit-usage.html#maintenance'));
  assert.match(await page.locator('main').innerText(),/mc_job_id=/);
  await page.goto(url('toolkit-usage.html#windows-cleanup'));await anchor('windows-cleanup');
  assert.match(await page.locator('#windows-cleanup').innerText(),/wsl -d \$mcDistro --cd \$mcPackageLinux -- bash/);
  assert.match(await page.locator('#windows-cleanup').innerText(),/--workspace \$mcWorkspaceLinux/);
  assert.match(await page.locator('#windows-cleanup').innerText(),/不能用本机WSL清理远程服务器/);
  await shot('04d-windows-cleanup');
  journeys.push('existing model: import/dependencies -> discovered parameter copy -> common compute -> artifacts/cleanup');
  // Administrator: installation/optional runtime -> MCP -> DSH -> usage.
  await page.locator('.sidebar nav a[href="deployment.html"]').click();
  assert.match(await page.locator('#setup').innerText(),/quickstart.sh/);
  await page.goto(url('deployment.html#cad-runtime'));await anchor('cad-runtime');
  assert.match(await page.locator('#cad-runtime').innerText(),/--cad-only/);
  await page.goto(url('deployment.html#dsh'));await anchor('dsh');
  assert.match(await page.locator('#dsh').innerText(),/ssh -N -L 8770/);
  assert.match(await page.locator('#dsh').innerText(),/cordis.patch.yml/);
  assert.match(await page.locator('#dsh').innerText(),/不修改已有模型配置/);
  await page.locator('#schema-check summary').click();
  assert.match(await page.locator('#dsh').innerText(),/group\/offset\/limit\/summary/);
  assert.match(await page.locator('#schema-check').innerText(),/模型看到的简略签名|模型可见信息/);
  assert.match(await page.locator('#schema-check').innerText(),/原因待确认/);
  assert.match(await page.locator('#dsh').innerText(),/新开会话/);
  await shot('05-deployment-dsh');
  await page.locator('#dsh a[href="toolkit-usage.html#unknown-operation"]').click();await anchor('unknown-operation');
  assert.match(await page.locator('#unknown-operation').innerText(),/不能证明调用从未执行/);
  assert.match(await page.locator('#unknown-operation').innerText(),/当前未见变更/);
  assert.match(await page.locator('#unknown-operation').innerText(),/清单完整不等于文件写入完整/);
  await shot('05a-unknown-operation');
  await page.goto(url('deployment.html#package-verification'));await anchor('package-verification');
  assert.match(await page.locator('#package-verification').innerText(),/verify-package.sh/);
  assert.match(await page.locator('#package-verification').innerText(),/最近一次成功打包的兼容副本/);
  assert.match(await page.locator('#package-verification').innerText(),/不代表|不代替/);
  await shot('05a-package-verification');
  await page.goto(url('deployment.html#systemd-user'));await anchor('systemd-user');
  assert.match(await page.locator('#systemd-user').innerText(),/KillMode=process/);
  assert.match(await page.locator('#systemd-user').innerText(),/不是已启用/);
  await shot('05b-service-lifecycle');
  await page.goto(url('deployment.html#connection-troubleshooting'));await anchor('connection-troubleshooting');
  assert.match(await page.locator('#connection-troubleshooting').innerText(),/Connection refused/);
  assert.match(await page.locator('#connection-troubleshooting').innerText(),/不能断定请求未到达MCP/);
  assert.doesNotMatch(await page.locator('#connection-troubleshooting').innerText(),/错误发生在DSH发出HTTP请求、尚未到达MCP时/);
  await page.goto(url('deployment.html#artifact-delivery'));await anchor('artifact-delivery');
  assert.match(await page.locator('#artifact-delivery').innerText(),/SCP\/SFTP/);
  await shot('05c-artifact-delivery');
  await page.goto(url('toolkit-usage.html#mesh-fields'));await anchor('mesh-fields');
  assert.match(await page.locator('#mesh-fields').innerText(),/x_edges/);
  assert.match(await page.locator('#mesh-fields').innerText(),/y_edges/);
  assert.match(await page.locator('#mesh-fields').innerText(),/values_json/);
  await shot('05c-mesh-fields');
  await page.goto(url('toolkit-usage.html#failure'));
  await page.locator('#failure a[href="#repair"]').click();await anchor('repair');
  assert.match(await page.locator('#repair').innerText(),/repair_demo.py/);
  assert.match(await page.locator('#repair').innerText(),/确认/);
  await shot('05c-repair-entry');
  await page.goto(url('toolkit-usage.html#results'));
  assert.match(await page.locator('#results').innerText(),/zero_bins/);
  await page.goto(url('project-progress.html#trial-20261105'));await anchor('trial-20261105');
  assert.match(await page.locator('#trial-20261105').innerText(),/0.93804/);
  assert.match(await page.locator('#trial-20261105').innerText(),/未独立重演/);
  await shot('05d-trial-receipt');
  await page.goto(url('deployment.html#dsh'));

  await page.locator('#dsh a[href="toolkit-usage.html#assembly"]').click();await anchor('assembly');
  // Open a runnable command in the native-input section and exercise local file copy fallback.
  await page.goto(url('toolkit-usage.html#existing'));
  const copy=page.locator('#existing pre.command').first();
  const original=await copy.locator('code').textContent();
  await page.evaluate(()=>Object.defineProperty(navigator,'clipboard',{value:undefined,configurable:true}));
  await copy.locator('button').click();
  assert.equal(await page.evaluate(()=>window.getSelection().toString()),original);
  journeys.push('administrator: install/configuration -> CAD/Office -> MCP/SSH/DSH -> first task');
  // Developer: preserve the accepted architecture and its independently openable figures.
  await page.goto(url('toolkit.html'));
  assert.equal(await page.locator('.diagram img').count(),5);
  for(const id of ['model-details','job-details','result-details','runtime-details'])assert.equal(await page.locator('#'+id).getAttribute('open'),null);
  await page.goto(url('toolkit.html#model-details'));await anchor('model-details');
  assert.equal(await page.locator('#python-entry').getAttribute('open'),null);
  await page.goto(url('toolkit.html#hpc-design'));await anchor('hpc-design');
  assert.match(await page.locator('#hpc-design > summary').innerText(),/未实现/);
  await page.goto(url('toolkit.html#input-validation'));await anchor('input-validation');
  assert.match(await page.locator('#input-validation').innerText(),/不会强制执行/);
  await page.goto(url('toolkit.html#developer-extension'));await anchor('developer-extension');
  await page.locator('#developer-extension details').first().locator(':scope > summary').click();
  assert.match(await page.locator('#developer-extension').innerText(),/宏观材料/);
  await shot('06-architecture-extension');
  await page.locator('nav a[href="reference.html"]').click();
  await page.locator('#tool-search').fill('not_an_existing_tool');
  assert.equal(await page.locator('#no-results').isVisible(),true);
  await page.goto(url('reference.html#openmc_define_tally'));
  assert.equal(await page.locator('#openmc_define_tally').getAttribute('open'),'');
  await page.locator('#openmc_define_tally details').first().locator('summary').click();
  assert.match(await page.locator('#openmc_define_tally').innerText(),/particle/);
  await shot('07-interface-reference');
  journeys.push('developer: same five figures -> validation/extension -> generated parameter schema');
  // Navigation is finite and consistent, not a directory of every discussion topic.
  for(const name of main) {
    await page.goto(url(name));
    const actual=await page.locator('nav[aria-label="文档导航"] a').evaluateAll(nodes=>nodes.map(n=>n.getAttribute('href')));
    assert.deepEqual(actual,main,'Stable main navigation: '+name);
    assert.equal(await page.evaluate(()=>getComputedStyle(document.documentElement).colorScheme),'dark');
  }
  // Old URLs preserve their chapter meaning, including a folded destination.
  const migration={redirects:{},additional_aliases:{
    'index.html#demo':'toolkit-usage.html#small-example',
    'index.html#demo-options':'toolkit-usage.html#small-example',
    'toolkit-usage.html#using':'toolkit-usage.html#start',
    'toolkit-usage.html#capabilities':'index.html#scope',
    'toolkit-usage.html#guide':'deployment.html#configuration',
    'toolkit-usage.html#results-title':'toolkit-usage.html#results',
    'toolkit-usage.html#files-title':'toolkit-usage.html#files',
    'mcp.html#remote':'deployment.html#remote',
    'mcp.html#handoff':'deployment.html#handoff',
    'mcp.html#mc-service':'deployment.html#mcp-service',
    'mcp.html#helix':'deployment.html#helix'
  }};
  for(const name of ['capabilities.html','reproduce.html','compute.html','domain-workflows.html','dsh.html','env.html','cad.html','inputs.html']) {
    const text=await fs.readFile(path.join(root,'docs',name),'utf8');
    migration.redirects[name]=JSON.parse(text.match(/const targets=(.*?);const key=/s)[1]);
  }
  assert.equal(migration.redirects['compute.html'].change,'toolkit-usage.html#parameters');
  assert.equal(migration.redirects['capabilities.html'].validation,'toolkit.html#input-validation');
  assert.equal(migration.redirects['dsh.html'].connect,'deployment.html#dsh');
  let redirects=0;
  for(const [name,mapping] of Object.entries(migration.redirects))for(const [id,target] of Object.entries(mapping)) {
    await page.goto(url(name)+(id?'#'+encodeURIComponent(id):''));
    await page.waitForURL(url(target.split('#')[0])+(target.includes('#')?'#'+target.split('#')[1]:''));
    const hash=target.split('#')[1];if(hash)await anchor(hash);
    redirects++;
  }
  for(const [old,target] of Object.entries(migration.additional_aliases)) {
    await page.goto(url(old));await page.waitForURL(url(target));await anchor(target.split('#')[1]);redirects++;
  }
  const noJS=await browser.newContext({javaScriptEnabled:false,reducedMotion:'reduce'});
  const fallback=await noJS.newPage();await fallback.goto(url('compute.html#change'));
  await fallback.locator('a[href="toolkit-usage.html#parameters"]').first().click();
  assert.ok(fallback.url().endsWith('toolkit-usage.html#parameters'));
  await noJS.close();
  await page.setViewportSize({width:390,height:844});
  for(const name of [...main,'official-examples.html','mcp.html','project-records.html','expert-trial.html']) {
    await page.goto(url(name));
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'No page overflow: '+name);
  }
  await shot('08-mobile');
  assert.deepEqual(problems,[]);assert.deepEqual(remoteRequests,[]);
  const standalone=await checkStandaloneToolkit(browser,root,path.join(out,'standalone-toolkit'));
  await fs.writeFile(path.join(out,'receipt.json'),JSON.stringify({passed:true,mainDocuments:main,journeys,redirects,problems,remoteRequests,screenshots,standaloneToolkit:standalone.passed,boundary:'Offline documentation only; no model, solver or service.'},null,2)+'\n');
  process.stdout.write('Documentation reader journeys, migration and offline diagrams passed.\n');
} finally {await context.close();await browser.close();}
