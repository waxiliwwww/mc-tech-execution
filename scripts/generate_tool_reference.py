#!/usr/bin/env python3
"""Generate an offline HTML lookup from the exact schemas exposed by CLI/MCP."""
from html import escape
import inspect
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'mcp'))
from tools import TOOLS


def implementation(tool):
    if 'function' in tool:
        try:
            source = Path(inspect.getsourcefile(inspect.unwrap(tool['function']))).relative_to(ROOT).as_posix()
            return f'函数：<a href="../{escape(source, quote=True)}">{escape(source)}</a>。'
        except (TypeError, ValueError):
            return '函数适配；实现由注册表导入。'
    if 'program' in tool:
        return '命令行程序适配：<a href="../mcp/adapters.py">统一执行与校验</a>。argv 和工作目录由开发者配置。'
    if 'http' in tool:
        return 'HTTP JSON适配：<a href="../mcp/adapters.py">统一执行与校验</a>。URL 和凭据环境变量由开发者配置。'
    raise ValueError('工具缺少执行适配器：' + tool['name'])


def table(headers, rows):
    return ('<div class="table-wrap"><table><thead><tr>' + ''.join('<th>' + escape(v) + '</th>' for v in headers)
            + '</tr></thead><tbody>' + ''.join('<tr>' + ''.join('<td>' + escape(str(v)) + '</td>' for v in row)
                                              + '</tr>' for row in rows) + '</tbody></table></div>')


def generate(definitions=None):
    definitions = TOOLS if definitions is None else definitions
    nav = [('index', '认识工具包'), ('compute', '完成一次计算'), ('official-examples', '全部官方例题'), ('inputs', '自有模型与源码'),
           ('mcp', '封装与部署 MCP'), ('reference', '查工具参数')]
    navigation = ''.join(f'<a href="{name}.html"' + (' aria-current="page"' if name == 'reference' else '')
                         + f'>{label}</a>' for name, label in nav)
    lines = ['<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width,initial-scale=1">',
             '<title>工具参数参考 · MC 工具包</title><link rel="stylesheet" href="assets/manual.css">',
             '<script src="assets/manual.js" defer></script></head><body>',
             '<a class="sr-only" href="#content">跳到正文</a><aside class="sidebar">',
             '<a class="brand" href="index.html">MC 工具包<br>使用与开发手册</a>',
             '<p class="edition">OpenMC · 独立 MCP</p><nav aria-label="文档导航">', navigation,
             '</nav><p class="minor">先沿一个实际任务操作。<br>参数全集随用随查。<br>本手册可离线打开。</p></aside>',
             '<main id="content"><p class="eyebrow">按需查阅 · 来自实际工具注册表</p><h1>查一个工具的参数</h1>',
             '<p class="lead">先按操作指南完成任务，本页用于查询具体字段。CLI 与 MCP 使用同一 Schema；',
             '下面的参数说明也会通过 tools/list 发给调用方。</p>',
             '<div class="note">默认值表示实际省略行为；Schema 注解不替程序填参。可省略不代表可填 null。',
             '模型 ID、参数名、计分和单位须先从实际输入/结果发现，不能照抄示例。</div>',
             '<label class="search-label" for="tool-search">按工具名、字段或用途查找</label>',
             '<input class="search" type="search" id="tool-search" placeholder="例如：particles、输入、openmc_plot_results" autocomplete="off">',
             '<p class="muted" id="tool-count" role="status"></p>',
             '<p class="no-results" id="no-results" hidden>没有匹配的工具。试试参数名或更短的关键词。</p>']
    for tool in definitions:
        name = tool['name']; schema = tool['inputSchema']; rows = []
        for parameter, field in schema.get('properties', {}).items():
            if not field.get('description'):
                raise ValueError(f'工具{name}参数{parameter}缺description；补Schema后再生成说明')
            required = parameter in schema.get('required', [])
            default = json.dumps(field['default'], ensure_ascii=False) if 'default' in field else ('不可省略' if required else '省略时见说明')
            example = json.dumps(field['examples'][0], ensure_ascii=False) if field.get('examples') else '—'
            rows.append([parameter, field.get('type', '见 Schema'), '是' if required else '否', default, field['description'], example])
        if not rows: rows.append(['无参数', 'object', '—', '空对象', '不接受额外参数', '{}'])
        lines.extend([f'<details class="tool" id="{escape(name, quote=True)}"><summary>{escape(name)}',
                      f'<span class="tool-purpose">{escape(tool["description"])}</span></summary>',
                      table(['参数', '类型', '必填', '省略或默认', '含义与来源', '填写示例'], rows)])
        output = tool.get('outputSchema', {'type': 'object'})
        lines.append('<h3>返回怎么看</h3><p>' + escape(output.get('description', '由业务实现返回 JSON 对象。')) + '</p>')
        if output.get('properties'):
            lines.append(table(['关键返回字段', '含义'], [[key, field.get('description', '见 Schema')]
                                                       for key, field in output['properties'].items()]))
            lines.append('<p class="muted">返回分支可能不同；未声明必填的说明字段不保证每次出现。</p>')
        for title, value in [('完整输入 Schema：嵌套参数、枚举与范围', schema), ('输出 Schema', output)]:
            lines.extend(['<details><summary>' + title + '</summary><pre><code>',
                          escape(json.dumps(value, ensure_ascii=False, indent=2)), '</code></pre></details>'])
        lines.extend(['<p class="source">' + implementation(tool) + '</p></details>'])
    lines.extend(['<footer>由 scripts/generate_tool_reference.py 从实际注册表生成 · 无需联网或启动服务</footer>',
                  '</main></body></html>'])
    return '\n'.join(lines) + '\n'


if __name__ == '__main__':
    target = ROOT / 'docs/reference.html'
    target.write_text(generate(), encoding='utf-8')
    print(target)
