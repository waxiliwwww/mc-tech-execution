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
            value = asyncio.run(execute_tool(tool, arguments))
        print(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2))
        return 0
    except ToolExecutionError as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
