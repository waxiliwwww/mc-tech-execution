#!/usr/bin/env python3
"""Generate a package from an existing completed job through standard MCP.

No solver or model calls, no service startup. Keep the original single-purpose
APIs available for custom analysis. Paths returned by the service are server
paths; use transfer_files.py to fetch selected files when needed.
"""
import argparse
import asyncio
import json

from openmc_demo import client


async def main(args):
    async with client(args.url) as call:
        request = {'job_id':args.job_id, 'tally_id':args.tally_id, 'score':args.score,
                   'view':args.view, 'report_formats':args.report_format or ['markdown']}
        for name in ('nuclide','filter_selection','level','energy_bin','style','image_format','title'):
            value=getattr(args,name)
            if value is not None:request[name]=value
        data = await call('openmc_generate_result_bundle', request)
        if args.json:print(json.dumps(data,ensure_ascii=False,indent=2));return
        print('结果包已生成（服务器），未重新计算')
        print('选择：'+json.dumps(data['selection'],ensure_ascii=False)+'；点数：'+str(data['points']))
        print('StatePoint SHA256：'+data['source']['sha256'])
        for artifact in data['artifacts']:
            print(artifact['server_path']+'  ('+str(artifact['bytes'])+' bytes)')
        for warning in data['warnings']:print('提示：'+str(warning))
        print('以上是服务端位置；远程按需取回，脚本与同名JSON放一起即可重画。')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',required=True,help='已启动MCP服务地址，不会启动或改配置')
    parser.add_argument('--job-id',required=True,help='已成功完成作业的job_id')
    parser.add_argument('--tally-id',type=int,required=True,help='该作业真实tally ID')
    parser.add_argument('--score',default='flux',help='该tally的计分量，默认flux')
    parser.add_argument('--view',choices=['auto','series','mesh'],default='auto')
    parser.add_argument('--nuclide',help='计分核素；省略沿工具默认选择')
    parser.add_argument('--filter-selection',type=json.loads,help='原过滤条件JSON数组；用于series视图，按真实tally定义填写')
    parser.add_argument('--level',type=int,help='mesh视图零基z层号')
    parser.add_argument('--energy-bin',type=int,help='mesh视图零基能量箱')
    parser.add_argument('--style',choices=['line','bar'],help='series图型；bar配auto会选择series')
    parser.add_argument('--image-format',choices=['png','svg'],help='默认PNG；SVG用于可编辑图像')
    parser.add_argument('--title',help='只改变图标题，不改变科学数据')
    parser.add_argument('--report-format',action='append',choices=['markdown','csv','docx','pdf','doc'])
    parser.add_argument('--json',action='store_true',help='输出完整结构化回执')
    asyncio.run(main(parser.parse_args()))
