"""Existing Slurm helper: real toolkit operations, controlled dropped replies and scheduler."""
import asyncio,json,os,sys,unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import test_slurm_workflow as fixture
from adapters import execute_tool
from tools import TOOLS
import openmc_slurm as slurm
sys.path.insert(0,str(fixture.ROOT/'examples'))
import slurm_workflow as client

class SlurmClient(unittest.TestCase):
    setUp=fixture.SlurmWorkflow.setUp
    write_profile=fixture.SlurmWorkflow.write_profile
    hashes=staticmethod(fixture.SlurmWorkflow.hashes)
    prepare=fixture.SlurmWorkflow.prepare
    load=fixture.SlurmWorkflow.load
    rows=fixture.SlurmWorkflow.rows
    def args(self,sid=None):
        return SimpleNamespace(submission_id=sid,case_dir=None if sid else 'cases/my input',output=self.root/'client.json',mcp_url='http://controlled/mcp',submit=True,timeout=10,poll_seconds=.001,name=None,cpus=None,time=None)
    def transport(self,drop_status=False,drop_collect=False,logical_status_failures=0,always_unknown=False):
        registry={t['name']:t for t in TOOLS};state={'sessions':0,'calls':[],'status_dropped':False,'collect_dropped':False,'failures':logical_status_failures}
        @asynccontextmanager
        async def session(_url):
            state['sessions']+=1;dead=False
            async def call(name,args):
                nonlocal dead
                state['calls'].append((name,args))
                if dead:raise ConnectionError('this connection is closed; a fresh session is required')
                if name=='openmc_slurm_status' and state['failures']:
                    state['failures']-=1;raise RuntimeError('controlled Slurm query temporarily unavailable')
                if name=='openmc_slurm_status' and always_unknown:return {'state':'unknown','last_error':'accounting pending','cluster_job_id':None}
                result=await execute_tool(registry[name],args)
                if name=='openmc_slurm_status' and drop_status and not state['status_dropped']:
                    state['status_dropped']=True;dead=True;raise ConnectionError('status reply dropped after execution')
                if name=='openmc_slurm_collect' and drop_collect and not state['collect_dropped']:
                    state['collect_dropped']=True;dead=True;raise ConnectionError('collect reply dropped after publication')
                return result
            yield call
        return session,state

    def test_default_resources_reuse_site_and_repeating_same_receipt_does_not_recompute(self):
        self.p['resource_defaults']={'cpus_per_task':3,'time':'00:42:00','partition':'CPU'};self.write_profile()
        args=self.args();session,state=self.transport()
        with patch.object(client,'client',session):
            first=asyncio.run(client.resilient_workflow(args));second=asyncio.run(client.resilient_workflow(args))
        sid=first['submission_id'];self.assertEqual(second['submission_id'],sid);self.assertEqual(len(self.rows()),1)
        self.assertEqual(self.load(sid)['resources']['cpus_per_task'],3);self.assertEqual(self.load(sid)['resources']['time'],'00:42:00')
        prepare_calls=[v for v in state['calls'] if v[0]=='openmc_slurm_prepare'];self.assertEqual(len(prepare_calls),1);self.assertEqual(prepare_calls[0][1]['resources'],{})
        self.assertEqual(self.hashes(self.case),self.original)

    def test_dropped_status_and_collect_reconnect_and_keep_one_job(self):
        sid=self.prepare()['submission_id'];slurm.submit_slurm(sid);session,state=self.transport(True,True);args=self.args(sid)
        with patch.object(client,'client',session):value=asyncio.run(client.resilient_workflow(args))
        self.assertEqual(value['outcome'],'completed');self.assertEqual(value['job_id'],sid);self.assertEqual(state['sessions'],3);self.assertEqual(len(self.rows()),1)
        self.assertFalse(any(v[0] in ('openmc_slurm_prepare','openmc_slurm_submit','openmc_slurm_cancel') for v in state['calls']))
        self.assertNotIn('reason',value);self.assertEqual(value['last_status']['state'],'completed')
        self.assertEqual(self.hashes(self.case),self.original)

    def test_temporary_query_error_recovers_without_reopening_healthy_session(self):
        sid=self.prepare()['submission_id'];slurm.submit_slurm(sid);session,state=self.transport(logical_status_failures=2)
        with patch.object(client,'client',session):value=asyncio.run(client.resilient_workflow(self.args(sid)))
        self.assertEqual(value['outcome'],'completed');self.assertEqual(state['sessions'],1);self.assertEqual(len(self.rows()),1)

    def test_persistent_unknown_pauses_and_never_cancels_or_submits_again(self):
        sid=self.prepare()['submission_id'];args=self.args(sid);session,state=self.transport(always_unknown=True)
        with patch.object(client,'client',session),self.assertRaisesRegex(RuntimeError,'仍无法确认'):asyncio.run(client.resilient_workflow(args))
        self.assertEqual(len(state['calls']),3);value=json.loads(args.output.read_text());self.assertEqual(value['outcome'],'waiting_paused');self.assertEqual(value['submission_id'],sid)
        self.assertFalse((self.root/'scheduler/scheduler.json').exists());self.assertEqual(self.load(sid)['state'],'prepared')

    def test_waiting_deadline_keeps_running_job_and_original_receipt(self):
        os.environ['SLURM_FIXTURE_MODE']='hold';sid=self.prepare()['submission_id'];slurm.submit_slurm(sid);args=self.args(sid);args.timeout=.01;session,state=self.transport()
        with patch.object(client,'client',session),self.assertRaisesRegex(RuntimeError,'等待到期'):asyncio.run(client.resilient_workflow(args))
        self.assertEqual(self.load(sid)['state'],'queued');self.assertEqual(len(self.rows()),1)
        self.assertEqual(json.loads(args.output.read_text())['outcome'],'waiting_paused')

    def test_receipt_endpoint_or_parameters_mismatch_does_not_overwrite_or_submit(self):
        args=self.args();session,state=self.transport()
        with patch.object(client,'client',session):asyncio.run(client.resilient_workflow(args))
        raw=args.output.read_bytes();args.mcp_url='http://other/mcp'
        with patch.object(client,'client',session),self.assertRaisesRegex(RuntimeError,'另一请求'):asyncio.run(client.resilient_workflow(args))
        self.assertEqual(args.output.read_bytes(),raw);self.assertEqual(len(self.rows()),1)
        args.mcp_url='http://controlled/mcp';args.cpus=7
        with patch.object(client,'client',session),self.assertRaisesRegex(RuntimeError,'另一请求'):asyncio.run(client.resilient_workflow(args))
        self.assertEqual(args.output.read_bytes(),raw);self.assertEqual(len(self.rows()),1)

    def test_explicit_id_cannot_silently_ignore_changed_resources(self):
        args=self.args();session,state=self.transport()
        with patch.object(client,'client',session):value=asyncio.run(client.resilient_workflow(args))
        args.submission_id=value['submission_id'];args.case_dir=None;args.cpus=7;raw=args.output.read_bytes();calls=len(state['calls'])
        with patch.object(client,'client',session),self.assertRaisesRegex(RuntimeError,'原任务未改'):asyncio.run(client.resilient_workflow(args))
        self.assertEqual(args.output.read_bytes(),raw);self.assertEqual(len(state['calls']),calls);self.assertEqual(len(self.rows()),1)
        args.cpus=1
        with patch.object(client,'client',session):same=asyncio.run(client.resilient_workflow(args))
        self.assertEqual(same['submission_id'],value['submission_id']);self.assertEqual(len(self.rows()),1)

    def test_explicit_id_without_old_receipt_does_not_record_ignored_resources(self):
        sid=self.prepare()['submission_id'];args=self.args(sid);args.cpus=7;session,state=self.transport()
        with patch.object(client,'client',session),self.assertRaisesRegex(RuntimeError,'没有原回执'):
            asyncio.run(client.resilient_workflow(args))
        self.assertFalse(args.output.exists());self.assertEqual(state['calls'],[])
        self.assertEqual(self.load(sid)['resources']['cpus_per_task'],1)

    def test_shared_receipt_lock_prevents_two_helpers_from_starting(self):
        from transfer_files import receipt_lock
        args=self.args()
        with receipt_lock(args.output,'任务跟踪'),self.assertRaisesRegex(ValueError,'已有任务跟踪'):
            asyncio.run(client.resilient_workflow(args))
        self.assertFalse((self.work/'.mc-slurm').exists())

    def test_reconnect_delay_cannot_submit_after_waiting_deadline(self):
        sid=self.prepare()['submission_id'];args=self.args(sid);args.timeout=.3;args.poll_seconds=.4
        session,state=self.transport(drop_status=True)
        with patch.object(client,'client',session),self.assertRaisesRegex(RuntimeError,'等待到期'):
            asyncio.run(client.resilient_workflow(args))
        self.assertEqual(state['sessions'],1)
        self.assertEqual([v[0] for v in state['calls']],['openmc_slurm_status'])
        self.assertFalse((self.root/'scheduler').exists());self.assertEqual(self.load(sid)['state'],'prepared')

    def test_late_successful_status_does_not_start_collection_after_deadline(self):
        registry={t['name']:t for t in TOOLS};sid=self.prepare()['submission_id'];slurm.submit_slurm(sid);calls=[]
        @asynccontextmanager
        async def session(_url):
            async def call(name,args):
                calls.append(name);value=await execute_tool(registry[name],args)
                if name=='openmc_slurm_status':await asyncio.sleep(.03)
                return value
            yield call
        args=self.args(sid);args.timeout=.02;args.poll_seconds=.4
        with patch.object(client,'client',session),patch.object(client.asyncio,'sleep',wraps=asyncio.sleep) as sleeps,self.assertRaisesRegex(RuntimeError,'等待到期'):
            asyncio.run(client.resilient_workflow(args))
        self.assertEqual([v.args[0] for v in sleeps.call_args_list],[.03])
        self.assertEqual(calls,['openmc_slurm_status']);self.assertFalse((self.work/'.mc-jobs').exists())
        self.assertEqual(json.loads(args.output.read_text())['last_status']['state'],'completed')

    def test_lost_preparation_reply_is_not_blindly_repeated(self):
        registry={t['name']:t for t in TOOLS};calls=[]
        @asynccontextmanager
        async def session(_url):
            async def call(name,args):
                calls.append(name);await execute_tool(registry[name],args);raise ConnectionError('reply lost')
            yield call
        args=self.args()
        with patch.object(client,'client',session),self.assertRaisesRegex(RuntimeError,'准备结果尚未确认'):asyncio.run(client.resilient_workflow(args))
        self.assertEqual(calls,['openmc_slurm_prepare']);self.assertEqual(len(list((self.work/'.mc-slurm').iterdir())),1)
        self.assertFalse((self.root/'scheduler').exists())

if __name__=='__main__':unittest.main()
