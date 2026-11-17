// Real Windows launcher -> WSL -> standard MCP. No provider or model calls.
import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
const sdk=process.env.MC_WINDOWS_MCP_CLIENT;
const load=p=>sdk?import(pathToFileURL(sdk.replaceAll('\\','/')+'/dist/'+p+'.mjs').href):import('@modelcontextprotocol/client'+(p==='index'?'':'/'+p));
const {Client}=await load('index');
const {StdioClientTransport}=await load('stdio');
const transport=new StdioClientTransport({command:process.argv[2],args:[],stderr:'pipe'});
let stderr='';const client=new Client({name:'windows-exe-stdio-check',version:'1'});
let evidence;
try {
 await client.connect(transport);
 transport.stderr?.on('data',chunk=>stderr+=chunk.toString('utf8'));
 const definitions=(await client.listTools()).tools;
 assert(definitions.some(t=>t.name==='openmc_generate_result_bundle'));
 const workflow=await client.callTool({name:'openmc_get_workflow',arguments:{chapter:'start'}});
 assert(!workflow.isError);assert(workflow.structuredContent.content.includes('无需用户说“用MCP”'));
 const environment=await client.callTool({name:'openmc_environment',arguments:{}});
 assert(!environment.isError);if(process.argv[4]!=='--lifetime')assert(environment.structuredContent.api_available);
 const invalid=await client.callTool({name:'openmc_job_status',arguments:{job_id:'中文 invalid'}});
 assert(invalid.isError);assert(invalid.content[0].text.includes('无效作业编号'));
 evidence={version:client.getServerVersion(),definitions,workspace:environment.structuredContent.workspace,
  unicode_round_trip:true,workflow_received:true,process_id:transport.pid,model_requests:0};
 if(process.argv[4]==='--lifetime') {
  const submitted=await client.callTool({name:'openmc_submit_job',arguments:{case_dir:'case',name:'Windows跨会话受控作业',threads:1}});
  assert(!submitted.isError,JSON.stringify(submitted));evidence.job=submitted.structuredContent;
  let state;
  for(let n=0;n<100;n++){
   state=(await client.callTool({name:'openmc_job_status',arguments:{job_id:evidence.job.job_id}})).structuredContent;
   if(state.state==='running')break;await new Promise(resolve=>setTimeout(resolve,50));
  }
  assert.equal(state.state,'running');process.kill(evidence.process_id,'SIGKILL');
 }
 if(process.argv[4]==='--compute') {
  async function call(name,args){const value=await client.callTool({name,arguments:args});assert(!value.isError,JSON.stringify(value));return value.structuredContent;}
  const case_dir='launcher-smoke/'+Date.now();
  await call('openmc_create_model',{case_dir,template:'synthetic-mgxs',particles:200,batches:12,inactive:2});
  const tally=await call('openmc_define_tally',{case_dir,scores:['flux'],filters:[{type:'mesh',dimension:[3,2,1],lower_left:[-10,-10,-10],upper_right:[10,10,10]}]});
  evidence.job=await call('openmc_submit_job',{case_dir,name:'Windows启动器实际计算',threads:1});
  evidence.tally_id=tally.tally_id;
 }
} finally {await client.close();}
let childExited=false;try{process.kill(evidence.process_id,0);}catch{childExited=true;}
assert(childExited,'Windows launcher must exit when transport closes');
if(evidence.job){
 const resumed=new Client({name:'windows-exe-reconnect',version:'1'});
 const nextTransport=new StdioClientTransport({command:process.argv[2],args:[],stderr:'ignore'});
 try{
  await resumed.connect(nextTransport);
  async function call(name,args){const value=await resumed.callTool({name,arguments:args});assert(!value.isError,JSON.stringify(value));return value.structuredContent;}
  let status;
  if(process.argv[4]==='--lifetime') {
   try {
    status=await call('openmc_job_status',{job_id:evidence.job.job_id});assert.equal(status.state,'running');
   } finally {await call('openmc_cancel_job',{job_id:evidence.job.job_id});}
   for(let n=0;n<100;n++){
    status=await call('openmc_job_status',{job_id:evidence.job.job_id});
    if(status.state==='cancelled')break;await new Promise(resolve=>setTimeout(resolve,50));
   }
   assert.equal(status.state,'cancelled');evidence.forced_parent_exit_worker_survived=true;
  } else {
  for(let n=0;n<300;n++){
   status=await call('openmc_job_status',{job_id:evidence.job.job_id});
   if(['completed','failed','cancelled','interrupted'].includes(status.state))break;
   await new Promise(resolve=>setTimeout(resolve,200));
  }
  assert.equal(status.state,'completed');evidence.completed_job=status;
  evidence.results=await call('openmc_read_results',{job_id:evidence.job.job_id});
  evidence.bundle=await call('openmc_generate_result_bundle',{job_id:evidence.job.job_id,tally_id:evidence.tally_id,score:'flux',report_formats:['markdown']});
  assert.equal(evidence.bundle.state,'complete');evidence.reconnect_same_job=true;
  }
 } finally {await resumed.close();}
}
await writeFile(process.argv[3],JSON.stringify({...evidence,child_exited:true,stderr},null,2));
console.log('PASS Windows exe -> WSL stdio: '+evidence.definitions.length+' tools; Chinese text, environment, errors and cleanup verified.');
