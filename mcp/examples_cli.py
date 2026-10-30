"""Human-readable example commands; all actions call the public MCP business functions."""
from __future__ import annotations
import argparse
import json
import sys
import time


def configure_parser(parser):
    sub=parser.add_subparsers(dest='example_action',required=True)
    def create(name,help):
        p=sub.add_parser(name,help=help);p.add_argument('--json',action='store_true',help='完整结构化结果');return p
    p=create('list','列出全部官方例题');p.add_argument('--group',choices=['all','source','api','notebook'],default='all')
    p=create('show','查看例题输入、步骤和来源');p.add_argument('example_id')
    for name in ['prepare','run']:
        p=create(name,'创建实例'+('并逐步运行' if name=='run' else '，尚不计算'));p.add_argument('example_id');p.add_argument('--dir',required=True,dest='case_dir',help='工作区内新的相对目录')
        for key in ['particles','batches','inactive','seed']:p.add_argument('--'+key,type=int,help='显式覆盖官方设置；省略保留原值')
        p.add_argument('--factory-parameters',type=json.loads,default=None,help='工厂公开参数JSON；show列出可用项')
        p.add_argument('--mgxs-file',help='slab_mg所需多群HDF5，工作区内相对路径')
        if name=='run':p.add_argument('--threads',type=int,default=1);p.add_argument('--timeout',type=int,default=3600)
    p=create('step','提交实例下一步骤');p.add_argument('instance_id');p.add_argument('step');p.add_argument('--threads',type=int,default=1);p.add_argument('--wait',action='store_true');p.add_argument('--timeout',type=int,default=3600)
    for name in ['plot','report']:
        p=create(name,'用同一计分选择生成'+('图' if name=='plot' else '报告'))
        p.add_argument('instance_id');p.add_argument('--tally-id',type=int,required=True)
        p.add_argument('--score',required=True,help='来自results列出的实际score')
        p.add_argument('--nuclide',default=None)
        p.add_argument('--filter-selection',type=json.loads,default=None,help='可选过滤箱JSON；参考工具参数页')
        p.add_argument('--format',choices=['png','svg'] if name=='plot' else ['markdown','csv','docx','pdf','doc'],default='png' if name=='plot' else 'markdown')
    for name in ['status','results','cancel']:
        p=create(name,{'status':'查状态与下一步','results':'看实际结果和文件位置','cancel':'取消当前步骤'}[name]);p.add_argument('instance_id')
        if name=='status':p.add_argument('--wait-seconds',type=int,default=0,help='运行中等待最多0..30秒，超时不代表失败')
        if name=='results':p.add_argument('--calculation',help='选择Notebook四位求解阶段；先不带该项查看阶段清单')


def wait(instance, step, timeout):
    from example_workflows import example_status
    deadline=time.monotonic()+timeout
    while True:
        value=example_status(instance);job=value['steps'].get(step)
        if job and job['state'] in ('completed','failed','cancelled','interrupted'):
            if job['state']!='completed':raise ValueError(f"步骤{step}未完成：{job['state']}；{job.get('error')}\n{job.get('log_tail','')}")
            return value
        if time.monotonic()>deadline:raise ValueError('等待超时；作业状态尚未确认，不会重提。使用status查询或cancel取消。')
        time.sleep(.25)


def execute(args):
    from official_catalog import list_official_examples, describe
    from example_workflows import prepare_official_example, run_example_step, example_status, cancel_example, read_example_results
    action=args.example_action
    if action=='list':value=list_official_examples(args.group)
    elif action=='show':value=describe(args.example_id)
    elif action in ('prepare','run'):
        params={k:getattr(args,k) for k in ['particles','batches','inactive','seed'] if getattr(args,k) is not None}
        if args.factory_parameters is not None:params['factory']=args.factory_parameters
        if args.mgxs_file is not None:params['mgxs_file']=args.mgxs_file
        value=prepare_official_example(args.example_id,args.case_dir,params)
        args._example_context={'instance_id':value['instance_id'],'step':None,'job_id':None}
        if action=='run':
            instance=value['instance_id']
            if not args.json:print(f'已创建实例：{instance}\n查询：./mc.sh examples status {instance}',flush=True)
            while value['next_step'] is not None:
                step=value['next_step']['id']
                if not args.json:print(f"运行步骤：{step}",flush=True)
                args._example_context={'instance_id':instance,'step':step,'job_id':None}
                submitted=run_example_step(instance,step,args.threads)
                args._example_context['job_id']=submitted['job_id']
                value=wait(instance,step,args.timeout)
            value=read_example_results(instance)
    elif action=='step':
        args._example_context={'instance_id':args.instance_id,'step':args.step,'job_id':None}
        value=run_example_step(args.instance_id,args.step,args.threads)
        args._example_context['job_id']=value['job_id']
        if args.wait and not args.json:print(f"实例：{args.instance_id}；作业：{value['job_id']}",flush=True)
        if args.wait:value=wait(args.instance_id,args.step,args.timeout)
    elif action in ('plot','report'):
        from openmc_plots import plot_results
        from openmc_tools import export_report
        value=example_status(args.instance_id)
        job=value['steps'].get('compute')
        if not job or job['state']!='completed':
            raise ValueError('该简化入口需要已完成的compute步骤；Notebook/多阶段例题请用results查看其实际图表与阶段产物。')
        function=plot_results if action=='plot' else export_report
        value={'instance_id':args.instance_id,**function(job['job_id'],tally_id=args.tally_id,
            score=args.score,nuclide=args.nuclide,filter_selection=args.filter_selection,format=args.format)}
    elif action=='status':value=example_status(args.instance_id,args.wait_seconds)
    elif action=='cancel':value=cancel_example(args.instance_id)
    else:value=read_example_results(args.instance_id,args.calculation)
    if args.json:print(json.dumps(value,ensure_ascii=False,allow_nan=False,indent=2));return 0
    if action=='list':
        print(f"官方例题：收录{value['count']}项；本轮{value['included_count']}项，暂缓{value['deferred_count']}项（收录不等于实跑通过）")
        for e in value['examples']:print(f"  {e['example_id']}  | {e['title']}  | {e['implementation_status']}")
        print('下一步：./mc.sh examples show 例题ID')
    elif action=='show':
        print(value['title']+'（'+value['name']+'）');print('来源：'+value['source_url']);print('设置：省略覆盖参数时保留官方原值')
        for k,v in value.get('factory_parameters',{}).items():
            print(f"工厂输入 {k}：{v['description']}；默认{v.get('default','按官方工厂')}")
        for k,v in value.get('required_inputs',{}).items():print(f"必需输入 {k}：{v['description']}")
        for s in value['steps']:print(f"  {s['id']}：{s['name']}"+('' if s['implemented'] else '（待适配，不能作为通过）'))
        print(f"准备：./mc.sh examples prepare {args.example_id} --dir cases/新目录")
    else:
        print('实例：'+value.get('instance_id',args.instance_id if hasattr(args,'instance_id') else ''))
        if 'state' in value:print('状态：'+value['state'])
        if value.get('case_server_path'):print('输入目录（服务端）：'+value['case_server_path'])
        for step,job in value.get('steps',{}).items():print(f"  {step}: {job['state']}  job_id={job['job_id']}")
        if value.get('next_step'):
            print(f"下一步：./mc.sh examples step {value['instance_id']} {value['next_step']['id']} --wait")
        if action in ('plot','report'):
            print('产物（服务端）：'+value['server_path'])
            if value.get('series_csv'):print('完整数据CSV（相对工作区）：'+value['series_csv'])
        result=value.get('calculation')
        if result:
            print('原始结果（服务端）：'+result['statepoint_server_path'])
            if result.get('keff'):print('keff：'+str(result['keff']))
            for tally in result.get('tallies',[]):
                print(f"可选计分 {tally['id']} {tally['name']}：score={','.join(tally['scores'])}；nuclide={','.join(tally['nuclides'])}；维度={tally['shape']}")
            for warning in result.get('warnings',[]):print('提示：'+warning)
        for script in value.get('script_results',[]):
            print('阶段 '+script['stage']+' 结果：'+script['summary_server_path'])
            if script.get('kind')=='depletion':print('燃耗时间（天）：'+str(script['series']['time_days']))
        notebook=value.get('notebook')
        if notebook:
            print(f"Notebook代码单元：实际执行{notebook['executed_code_cells']}/{notebook['code_cells']}；错误{len(notebook['errors'])}")
            print('阅读实际图表与输出：'+notebook['report_server_path'])
            for stage in notebook.get('calculations',[]):
                print(f"  求解阶段 {stage['id']}：{stage['kind']} / {stage['state']}；原代码单元{stage['original_cell_index']}；结果{','.join(stage['outputs']) or '无新的StatePoint'}")
            if notebook.get('calculations'):print(f"读取某次求解：./mc.sh examples results {value['instance_id']} --calculation 四位阶段编号")
        for stage in value.get('stage_artifacts',[]):
            print('步骤 '+stage['step']+' 产物目录：'+stage['directory'])
            for f in stage['artifacts']:
                name=f['server_path'].rsplit('/',1)[-1]
                if name.startswith(('statepoint.', 'report.', 'plot-', 'notebook-report')):
                    print('  '+f['server_path'])
        if value.get('stage_artifacts'):print('完整文件清单：在同一命令末尾加 --json。各阶段job_id与实际产物一并保留。')
    return 0
