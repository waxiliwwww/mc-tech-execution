#!/usr/bin/env python3
"""Render the pinned official API inventory into the existing overview offline.

The upstream index determines the inventory; explicit reviewed mappings determine
tool support. Seeing a Python name in source never automatically means support.
"""
from pathlib import Path
import argparse
import hashlib
import html
import json
import re

ROOT = Path(__file__).resolve().parents[1]
START = '<!-- OPENMC-API-COVERAGE:START -->'
END = '<!-- OPENMC-API-COVERAGE:END -->'


def load(root=ROOT):
    assets = root / 'docs/assets/openmc-api'
    manifest = json.loads((assets/'manifest.json').read_text())
    mapping = json.loads((root/'config/openmc-api-coverage.json').read_text())
    for source in manifest['sources']:
        data = (assets/source['file']).read_bytes()
        if hashlib.sha256(data).hexdigest() != source['sha256']:
            raise ValueError('官方索引指纹不符：' + source['file'])
    return manifest, mapping


def symbols(text, module):
    result = []
    active = False
    section = ''
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if index + 1 < len(lines) and re.fullmatch(r'[-+~^]{3,}', lines[index+1].strip()):
            section = line.strip()
        if line.startswith('.. autosummary::'):
            active = True
            continue
        if active and line and not line.startswith((' ', '\t')):
            active = False
        match = re.match(r'\.\. (?:auto)?(?:data|function|class)::\s+(\S+)', line)
        value = match[1] if match else line.strip() if active and re.fullmatch(r'\s+[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*\s*', line) else None
        if value:
            name = value if value.startswith('openmc.') else module + '.' + value
            if not any(row['name'] == name for row in result):
                result.append({'name':name, 'official_section':section, 'origin':'index'})
    return result


def inventory(root=ROOT):
    manifest, mapping = load(root)
    rows = []
    for module in manifest['modules']:
        found = symbols((root/'docs/assets/openmc-api'/ (module['page']+'.rst')).read_text(), module['name'])
        for extra in manifest['supplementary']:
            if extra['module'] == module['page']:
                found.append({'name':extra['name'], 'official_section':extra['kind'],
                              'origin':'supplement', 'source_url':extra['source_url']})
        for row in found:
            group_id = mapping['assignments'].get(row['name'], mapping['module_defaults'][module['page']])
            row.update(module=module['page'], group_id=group_id)
            rows.append(row)
    names = {r['name'] for r in rows}
    if len(names) != len(rows):
        raise ValueError('接口名称重复，须明确别名而不是重复计数')
    unknown = set(mapping['assignments']) - names
    if unknown:
        raise ValueError('对照引用了索引中不存在的接口：' + ', '.join(sorted(unknown)))
    return manifest, mapping, rows


def render(root=ROOT):
    manifest, mapping, rows = inventory(root)
    esc = html.escape
    indexed = sum(row['origin'] == 'index' for row in rows)
    output = [START, '<details id="api-coverage"><summary>OpenMC公开接口对照：九个官方模块、'+str(indexed)+'个索引名称及常用补充</summary>',
        '<p><strong>先按任务看能力落点，再按模块查具体接口。</strong>本表对照OpenMC '+esc(manifest['version'])+'官方Python API索引；固定源码提交<code>'+manifest['commit'][:12]+'</code>，核对日期'+manifest['retrieved_on']+'。共'+str(indexed)+'个去重索引名称，另补'+str(len(rows)-indexed)+'个别名/兼容或源码工厂；名称数量不是功能完成率。</p>',
        '<p>状态描述的是工具包开放到哪一步：通用创建/修改、模板内部使用、原生接入待核实、结果读取，或未封装。按类/函数/显式变量列出，<strong>不代表每个类的全部方法、属性和组合已开放</strong>；原生文件可导入也不等于同类对象可从自然语言创建。官方标为内部/辅助的名称仍列出供查阅。CLI/MCP调用说明见<a href="reference.html">工具接口参考</a>；实际运行证据见<a href="project-records.html">实跑记录</a>，本次是文档/源码审计，不是重新实跑全部接口。</p>',
        '<div class="table-wrap"><table id="api-module-map"><thead><tr><th>官方模块</th><th>索引项</th><th>本包开放到哪里</th></tr></thead><tbody>']
    overview = {
        'base':'受控建模、有限改参、计分、本地任务与受管结果；高级类型逐项看下方边界。',
        'model':'模型容器与少数模板辅助；没有任意复合曲面或TRISO构建工具。',
        'examples':'官方PWR组件/简化全堆模板和固定例题工厂清单；通用建模不局限于这些模板。',
        'deplete':'CoupledOperator + Predictor运行与Results部分读取；其他积分器和高级活化未开放。',
        'mgxs':'示例内部使用、既有多群库接入；没有通用多群截面生成/凝聚服务。',
        'stats':'Point/Box、可选单能、默认各向同性；其他分布没有通用创建入口。',
        'data':'配置/发现/核对已有数据；ENDF/ACE/NJOY等加工API未封装。',
        'capi':'燃耗/Notebook适配内部使用部分绑定；没有用户可控的内存态批次会话。',
        'openmoc':'没有OpenMC/OpenMOC对象转换工具。',
    }
    for module in manifest['modules']:
        count = sum(r['module']==module['page'] and r['origin']=='index' for r in rows)
        output.append('<tr><td><a href="#api-module-'+module['page']+'"><code>'+module['name']+'</code></a></td><td>'+str(count)+'</td><td>'+esc(overview[module['page']])+'</td></tr>')
    output.append('</tbody></table></div><details id="api-functional-details"><summary>按具体功能查工具入口、创建/接入边界与限制</summary><div class="table-wrap"><table id="api-functional-map"><thead><tr><th>功能落点</th><th>本包状态与范围</th><th>怎么用</th></tr></thead><tbody>')
    for key, group in mapping['groups'].items():
        if key == 'unexposed':
            continue
        tools = '、'.join('<a href="reference.html#'+esc(t)+'"><code>'+esc(t)+'</code></a>' for t in group['tools']) or '没有专用MCP入口'
        output.append('<tr id="api-group-'+esc(key)+'"><th>'+esc(group['label'])+'</th><td><strong>'+esc(group['state'])+'</strong>。'+esc(group['boundary'])+'</td><td>'+tools+'</td></tr>')
    output.append('</tbody></table></div></details><p>作业命名、预览与哈希、扫描、诊断/确认修复、知识检索和报告是本包增加的工程封装，不把它们当作OpenMC原生API条目。Slurm适配仅离线/受控调度验证，HPC账户与真实集群待联调；也没有把官方Notebook能执行某段代码当作已经开放该段代码对应的通用工具。</p>')
    for module in manifest['modules']:
        entries = [r for r in rows if r['module'] == module['page']]
        output.append('<details id="api-module-'+module['page']+'"><summary>'+esc(module['name'])+' — '+esc(module['label'])+'（'+str(sum(r['origin']=='index' for r in entries))+'个索引项'+('，另有补充' if any(r['origin']=='supplement' for r in entries) else '')+'）</summary><p><a href="'+module['documentation']+'">官方模块文档</a> · <a href="assets/openmc-api/'+module['page']+'.rst">随包固定原始索引</a>。点击功能落点可回看参数边界和工具入口。</p><div class="table-wrap"><table><thead><tr><th>官方接口名</th><th>本包状态</th><th>对应功能与限制</th></tr></thead><tbody>')
        for row in entries:
            group = mapping['groups'][row['group_id']]
            # Module pages provide all index entries; the exact public name is
            # shown rather than inventing a generated class-page URL for aliases.
            url = row.get('source_url', module['documentation'])
            group_link = ('<a href="#api-group-'+row['group_id']+'">'+esc(group['label'])+'</a>') if row['group_id'] != 'unexposed' else esc(group['label'])
            note = (' · '+esc(row['official_section'])) if row['origin']=='supplement' or 'Internal' in row['official_section'] or 'Abstract' in row['official_section'] else ''
            output.append('<tr data-openmc-api="'+esc(row['name'])+'"><td><a href="'+esc(url)+'"><code>'+esc(row['name'])+'</code></a>'+note+'</td><td>'+esc(group['state'])+'</td><td>'+group_link+'</td></tr>')
        output.append('</tbody></table></div></details>')
    output.append('<details id="api-audit-basis"><summary>实现依据、证据入口与清单维护口径</summary><p>官方索引/MIT许可随包保存，<a href="assets/openmc-api/manifest.json">来源与SHA256清单</a>记录固定提交；原始索引决定有哪些名称，<a href="../config/openmc-api-coverage.json">实现对照数据</a>由源码审计决定本包状态。<code>python3 scripts/generate_api_coverage.py --check</code>检查正文与两份依据一致；新增能力需同时更新映射和相应验证，不能仅改说明为“已支持”。</p><ul>')
    for group in mapping['groups'].values():
        if group['implementation'] or group['evidence']:
            links = ['<a href="../'+esc(p)+'">'+esc(p)+'</a>' for p in group['implementation']+group['evidence']]
            output.append('<li>'+esc(group['label'])+'：'+'；'.join(links)+'</li>')
    output.append('</ul><p>类的字段细节继续看上方输入能力速查和<code>openmc_model_capabilities(detail="full")</code>返回的Schema；可改参数以<code>openmc_list_parameters</code>实际返回键为准。清单不增加合同验收承诺或物理准确率结论。</p></details></details>'+END)
    return '\n'.join(output)


def update(root=ROOT, check=False):
    target = root/'docs/index.html'
    text = target.read_text()
    generated = render(root)
    if START in text:
        before, rest = text.split(START, 1)
        _, after = rest.split(END, 1)
        desired = before + generated + after
    else:
        marker = '<section id="routes">'
        desired = text.replace(marker, generated + '\n' + marker, 1)
        if desired == text:
            raise ValueError('总览缺少插入位置，未修改')
    if check:
        if desired != text:
            raise ValueError('接口清单未同步；执行python3 scripts/generate_api_coverage.py')
    else:
        target.write_text(desired)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    update(check=args.check)
    print('OpenMC官方接口清单与总览一致' if args.check else '已更新docs/index.html的官方接口对照')
