#!/usr/bin/env python3
"""Generate portable Markdown input reference from the actual tool registry."""
import inspect
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'mcp'))
from tools import TOOLS


def generate():
    lines=['# 工具接口参考','','由scripts/generate_tool_reference.py从mcp/tools.py生成；修改工具后重新运行。参数由CLI和标准MCP共用，业务范围及错误/路径约定见[MCP服务与接口](MCP服务与接口.md)。','','## 工具目录','']
    for tool in TOOLS:lines.append('- ['+tool['name']+'](#'+tool['name']+')：'+tool['description'])
    for tool in TOOLS:
        lines.extend(['','## '+tool['name'],'',tool['description'],'','| 参数 | 是否必填 | 服务端默认值 |','| --- | --- | --- |'])
        function=inspect.unwrap(tool['function'])
        signature=inspect.signature(function)
        for name in tool['inputSchema'].get('properties',{}):
            parameter=signature.parameters.get(name)
            default='无' if parameter is None or parameter.default is inspect.Parameter.empty else json.dumps(parameter.default,ensure_ascii=False)
            lines.append('|'+name+'|'+('是' if name in tool['inputSchema'].get('required',[]) else '否')+'|'+default+'|')
        if not tool['inputSchema'].get('properties'):lines.append('| 无参数 | — | — |')
        source=Path(inspect.getsourcefile(function)).relative_to(ROOT)
        lines.extend(['','实现入口：['+str(source)+'](../'+str(source)+')。','','完整输入Schema：','','```json',json.dumps(tool['inputSchema'],ensure_ascii=False,indent=2),'```'])
    return '\n'.join(lines)+'\n'


if __name__=='__main__':
    target=ROOT/'docs/工具接口参考.md';target.write_text(generate(),encoding='utf-8');print(target)
