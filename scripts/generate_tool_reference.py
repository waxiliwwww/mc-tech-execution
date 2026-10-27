#!/usr/bin/env python3
"""Generate portable documentation from the same schemas MCP exposes."""
import inspect
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'mcp'))
from tools import TOOLS


def cell(value):
    return str(value).replace('|', '\\|').replace('\n', ' ')


def implementation(tool):
    if 'function' in tool:
        try:
            source = Path(inspect.getsourcefile(inspect.unwrap(tool['function']))).relative_to(ROOT)
            return '函数：[' + str(source) + '](../' + source.as_posix() + ')。'
        except (TypeError, ValueError):return '函数适配；实现由注册表导入。'
    if 'program' in tool:return '命令行程序适配：[统一执行与校验](../mcp/adapters.py)。argv及工作目录由开发者在注册表配置。'
    if 'http' in tool:return 'HTTP JSON适配：[统一执行与校验](../mcp/adapters.py)。URL及凭据环境变量由开发者配置。'
    raise ValueError('工具缺少执行适配器：' + tool['name'])


def generate(definitions=None):
    definitions = TOOLS if definitions is None else definitions
    lines = ['# 工具接口参考', '',
             '由scripts/generate_tool_reference.py从实际注册表生成。CLI与MCP共用以下Schema，参数说明会随tools/list发给调用方。', '',
             '先按[使用指南](使用指南.md)走完一次计算；接自己的程序见[已有程序接入](已有程序接入.md)。本页用于查参数，无需通读。', '',
             '默认值是省略参数后的现有行为注解；Schema的default不会替程序填参。“省略时见说明”不表示可以发送null。示例表示填写方式，动态名称/单位须先调用发现接口核对。', '', '## 工具目录', '']
    for tool in definitions:lines.append('- [' + tool['name'] + '](#' + tool['name'] + ')：' + tool['description'])
    for tool in definitions:
        schema = tool['inputSchema']
        lines.extend(['', '## ' + tool['name'], '', tool['description'], '',
                      '| 参数 | 类型 | 必填 | 省略或默认 | 含义与来源 | 填写示例 |',
                      '| --- | --- | --- | --- | --- | --- |'])
        for name, field in schema.get('properties', {}).items():
            required = name in schema.get('required', [])
            default = json.dumps(field['default'], ensure_ascii=False) if 'default' in field else ('不可省略' if required else '省略时见说明')
            example = json.dumps(field['examples'][0], ensure_ascii=False) if field.get('examples') else '—'
            if not field.get('description'):raise ValueError(f"工具{tool['name']}参数{name}缺description；补Schema后再生成说明")
            lines.append('|' + '|'.join(cell(v) for v in [name, field.get('type', '见Schema'), '是' if required else '否', default, field['description'], example]) + '|')
        if not schema.get('properties'):lines.append('| 无参数 | object | — | 空对象 | 不接受额外参数 | {} |')
        output = tool.get('outputSchema', {'type': 'object'})
        lines.extend(['', '返回：' + output.get('description', '由业务实现返回JSON对象。'), ''])
        if output.get('properties'):
            lines.extend(['| 关键返回字段 | 含义 |', '| --- | --- |'])
            for name, field in output['properties'].items():lines.append('|' + cell(name) + '|' + cell(field.get('description', '见Schema')) + '|')
            lines.extend(['', '返回分支可能不同；未声明必填的说明字段不保证每次出现。'])
        lines.extend(['', implementation(tool), '', '完整输入Schema（含嵌套参数、枚举及范围）：', '',
                      '```json', json.dumps(schema, ensure_ascii=False, indent=2), '```', '',
                      '输出Schema：', '', '```json', json.dumps(output, ensure_ascii=False, indent=2), '```'])
    return '\n'.join(lines) + '\n'


if __name__=='__main__':
    target=ROOT/'docs/工具接口参考.md';target.write_text(generate(),encoding='utf-8');print(target)
