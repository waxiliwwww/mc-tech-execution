#!/usr/bin/env python3
"""Direct CLI uses the same registry and schema validation as MCP; JSON on stdout."""
import argparse
import asyncio
import json
import os
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description='MC接口工具直接调用；不需要模型或MCP服务')
    parser.add_argument('--workspace', help='业务工作目录；默认项目workspace')
    parser.add_argument('--config', help='OpenMC运行配置；默认config/openmc.json')
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('list', help='输出工具定义和输入输出schema')
    from examples_cli import configure_parser
    configure_parser(sub.add_parser('examples', help='按官方例题逐步准备、计算和查看产物'))
    call = sub.add_parser('call', help='调用一个工具')
    call.add_argument('tool')
    call.add_argument('arguments', nargs='?', default='{}', help='一个JSON对象')
    args = parser.parse_args()
    if args.workspace:
        os.environ['MC_WORKSPACE'] = os.path.abspath(args.workspace)
    if args.config:
        os.environ['MC_OPENMC_CONFIG'] = os.path.abspath(args.config)
    from adapters import ToolExecutionError, execute_tool, public_tool
    from tools import TOOLS
    try:
        if args.action == 'examples':
            from examples_cli import execute
            try:return execute(args)
            except (ValueError, OSError, KeyboardInterrupt) as exc:
                context=getattr(args,'_example_context',{})
                message='等待被中断；不代表计算停止' if isinstance(exc,KeyboardInterrupt) else str(exc)
                failure={'error':message,**context}
                instance=context.get('instance_id')
                if instance:
                    failure['next_actions']=[f'./mc.sh examples status {instance}',f'./mc.sh examples cancel {instance}']
                    try:
                        from example_workflows import example_status
                        current=example_status(instance);failure['state']=current['state']
                        if context.get('step') in current['steps']:
                            failure['job_id']=current['steps'][context['step']]['job_id']
                    except (ValueError,OSError):failure['state']='unknown'
                if args.json:print(json.dumps(failure,ensure_ascii=False),file=sys.stderr)
                else:
                    print('官方例题操作失败：'+message,file=sys.stderr)
                    if instance:print('实例：'+instance+'\n继续查询：'+failure['next_actions'][0],file=sys.stderr)
                return 130 if isinstance(exc,KeyboardInterrupt) else 1
        if args.action == 'list':
            value = {'tools': [public_tool(t) for t in TOOLS]}
        else:
            tool = next((t for t in TOOLS if t['name'] == args.tool), None)
            if tool is None:
                raise ToolExecutionError('工具不存在；使用list查看工具清单')
            try:
                arguments = json.loads(args.arguments)
            except ValueError as exc:
                raise ToolExecutionError('arguments须为有效JSON对象') from exc
            if args.tool == 'apply_repair':
                preview_tool = next(t for t in TOOLS if t['name'] == 'read_repair_plan')
                preview = asyncio.run(execute_tool(preview_tool, arguments))
                if preview['state'] != 'proposed':raise ToolExecutionError('计划已经执行；不会重复写入')
                if not preview['file_unchanged']:raise ToolExecutionError('文件在预览后发生变化；请重新生成修复计划并确认')
                print(preview['diff'], file=sys.stderr)
                if not sys.stdin.isatty():raise ToolExecutionError('非交互输入不能代替人工确认；请在终端查看差异并确认，或使用有确认策略的MCP宿主。')
                print('只应用上述差异；专业正确性仍需校核和重算。确认修改？[y/N] ', end='', file=sys.stderr, flush=True)
                if sys.stdin.readline().strip().lower() not in ('y', 'yes'):
                    raise ToolExecutionError('用户拒绝本次修改，文件保持不变')
                current = asyncio.run(execute_tool(preview_tool, arguments))
                if current != preview:raise ToolExecutionError('确认期间计划或输入发生变化，请重新预览后确认')
            value = asyncio.run(execute_tool(tool, arguments))
        print(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2))
        return 0
    except ToolExecutionError as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
