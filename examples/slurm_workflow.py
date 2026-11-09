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


async def workflow(args):
    evidence={'calls':[],'submission_id':args.submission_id,'job_id':None,'professional_acceptance':False}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    def save():args.output.write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n')
    async with client(args.mcp_url) as call:
        async def step(name,arguments):
            print('调用：'+name,file=sys.stderr)
            result=await call(name,arguments)
            evidence['calls'].append({'tool':name,'arguments':arguments,'result':result});save()
            return result
        if args.submission_id:
            identifier=args.submission_id
        else:
            resources={'cpus_per_task':args.cpus,'time':args.time}
            for key in ('partition','account','qos','memory_mb'):
                if getattr(args,key) is not None:resources[key]=getattr(args,key)
            prepared=await step('openmc_slurm_prepare',{'case_dir':args.case_dir,'resources':resources,'name':args.name})
            identifier=evidence['submission_id']=prepared['submission_id'];save()
            print('离线准备完成；submission_id='+identifier)
            print('完整脚本、资源、输入哈希：'+str(args.output.resolve()))
            if not args.submit:
                print('未连接集群。检查记录后用 --submission-id '+identifier+' --submit 继续。')
                return evidence
        if not args.submit:
            await step('openmc_slurm_status',{'submission_id':identifier});return evidence
        # submit is idempotent for a known ID and refuses an unresolved submission.
        current=await step('openmc_slurm_status',{'submission_id':identifier})
        if current['state'] in ('prepared','staging','staged'):await step('openmc_slurm_submit',{'submission_id':identifier})
        deadline=time.monotonic()+args.timeout
        while True:
            current=await step('openmc_slurm_status',{'submission_id':identifier})
            print('状态：'+current['state']+'；集群编号：'+str(current.get('cluster_job_id')))
            if current['state'] in ('completed','failed','cancelled'):break
            if current['state'] in ('unknown','submission_unknown'):
                raise RuntimeError('结果未知；保留此submission_id，稍后查询或联系运维，不要重新准备/重复提交')
            if time.monotonic()>=deadline:raise RuntimeError('等待超时；远端作业未自动取消，用同一submission_id续查或明确取消')
            await asyncio.sleep(args.poll_seconds)
        collected=await step('openmc_slurm_collect',{'submission_id':identifier})
        evidence['job_id']=collected['job_id'];save()
        print('结果已取回服务端：'+str(collected.get('directory')))
        print('普通结果/图表/报告使用job_id='+collected['job_id'])
        if current['state']!='completed':raise RuntimeError('远端任务未成功；已保留原始失败日志，不能按成功结果读取')
        return evidence


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    source=parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--case-dir',help='服务端工作区内已有模型目录')
    source.add_argument('--submission-id',help='继续查询/提交同一准备记录，不新建任务')
    parser.add_argument('--mcp-url',default=os.environ.get('MC_TEST_MCP_URL'),help='标准MCP地址；省略走CLI')
    parser.add_argument('--submit',action='store_true',help='明确允许连接所配置集群并推进提交/收集')
    parser.add_argument('--name',default='Slurm试用',help='显示任务名，默认Slurm试用')
    parser.add_argument('--cpus',type=int,default=1,help='单任务CPU/OpenMC线程数，默认1')
    parser.add_argument('--time',default='00:10:00',help='集群时限，默认00:10:00')
    for name in ('partition','account','qos'):parser.add_argument('--'+name,help='运维提供的资源名；省略采用集群默认')
    parser.add_argument('--memory-mb',type=int,help='内存MiB；省略用集群默认')
    parser.add_argument('--timeout',type=float,default=600,help='客户端最长等待秒数；到限不自动取消远端任务')
    parser.add_argument('--poll-seconds',type=float,default=2,help='查询间隔秒数，默认2')
    parser.add_argument('--output',type=Path,default=Path('artifacts')/('slurm-'+uuid.uuid4().hex+'.json'),help='客户端调用记录文件')
    args=parser.parse_args()
    if args.timeout<=0 or args.poll_seconds<=0:parser.error('等待时间和查询间隔须为正数')
    try:asyncio.run(workflow(args))
    except Exception as exc:
        print('Slurm示例未完成：'+error_text(exc)+'；记录：'+str(args.output.resolve()),file=sys.stderr);return 1
    return 0


if __name__=='__main__':sys.exit(main())
