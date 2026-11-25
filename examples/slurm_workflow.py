#!/usr/bin/env python3
"""Prepare or run an existing model through the opt-in Slurm MCP/CLI tools.

Default: offline preparation only. --submit explicitly authorizes the configured
cluster connection. --submission-id resumes the original task without re-submit.
No model calls, no automatic deletion and no implicit cancellation at timeout.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import time
import uuid
from openmc_demo import client, error_text


class WaitingPaused(RuntimeError):
    """Waiting ended; do not reinterpret as an operation failure to retry."""


def _connection_error(exc):
    import subprocess
    if isinstance(exc,(ConnectionError,TimeoutError,subprocess.TimeoutExpired)):return True
    try:
        import httpx,anyio
        if isinstance(exc,(httpx.TransportError,anyio.EndOfStream,anyio.BrokenResourceError,anyio.ClosedResourceError)):return True
        if isinstance(exc,httpx.HTTPStatusError):return exc.response.status_code in (408,425,429,500,502,503,504)
    except ImportError:pass
    return any(_connection_error(e) for e in getattr(exc,'exceptions',()))


async def resilient_workflow(args):
    from transfer_files import receipt_lock
    with receipt_lock(args.output, '任务跟踪'):
        return await _resilient_workflow(args)


async def _resilient_workflow(args):
    """Reconnect boundedly; each session rechecks discovery, and keeps the original receipt."""
    import copy
    from transfer_files import save as save_receipt
    if args.timeout<=0 or args.poll_seconds<=0:raise ValueError('等待时间和查询间隔须为正数')
    end=time.monotonic()+args.timeout
    for attempt in range(3):
        current=copy.copy(args);current.timeout=max(0.,end-time.monotonic());current.deadline=end
        try:return await workflow(current)
        except Exception as exc:
            if not _connection_error(exc):raise
            stored=json.loads(args.output.read_text()) if args.output.exists() else {}
            if not stored.get('submission_id') and stored.get('active_step',{}).get('tool')=='openmc_slurm_prepare':
                stored.update(outcome='preparation_unconfirmed',reason=error_text(exc),next_action={'tool':'openmc_list_jobs','arguments':{'include_slurm':True}});save_receipt(args.output,stored)
                raise RuntimeError('准备结果尚未确认；先用openmc_list_jobs(include_slurm=true)找回原记录，未新建或提交第二次任务') from exc
            if attempt==2 or time.monotonic()>=end:
                stored.update(outcome='connection_paused',reason=error_text(exc),next_action={'resume_receipt':str(args.output.resolve()),'submission_id':stored.get('submission_id')});save_receipt(args.output,stored)
                raise RuntimeError('连接恢复暂未成功；原任务和回执已保留，沿原编号接续，不重新计算：'+error_text(exc)) from exc
            print('连接中断，正在沿原记录恢复连接。',file=sys.stderr)
            await asyncio.sleep(min(args.poll_seconds,max(0.,end-time.monotonic())))

async def workflow(args):
    import hashlib,re
    from transfer_files import save as save_receipt
    endpoint=hashlib.sha256((args.mcp_url or 'local-cli').encode()).hexdigest()
    resources={}
    for option,key in [('cpus','cpus_per_task'),('nodes','nodes'),('ntasks','ntasks'),('time','time'),('partition','partition'),('account','account'),('qos','qos'),('memory_mb','memory_mb')]:
        if getattr(args,option,None) is not None:resources[key]=getattr(args,option)
    deadline=getattr(args,'deadline',time.monotonic()+args.timeout)
    request={'endpoint_sha256':endpoint,'case_dir':getattr(args,'case_dir',None),'resources':resources,'name':getattr(args,'name',None)}
    identifier=args.submission_id
    evidence={'calls':[],'submission_id':identifier,'job_id':None,'request':request,'professional_acceptance':False}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    if identifier and not args.output.exists() and (resources or getattr(args,'name',None) is not None):
        raise RuntimeError('没有原回执可核对显式资源/名称，原任务未改、未创建新回执；继续原任务请省略这些参数，新计算请另用新请求')
    if args.output.exists():
        try:previous=json.loads(args.output.read_text(encoding='utf-8'))
        except (OSError,ValueError) as exc:raise RuntimeError('旧回执无法读取，未覆盖；先用openmc_list_jobs(include_slurm=true)找原任务，再用原编号和新回执接续') from exc
        if not isinstance(previous,dict):raise RuntimeError('旧回执不是有效记录，未覆盖；先查找原Slurm任务')
        previous_request=previous.get('request')
        if previous_request is not None and not isinstance(previous_request,dict):raise RuntimeError('旧回执请求信息损坏，未覆盖；用原编号和新回执接续')
        if not isinstance(previous.get('calls',[]),list):raise RuntimeError('旧回执调用记录损坏，未覆盖；用原编号和新回执接续')
        recorded_id=previous.get('submission_id')
        if not recorded_id:
            for entry in reversed(previous.get('calls',[])):
                value=entry.get('result') if isinstance(entry,dict) and entry.get('tool')=='openmc_slurm_prepare' else None
                candidate=value.get('submission_id') if isinstance(value,dict) else None
                if isinstance(candidate,str) and re.fullmatch('[a-f0-9]{32}',candidate):recorded_id=candidate;break
        if recorded_id:previous['submission_id']=recorded_id
        requested_resources=previous_request.get('resources') if isinstance(previous_request,dict) else None
        if requested_resources is not None and not isinstance(requested_resources,dict):raise RuntimeError('旧回执资源信息损坏，未覆盖；用原编号和新回执接续')
        frozen_resources=dict(requested_resources or {});frozen_name=previous_request.get('name') if isinstance(previous_request,dict) else None
        for entry in previous.get('calls',[]):
            if not isinstance(entry,dict) or entry.get('tool')!='openmc_slurm_prepare':continue
            old_args=entry.get('arguments') or {};old_result=entry.get('result') or {}
            if isinstance(old_args,dict):
                if isinstance(old_args.get('resources'),dict):frozen_resources.update(old_args['resources'])
                if old_args.get('name') is not None:frozen_name=old_args['name']
            if isinstance(old_result,dict):
                if isinstance(old_result.get('resource_summary'),dict):frozen_resources.update(old_result['resource_summary'])
                if old_result.get('name') is not None:frozen_name=old_result['name']
        if identifier and (any(frozen_resources.get(k)!=v for k,v in resources.items()) or getattr(args,'name',None) is not None and args.name!=frozen_name):
            raise RuntimeError('本次显式资源/名称与原任务不一致或缺少旧依据，原任务未改、回执未覆盖；继续原任务请省略新参数，新计算请使用新请求和回执')
        if identifier:
            if recorded_id!=identifier or previous_request and previous_request.get('endpoint_sha256')!=endpoint:
                raise RuntimeError('回执属于其他服务/任务，未覆盖；请选择对应回执或新路径')
        elif previous_request==request and previous.get('submission_id'):
            identifier=previous['submission_id']
        elif previous_request==request and not previous.get('active_step') and not previous.get('calls'):pass
        else:raise RuntimeError('回执已属于另一请求，未覆盖；继续原任务请用原submission_id，新任务请换output')
        evidence=previous;evidence['request']=previous_request or request;evidence.setdefault('calls',[])
    print('接续记录：'+str(args.output.resolve()),flush=True)
    def save():save_receipt(args.output,evidence)
    def pause(reason):
        evidence.update(outcome='waiting_paused',reason=reason,
            next_action=({'tool':'openmc_slurm_status','arguments':{'submission_id':identifier}} if identifier else {'tool':'openmc_list_jobs','arguments':{'include_slurm':True}}),
            note='保留原任务和回执；可沿原编号接续，未自动重投或取消。')
        save();raise WaitingPaused(reason+'；原任务未重投/取消，用同一submission_id及回执接续')
    if time.monotonic()>=deadline:pause('等待到期；尚未发起新的远端操作')
    save()
    async with client(args.mcp_url) as call:
        async def step(name,arguments):
            if time.monotonic()>=deadline:pause('等待到期；尚未发起新的远端操作')
            if name!='openmc_slurm_status':print('调用：'+name,file=sys.stderr)
            evidence['active_step']={'tool':name,'arguments':arguments};save()
            if time.monotonic()>=deadline:
                evidence.pop('active_step',None);pause('等待到期；尚未发起新的远端操作')
            try:result=await call(name,arguments)
            except Exception as exc:
                evidence['calls'].append({'tool':name,'arguments':arguments,'error':error_text(exc)[:4000]});save();raise
            evidence['calls'].append({'tool':name,'arguments':arguments,'result':result});evidence.pop('active_step',None);save()
            return result
        if not identifier:
            prepare_args={'case_dir':args.case_dir,'resources':resources}
            if getattr(args,'name',None) is not None:prepare_args['name']=args.name
            prepared=await step('openmc_slurm_prepare',prepare_args)
            identifier=evidence['submission_id']=prepared['submission_id'];evidence['outcome']='prepared';save()
            print(prepared.get('execution_summary','离线准备完成；使用已确认的资源和站点设置。'))
            print('脚本、资源及输入核对记录：'+str(args.output.resolve()))
            if not args.submit:
                print('未连接集群。检查后用 --submission-id '+identifier+' --submit 继续。');return evidence
        if not args.submit:
            evidence['last_status']=await step('openmc_slurm_status',{'submission_id':identifier});evidence['outcome']='status_checked';save();return evidence
        unavailable=0;submit_attempts=0;last_display=None
        labels={'prepared':'已准备','staging':'传输输入中','staged':'待提交','queued':'排队中','running':'计算中','completed':'正常结束','failed':'计算失败','cancelled':'已取消','unknown':'暂未确认状态','submission_unknown':'正在核对原提交'}
        while True:
            try:current=await step('openmc_slurm_status',{'submission_id':identifier})
            except Exception as exc:
                if isinstance(exc,WaitingPaused) or _connection_error(exc):raise
                unavailable+=1;evidence['last_query_error']=error_text(exc)[:4000];save()
                if unavailable>=3:pause('连续三次未能取得状态：'+error_text(exc))
            else:
                evidence['last_status']=current;save()
                display=(current['state'],current.get('cluster_job_id'),current.get('scheduler_reason'))
                if display!=last_display:
                    print('状态：'+labels.get(current['state'],current['state'])+'；集群编号：'+str(current.get('cluster_job_id')));last_display=display
                if current['state'] in ('completed','failed','cancelled'):break
                if current['state'] in ('prepared','staging','staged'):
                    unavailable=0
                    if submit_attempts>=2:pause('输入传输/提交尚未推进：'+str(evidence.get('last_submit_error') or '已保留同一准备记录'))
                    submit_attempts+=1
                    try:await step('openmc_slurm_submit',{'submission_id':identifier})
                    except Exception as exc:
                        evidence['last_submit_error']=error_text(exc)[:4000];save()
                        if isinstance(exc,WaitingPaused) or _connection_error(exc):raise
                        # Status of this same ID, never a new preparation, resolves submission uncertainty.
                elif current['state'] in ('unknown','submission_unknown'):
                    unavailable+=1
                    if unavailable>=3:pause('原任务状态仍无法确认：'+str(current.get('last_error') or current['state']))
                else:unavailable=0
            if time.monotonic()>=deadline:pause('等待到期；远端任务可能仍在排队或运行')
            await asyncio.sleep(min(args.poll_seconds,max(0.,deadline-time.monotonic())))
        for attempt in range(2):
            try:collected=await step('openmc_slurm_collect',{'submission_id':identifier});break
            except Exception as exc:
                if isinstance(exc,WaitingPaused) or _connection_error(exc):raise
                if attempt:pause('结果取回暂未完成：'+error_text(exc))
                await asyncio.sleep(min(args.poll_seconds,max(0.,deadline-time.monotonic())))
        for key in ('reason','next_action','note','last_query_error','last_submit_error'):evidence.pop(key,None)
        evidence.update(job_id=collected['job_id'],outcome='completed' if current['state']=='completed' else current['state']);save()
        print('结果已取回服务端：'+str(collected.get('directory')))
        print('普通结果/图表/报告使用job_id='+collected['job_id'])
        if current['state']=='cancelled':raise RuntimeError('任务已取消；已保留日志和输入，没有重新计算')
        if current['state']!='completed':raise RuntimeError('远端任务未成功；已保留失败日志，没有重新计算')
        return evidence


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    source=parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--case-dir',help='服务端工作区内已有模型目录')
    source.add_argument('--submission-id',help='继续查询/提交同一准备记录，不新建任务')
    parser.add_argument('--mcp-url',default=os.environ.get('MC_TEST_MCP_URL'),help='标准MCP地址；省略走CLI')
    parser.add_argument('--submit',action='store_true',help='明确允许连接所配置集群并推进提交/收集')
    parser.add_argument('--name',help='任务显示名；省略沿用工具默认')
    parser.add_argument('--cpus',type=int,help='每进程线程数；省略复用站点默认')
    parser.add_argument('--nodes',type=int,help='节点数；省略复用站点默认')
    parser.add_argument('--ntasks',type=int,help='MPI进程总数；省略复用站点默认')
    parser.add_argument('--time',help='集群时限；省略复用站点默认')
    for name in ('partition','account','qos'):parser.add_argument('--'+name,help='运维提供的资源名；省略采用集群默认')
    parser.add_argument('--memory-mb',type=int,help='内存MiB；省略用集群默认')
    parser.add_argument('--timeout',type=float,default=600,help='客户端最长等待秒数；到限不自动取消远端任务')
    parser.add_argument('--poll-seconds',type=float,default=2,help='查询间隔秒数，默认2')
    parser.add_argument('--output',type=Path,default=Path('artifacts')/('slurm-'+uuid.uuid4().hex+'.json'),help='客户端调用记录文件')
    args=parser.parse_args()
    if args.timeout<=0 or args.poll_seconds<=0:parser.error('等待时间和查询间隔须为正数')
    try:asyncio.run(resilient_workflow(args))
    except Exception as exc:
        print('Slurm示例未完成：'+error_text(exc)+'；记录：'+str(args.output.resolve()),file=sys.stderr);return 1
    return 0


if __name__=='__main__':sys.exit(main())
