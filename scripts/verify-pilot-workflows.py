#!/usr/bin/env python3
"""Select existing real tests in new processes and preserve their local evidence.

No solver/test logic is duplicated. Requires MC_TEST_OPENMC_CONFIG with a native
runtime. No model/provider/DSH calls. Existing attempt directories are immutable.
"""
from __future__ import annotations
import argparse, contextlib, fnmatch, hashlib, json, os, re, shutil, subprocess, sys, tempfile, time, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def safe(value):
    if isinstance(value,dict):
        return {k:safe(v) for k,v in value.items() if k not in {'runtime','runtime_config','config','headers'}
                and not re.search(r'password|token|secret|authorization|api[_-]?key',k,re.I)}
    if isinstance(value,list):return [safe(x) for x in value]
    return value


class EvidenceCapture:
    def __init__(self,out,max_bytes=128*1024**2):
        self.out=Path(out);self.limit=max_bytes;self.used=0;self.directories=[];self.status={};self.captures=[];self.errors=[]
    def index(self):
        write_json(self.out/'temporary-directories.json',[{'directory':x.name,'status':self.status.get(x.name,'pending')} for x in self.directories])
    def capture(self,temporary):
        source=Path(temporary.name)
        if source.name in {'','/'}:raise ValueError('Invalid temporary source')
        if self.status.get(temporary.name) in {'captured','empty'}:return True
        if self.status.get(temporary.name)=='retained_after_capture_error':return False
        target=self.out/'snapshots'/('fixture-'+str(self.directories.index(temporary)+1))
        try:
            files=[p for p in source.rglob('*') if p.is_file() and not p.is_symlink()
                   and p.suffix.lower() in {'.xml','.h5','.png','.csv','.json','.md','.pdf','.docx','.doc','.log'}
                   and p.name not in {'service.json','connection.json','openmc.json','config.json','runtime.json'}]
            manifest=[]
            for p in files:
                if self.used+p.stat().st_size>self.limit:raise RuntimeError('Evidence capture exceeds configured bound')
                dest=target/p.relative_to(source);dest.parent.mkdir(parents=True,exist_ok=True);original_hash=digest(p)
                if p.suffix=='.json':write_json(dest,safe(json.loads(p.read_text())))
                else:shutil.copy2(p,dest)
                if digest(p)!=original_hash or (p.suffix!='.json' and digest(dest)!=original_hash):
                    raise RuntimeError('Evidence changed during capture: '+str(p))
                self.used+=dest.stat().st_size
                manifest.append({'file':str(p.relative_to(source)),'source_sha256':original_hash,
                                 'saved_sha256':digest(dest),'json_filtered':p.suffix=='.json','bytes':dest.stat().st_size})
            if manifest:
                write_json(target/'manifest.json',manifest)
                self.captures.append({'fixture':source.name,'snapshot':str(target.relative_to(self.out)),'files':len(manifest)})
            self.status[temporary.name]='captured' if files else 'empty';self.index();return True
        except Exception as error:
            # The weakref finalizer would otherwise delete the uncaptured original.
            temporary._finalizer.detach()
            self.status[temporary.name]='retained_after_capture_error'
            self.errors.append({'directory':str(source),'reason':type(error).__name__+': '+str(error)})
            self.index();return False
    @contextlib.contextmanager
    def installed(self):
        original=tempfile.TemporaryDirectory;manager=self
        class CapturedDirectory(original):
            def __init__(self,*args,**kwargs):
                super().__init__(*args,**kwargs);manager.directories.append(self);manager.index()
            def cleanup(self):
                if manager.capture(self):super().cleanup()
        tempfile.TemporaryDirectory=CapturedDirectory
        try:yield self
        finally:
            # Strong references include fixtures abandoned by a failed setUpClass.
            try:
                for temporary in self.directories:
                    try:temporary.cleanup()
                    except Exception as error:
                        temporary._finalizer.detach()
                        self.status[temporary.name]='retained_after_capture_error'
                        self.errors.append({'directory':temporary.name,'reason':type(error).__name__+': '+str(error)})
            finally:tempfile.TemporaryDirectory=original


def missing_artifacts(out,patterns):
    names=[p.name for p in (out/'snapshots').rglob('*') if p.is_file()]
    return [pattern for pattern in patterns if not any(fnmatch.fnmatch(name,pattern) for name in names)]


def execute(flow,out):
    capture=EvidenceCapture(out);start=time.monotonic();started_at=time.time();result=None;unexpected=None
    sys.path.insert(0,str(ROOT/'tests'))
    try:
        with capture.installed():
            suite=unittest.defaultTestLoader.loadTestsFromNames(flow['tests'])
            with (out/'unittest.log').open('w') as log:result=unittest.TextTestRunner(stream=log,verbosity=2).run(suite)
    except Exception as error:unexpected=type(error).__name__+': '+str(error)
    auxiliary=[];missing_auxiliary=[]
    for name in flow.get('auxiliary_evidence',[]):
        source=ROOT/name
        if not source.is_file() or source.stat().st_mtime < started_at:missing_auxiliary.append(name);continue
        dest=out/'auxiliary-evidence'/source.name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,dest)
        auxiliary.append({'source':name,'file':str(dest.relative_to(out)),'sha256':digest(dest)})
    events=[]
    for log in (ROOT/'mcp/logs').glob('server-*.jsonl'):
        if log.stat().st_mtime>=started_at:
            dest=out/'server-events'/log.name;dest.parent.mkdir(exist_ok=True);shutil.copy2(log,dest)
            events.append({'file':str(dest.relative_to(out)),'sha256':digest(dest)})
    missing=missing_artifacts(out,flow['required_artifacts'])
    passed=bool(result and result.wasSuccessful() and not result.skipped and not capture.errors
                and capture.captures and not missing and not missing_auxiliary and not unexpected)
    receipt={'workflow':flow['id'],'title':flow['title'],'status':'PASS' if passed else 'FAIL',
             'user_entry':flow['user_entry'],'input':flow['input'],'oracle':flow['oracle'],'selected_tests':flow['tests'],
             'tests_run':result.testsRun if result else 0,'skipped':[str(x[0]) for x in result.skipped] if result else [],
             'failures':len(result.failures) if result else 0,'errors':len(result.errors) if result else 0,
             'unexpected_error':unexpected,'elapsed_seconds':round(time.monotonic()-start,3),'snapshots':capture.captures,
             'capture_errors':capture.errors,'captured_bytes':capture.used,'missing_required_artifacts':missing,
             'auxiliary_evidence':auxiliary,'missing_auxiliary_evidence':missing_auxiliary,'server_events':events,
             'runtime_config_sha256':digest(Path(os.environ['MC_TEST_OPENMC_CONFIG'])),'real_model_calls':0,'professional_acceptance':False}
    write_json(out/'result.json',receipt)
    return 0 if passed else 1


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--workflow',default='all')
    parser.add_argument('--evidence-dir',default='artifacts/pilot-workflows');parser.add_argument('--child',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args();runtime=os.environ.get('MC_TEST_OPENMC_CONFIG')
    if not runtime or not Path(runtime).is_file():parser.error('Set MC_TEST_OPENMC_CONFIG to the real native runtime config; skip is not accepted')
    cfg=json.loads(Path(runtime).read_text())
    if cfg.get('runtime')!='native' or not cfg.get('python_command') or not cfg.get('openmc_command'):parser.error('Native API/executable configuration is required')
    manifest=json.loads((ROOT/'config/pilot-workflows.json').read_text());flows=manifest['workflows']
    selected=flows if args.workflow=='all' else [f for f in flows if f['id']==args.workflow]
    if not selected:parser.error('Unknown workflow ID')
    out=Path(args.evidence_dir).resolve()
    if args.child:
        claim=out/'attempt.json'
        if len(selected)!=1 or not claim.is_file() or json.loads(claim.read_text()).get('workflow')!=selected[0]['id']:parser.error('Child requires a fresh parent-owned attempt')
        if any(p.name not in {'attempt.json','process.log'} for p in out.iterdir()):parser.error('Attempt already started; preserve it and choose a new evidence directory')
        try:
            with (out/'started.json').open('x') as f:f.write(json.dumps({'pid':os.getpid()}))
        except FileExistsError:parser.error('Attempt already started')
        return execute(selected[0],out)
    for flow in selected:
        target=out/flow['id']
        if target.exists() and any(target.iterdir()):parser.error('Existing evidence must not be overwritten; select a new attempt directory')
        target.mkdir(parents=True,exist_ok=True)
        with (target/'attempt.json').open('x') as f:json.dump({'workflow':flow['id'],'pid':os.getpid()},f)
        with (target/'process.log').open('w') as log:
            done=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--workflow',flow['id'],'--evidence-dir',str(target),'--child'],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
        print(flow['id']+': '+('PASS' if done.returncode==0 else 'FAIL')+'; evidence='+str(target))
        if done.returncode:return done.returncode
    return 0

if __name__=='__main__':raise SystemExit(main())
