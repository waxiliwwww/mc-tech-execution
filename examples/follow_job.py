#!/usr/bin/env python3
"""Follow an existing job from the host. No model, submission or cancellation.

Run again with the same job ID/receipt after an interruption. Time limits stop
waiting only; solver lifecycle remains with the existing job tools.
"""
import argparse
import asyncio
import json
import math
from pathlib import Path
import sys
import time

from transfer_files import client, save
from openmc_demo import error_text


TERMINAL = {'completed', 'failed', 'cancelled', 'interrupted'}


async def follow(call, job_id, timeout, on_status=None):
    if timeout <= 0:raise ValueError('等待时限须为正秒数；到期不会取消作业')
    end = time.monotonic() + timeout
    while True:
        remaining = end - time.monotonic()
        status = await call('openmc_job_status', {'job_id':job_id, 'wait_seconds':min(30, max(0, math.ceil(remaining)))})
        if on_status:on_status(status)
        terminal = status['state'] in TERMINAL
        if terminal or time.monotonic() >= end:
            return {'job_id':job_id, 'state':status['state'], 'terminal':terminal,
                    'waiting_timed_out':not terminal, 'status':status,
                    'note':'沿原job_id继续查询；本客户端不提交、不重算、不取消。'}


async def run(args):
    path = Path(args.receipt or ('job-'+args.job_id+'.json')).resolve()
    identity = {'mcp_url':args.mcp_url, 'job_id':args.job_id}
    if path.exists():
        previous = json.loads(path.read_text(encoding='utf-8'))
        if previous.get('request') != identity:raise ValueError('回执属于不同服务/作业，未覆盖；选择新的--receipt')
    def record(status):save(path, {'request':identity, 'status':status})
    async with client(args.mcp_url) as call:
        result = await follow(call,args.job_id,args.timeout,record)
    result['receipt'] = str(path)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mcp-url',required=True)
    p.add_argument('--job-id',required=True)
    p.add_argument('--timeout',type=int,default=300,help='客户端等待总秒数，默认300；到期不取消')
    p.add_argument('--receipt',help='保存真实状态的本机回执；断线后沿同一命令查询')
    args=p.parse_args()
    try:print(json.dumps(asyncio.run(run(args)),ensure_ascii=False,indent=2));return 0
    except KeyboardInterrupt:
        print('等待已中断，作业未取消；沿原job_id及回执继续查询。',file=sys.stderr);return 130
    except Exception as exc:
        print('未取得最终状态：'+error_text(exc)+'；沿原job_id重跑同一命令，不要重新提交。',file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())
