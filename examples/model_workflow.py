#!/usr/bin/env python3
"""Run your declarative model or imported XML through the same CLI/MCP tools.

No provider/model is called. --spec accepts user data rather than an example ID.
See docs/domain-workflows.html for units, model scope and expected outputs.
"""
import argparse
import asyncio
import json
import os
import re
import tempfile
from pathlib import Path
import sys
import uuid

from openmc_demo import client, error_text
from public_case_demo import wait_job


def safe_value(value):
    """Receipts never include connection settings or known credential values."""
    if isinstance(value,dict):
        return {key:('[redacted]' if re.search(r'password|token|api[_-]?key|authorization|secret',str(key),re.I)
                     else safe_value(item)) for key,item in value.items()}
    if isinstance(value,list):return [safe_value(item) for item in value]
    if isinstance(value,str):
        for key,secret in os.environ.items():
            if len(secret)>=4 and re.search(r'password|token|api[_-]?key|authorization|secret',key,re.I):
                value=value.replace(secret,'[redacted]')
        value=re.sub(r'(https?://)[^/\s@]+@',r'\1[redacted]@',value)
        value=re.sub(r'((?:token|password|api[_-]?key)=)[^&\s]+',r'\1[redacted]',value,flags=re.I)
        return value
    return value


def save_receipt(args,output):
    if not args.receipt:return
    receipt=Path(args.receipt).resolve();receipt.parent.mkdir(parents=True,exist_ok=True)
    fd,temporary=tempfile.mkstemp(prefix='.receipt-',dir=receipt.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            json.dump(safe_value(output),stream,ensure_ascii=False,indent=2,allow_nan=False);stream.write('\n')
        os.replace(temporary,receipt)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)


class WorkflowFailure(RuntimeError):
    pass


async def workflow(args):
    case_dir=args.case_dir or 'cases/model-'+uuid.uuid4().hex[:10]
    output={'case_dir':case_dir,'current_case_dir':case_dir,'stage':'start','state':'running',
            'case':None,'parameters':None,'prepared':None,'runs':[],'professional_acceptance':False}
    try:
        save_receipt(args,output)
        changes={}
        for item in args.set:
            key,separator,raw=item.partition('=')
            if not separator or not key or key in changes:
                raise ValueError('--set使用不重复的 参数名=JSON数值，先查询openmc_list_parameters')
            changes[key]=json.loads(raw)
        async with client(args.mcp_url) as call:
            async def step(stage,name,arguments,container,key):
                output['stage']=stage;save_receipt(args,output)
                value=await call(name,arguments)
                container[key]=value;save_receipt(args,output)
                return value
            print('1/5 接入原生模型，或按你的模型描述通过Python API生成输入',file=sys.stderr)
            if args.spec:
                spec=json.loads(Path(args.spec).read_text(encoding='utf-8'))
                await step('model_input','openmc_build_model',{'case_dir':case_dir,'spec':spec},output,'case')
            else:
                await step('model_input','openmc_import_model',{'source_dir':args.source_dir,'target_dir':case_dir},output,'case')
            await step('discover_parameters','openmc_list_parameters',{'case_dir':case_dir},output,'parameters')
            if changes:
                prepared=await step('prepare','openmc_prepare_case',{'case_dir':case_dir,
                    'target_dir':case_dir+'-changed','parameters':changes},output,'prepared')
                if not prepared['parameter_diff']['verified']:
                    raise RuntimeError('修改落盘与读回不一致；停止提交，查看parameter_diff')
            cases=[case_dir]+([case_dir+'-changed'] if changes else [])
            for index,directory in enumerate(cases):
                output['current_case_dir']=directory;output.pop('current_job_id',None)
                run={'case_dir':directory,'state':'preparing','chart':None};output['runs'].append(run)
                print('2/5 预览、API与语义检查：'+directory,file=sys.stderr)
                preview=await step('preview','openmc_preview_input',{'case_dir':directory},run,'preview')
                validation=await step('validate','openmc_validate_inputs',{'case_dir':directory,
                    'api_check':True,'semantic_check':True},run,'validation')
                if not validation['structure_valid'] or not validation.get('semantic',{}).get('ok',True):
                    raise RuntimeError('输入检查未通过，停止提交：'+json.dumps(validation,ensure_ascii=False))
                print('3/5 命名提交并等终态（本次调用明确要求执行计算）',file=sys.stderr)
                name=args.name if index==0 else args.name+' / changed'
                submitted=await step('submit','openmc_submit_job',{'case_dir':directory,'name':name,'threads':args.threads},run,'submitted')
                identifier=submitted['job_id'];output['current_job_id']=identifier
                run['state']='submitted';output['stage']='wait_compute';save_receipt(args,output)
                status=await wait_job(call,identifier,args.timeout)
                run['job']=status;run['state']=status['state'];save_receipt(args,output)
                if status['state']!='completed':
                    raise RuntimeError('计算未成功，原记录保留：'+str(status.get('error')))
                output['stage']='verify_snapshot';save_receipt(args,output)
                expected={f['name']:f['sha256'] for f in preview.get('files',[]) if f.get('sha256')}
                mismatched=[key for key,digest in expected.items() if status['input_hashes'].get(key)!=digest]
                if mismatched:raise RuntimeError('预览后输入发生变化，结果不能归属于先前预览：'+', '.join(mismatched))
                run['preview_matches_snapshot']=True
                # Preserve the actual service-side artifact directory before any
                # optional plot/report component can fail. Refresh after exports.
                await step('compute_artifacts','openmc_list_artifacts',{'kind':'job','identifier':identifier},run,'artifacts')
                print('4/5 读取实际计分，生成图和同选报告',file=sys.stderr)
                results=await step('read_results','openmc_read_results',{'job_id':identifier,'max_values':1000},run,'results')
                tallies=results.get('tallies',[])
                chosen=next((t for t in tallies if t['id']==args.tally_id),None) if args.tally_id else next(
                    (t for t in tallies if 'flux' in t['scores']),tallies[0] if tallies else None)
                if args.tally_id and chosen is None:raise RuntimeError('指定tally不存在，计算结果已保留')
                selection={'job_id':identifier}
                if chosen:
                    selection.update(tally_id=chosen['id'],score='flux' if 'flux' in chosen['scores'] else chosen['scores'][0])
                    await step('plot','openmc_plot_results',selection,run,'chart')
                await step('report','openmc_export_report',{**selection,'format':args.report_format},run,'report')
                await step('artifacts','openmc_list_artifacts',{'kind':'job','identifier':identifier},run,'artifacts')
                run['state']='completed';save_receipt(args,output)
            print('5/5 查询任务名并对比本次两作业（如已提供改参）',file=sys.stderr)
            await step('list_named_jobs','openmc_list_jobs',{'name':args.name},output,'named_jobs')
            if len(output['runs'])==2:
                await step('compare','openmc_job_compare',{'job_a':output['runs'][0]['job']['job_id'],
                    'job_b':output['runs'][1]['job']['job_id'],'compare_tallies':True},output,'comparison')
            output['stage']='completed';output['state']='completed';save_receipt(args,output)
            return output
    except Exception as exc:
        output['state']='failed';output['error']=safe_value(error_text(exc))
        try:save_receipt(args,output)
        except Exception as receipt_error:output['receipt_error']=safe_value(error_text(receipt_error))
        context='阶段='+output['stage']+'；输入='+output['current_case_dir']
        identifiers=[r['submitted']['job_id'] for r in output['runs'] if r.get('submitted',{}).get('job_id')]
        if identifiers:
            context+='；已提交job_id='+', '.join(identifiers)+'；用openmc_job_status查询原任务，未自动重新提交'
        if args.receipt:
            context+='；调用记录='+str(Path(args.receipt).resolve())
            if 'receipt_error' in output:context+='（写入失败：'+output['receipt_error']+'）'
        raise WorkflowFailure(safe_value(context+'；'+output['error'])) from exc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--spec', help='客户端JSON模型文件；字段和单位见openmc_model_capabilities')
    source.add_argument('--source-dir', help='服务端MC_WORKSPACE内已有XML输入目录；不会上传本机文件')
    parser.add_argument('--case-dir', help='服务端工作区的新目录；省略自动产生唯一目录')
    parser.add_argument('--name', default='model-workflow', help='任务名称，可重名；结果仍按job_id区分')
    parser.add_argument('--set', action='append', default=[], metavar='PARAMETER=VALUE', help='按发现的参数创建修改副本并额外计算对比，可重复')
    parser.add_argument('--threads', type=int, default=1, help='每次计算线程数，默认1')
    parser.add_argument('--timeout', type=float, default=300, help='每次计算等待秒数，超时请求取消并保留编号')
    parser.add_argument('--tally-id', type=int, help='要绘图报告的实际tally编号；省略选首个含flux的计分，否则首个计分')
    parser.add_argument('--report-format', choices=['markdown','csv','docx','pdf','doc'], default='markdown')
    parser.add_argument('--mcp-url', help='标准MCP服务URL；省略走相同注册表的直接CLI')
    parser.add_argument('--receipt', help='客户端增量JSON记录；接入、提交及各阶段立即保存，失败保留编号和已有结果；与服务端产物分开')
    parser.add_argument('--json', action='store_true', help='完整JSON输出；默认中文摘要和服务端绝对位置')
    args = parser.parse_args()
    try:
        result = asyncio.run(workflow(args))
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        else:
            print('模型工作流完成（工程调试，未作专业验收）')
            print('模型输入：' + result['case']['server_path'])
            for run in result['runs']:
                job = run['job']
                print('任务：' + str(job.get('name')) + '；' + job['job_id'] + '；' + job['state'])
                print('  keff：' + json.dumps(run['results'].get('keff'), ensure_ascii=False))
                print('  输入与提交快照：指纹一致')
                if run['chart']:
                    print('  图：' + run['chart']['server_path'])
                    print('  完整数据：' + str(Path(run['chart']['server_path']).with_suffix('.csv')))
                print('  报告：' + run['report']['server_path'])
                print('  产物目录：' + run['artifacts'].get('directory', '见完整记录'))
                for warning in run['results'].get('warnings', []):print('  提示：' + warning)
            if args.receipt:print('完整调用记录（客户端）：' + str(Path(args.receipt).resolve()))
            print('比较结论需结合输入变化、标准差和专业依据；图表保留原始计分归一化。')
        return 0
    except Exception as exc:
        print('模型工作流停止：' + safe_value(error_text(exc)), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
