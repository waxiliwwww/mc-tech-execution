"""Execute an intact pinned notebook, retaining errors, figures and readable output."""
from __future__ import annotations
import base64
import html
import json
import os
from pathlib import Path
import sys
from openmc_runtime import ROOT, atomic_json, config


def render_report(notebook, title):
    sections=[];figures=[];code_cells=0;executed=0;errors=[]
    stage_path=Path('notebook-stages.json')
    stages=json.loads(stage_path.read_text()) if stage_path.exists() else {'calculations':[],'reads':[]}
    for number,cell in enumerate(notebook.cells):
        if cell.cell_type!='code' or not cell.source.strip():continue
        adapter=bool(cell.get('metadata',{}).get('mc_adapter'))
        if not adapter:
            code_cells+=1
            if cell.get('execution_count') is not None:executed+=1
        parts=[]
        for output in cell.get('outputs',[]):
            if output.output_type=='error':
                message=output.get('ename','')+': '+output.get('evalue','');errors.append({'cell':number,'message':message});parts.append('<pre class="error">'+html.escape(message)+'</pre>')
            elif output.output_type=='stream':parts.append('<pre>'+html.escape(output.get('text',''))+'</pre>')
            else:
                data=output.get('data',{})
                if 'image/png' in data:
                    raw=data['image/png'];raw=''.join(raw) if isinstance(raw,list) else raw
                    name=f'notebook-figure-{number}-{len(figures)}.png';Path(name).write_bytes(base64.b64decode(raw));figures.append(name)
                    parts.append('<img alt="本次执行单元展示的图片" src="data:image/png;base64,'+raw+'">')
                elif 'text/html' in data:
                    raw=data['text/html'];parts.append(''.join(raw) if isinstance(raw,list) else raw)
                elif 'text/plain' in data:
                    raw=data['text/plain'];parts.append('<pre>'+html.escape(''.join(raw) if isinstance(raw,list) else raw)+'</pre>')
        if cell.get('execution_count') is not None:
            references=[]
            for stage in stages['calculations']:
                if stage['original_cell_index']==number-1:
                    outputs=' '.join('<a href="'+html.escape(item['path'],quote=True)+'">'+html.escape(name)+'</a>' for name,item in stage['outputs'].items())
                    inputs=stage.get('inputs') or stage.get('original_inputs',{})
                    input_links=' '.join('<a href="'+html.escape(item['path'],quote=True)+'">'+html.escape(name)+'</a>' for name,item in inputs.items())
                    references.append('<p>阶段 '+stage['id']+'（'+html.escape(stage['kind'])+' / '+html.escape(stage['state'])+'）：输入 '+input_links+'；原始结果 '+(outputs or '此阶段未产生新的StatePoint')+'</p>')
                    for change in stage.get('preflight',{}).get('adaptations',[]):
                        references.append('<p class="error">已公开的运行适配：'+html.escape(change['setting'])+'，'+html.escape(str(change['before']))+' → '+html.escape(str(change['after']))+'。'+html.escape(change['note'])+'</p>')
            for read in stages['reads']:
                if read['original_cell_index']==number-1:
                    links=' '.join('<a href="'+html.escape(x['path'],quote=True)+'">阶段'+x['calculation_id']+'原始结果</a>' for x in read['matched_snapshots'])
                    references.append('<p>本单元实际读取 '+html.escape(read['requested_path'])+'；'+(links or '未匹配到已完成求解快照')+'；SHA256 '+read['sha256']+'</p>')
            label='工具包运行适配' if adapter else '代码单元 '+str(number-1)
            sections.append('<section><h2>'+label+'</h2><details><summary>查看实际执行代码</summary><pre>'+html.escape(cell.source)+'</pre></details>'+''.join(references)+''.join(parts)+'</section>')
    text='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src &#39;none&#39;; img-src data:; style-src &#39;unsafe-inline&#39;"><title>'+html.escape(title)+'</title><style>body{font:16px/1.7 system-ui,sans-serif;color:#173147;background:#f5f8fa;max-width:1150px;margin:auto;padding:30px}section{background:white;border:1px solid #d4e2e7;padding:20px;margin:20px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#edf3f4;padding:14px}img{max-width:100%;height:auto}table{border-collapse:collapse}td,th{padding:8px;border:1px solid #d7e2e7}.error{color:#a32a26}summary{cursor:pointer;color:#087e80}</style><h1>'+html.escape(title)+'</h1><p>官方Notebook实际执行输出；代码、错误与图片均保留。部分单元会读取官方静态参考图片，应结合代码区分；专业正确性不由运行成功自动判定。</p>'+''.join(sections)+'</html>'
    text=text.replace('</style>','body{overflow-wrap:anywhere}table{display:block;max-width:100%;overflow-x:auto}@media(max-width:600px){body{padding:16px}section{padding:12px}}</style>')
    Path('notebook-report.html').write_text(text)
    return {'code_cells':code_cells,'executed_code_cells':executed,'errors':errors,'figures':figures,
            'report':'notebook-report.html','executed_notebook':'notebook.executed.ipynb',
            'calculations':stages['calculations'],'result_reads':stages['reads'],
            'stage_index':'notebook-stages.json' if stage_path.exists() else None}


def execute_notebook(item):
    import nbformat
    from nbclient import NotebookClient
    from jupyter_client.kernelspec import KernelSpecManager
    notebook=nbformat.read(item['source_file'],as_version=4)
    # Upstream notebooks contain cached outputs. Clear them before starting so
    # failed/unreached cells cannot look like this run generated their figures.
    for cell in notebook.cells:
        if cell.cell_type=='code':cell.outputs=[];cell.execution_count=None
    code_count=sum(c.cell_type=='code' and bool(c.source.strip()) for c in notebook.cells)
    # A per-job kernel spec uses the configured OpenMC Python, without touching
    # user-level kernels or assuming a system Python installation has OpenMC.
    kernels=Path('.example-kernels');spec=kernels/'mc-example';spec.mkdir(parents=True)
    atomic_json(spec/'kernel.json',{'argv':[sys.executable,'-m','ipykernel_launcher','-f','{connection_file}'],
                                  'display_name':'MC official example','language':'python'})
    bootstrap='''import sys, os
sys.path.insert(0, ROOT_PATH)
from notebook_runtime import install_runtime_checks
install_runtime_checks(EXAMPLE_ID)
'''.replace('EXAMPLE_ID',repr(item['id'])).replace('ROOT_PATH',repr(str(ROOT/'mcp')))
    notebook.cells.insert(0,nbformat.v4.new_code_cell(bootstrap,metadata={'mc_adapter':True}))
    original_code_indices=[index for index,cell in enumerate(notebook.cells)
                           if cell.cell_type=='code' and cell.source.strip()
                           and not cell.metadata.get('mc_adapter')]
    def start(cell,cell_index,**kwargs):
        if cell.cell_type!='code' or not cell.source.strip():return
        ordinal=original_code_indices.index(cell_index)+1 if cell_index in original_code_indices else 0
        atomic_json(Path('notebook-progress.json'),{'code_cell':ordinal,'original_cell_index':cell_index-1,
                    'total_original_code_cells':code_count,'state':'running'})
        print('初始化Notebook运行环境' if ordinal==0 else f'执行代码单元 {ordinal}/{code_count}',flush=True)
    client=NotebookClient(notebook,timeout=config()['job_timeout_seconds'],kernel_name='mc-example',
        resources={'metadata':{'path':str(Path.cwd())}},on_cell_start=start)
    client.km=client.create_kernel_manager()
    client.km.kernel_spec_manager=KernelSpecManager(kernel_dirs=[str(kernels.resolve())])
    failure=None
    try:client.execute()
    except BaseException as error:failure=error
    finally:
        nbformat.write(notebook,'notebook.executed.ipynb')
        result=render_report(notebook,item.get('title',item['name']))
        result.update(example_id=item['id'],source_revision=item['source_revision'],bootstrap_added=True,
                      source_code_unchanged=True,upstream_cached_outputs_cleared=True,
                      adapters=['OpenMC runtime configuration sync','real nuclear-data preflight',
                      '0.16 Plot.from_geometry delegation to SlicePlot',
                      'NumPy trapz alias to trapezoid when needed',
                      'retain actual depletion reaction rates for tutorial readback',
                      'per-solve raw input/result snapshots before notebook filenames are reused',
                      *(['CAD source sampling-efficiency abort guard; XML semantic diff recorded per solve'] if item['id']=='notebook-cad-based-geometry' else []),
                      *(['replace gamma tutorial developer-specific executable argument'] if item['id']=='notebook-gamma-detector' else [])],
                      professional_acceptance=False)
        atomic_json(Path('notebook-result.json'),result)
        atomic_json(Path('notebook-progress.json'),{'state':'failed' if failure else 'completed',**result})
    if failure:raise failure
    if result['errors']:raise RuntimeError('Notebook含实际执行错误，不能报告成功')
    return result
