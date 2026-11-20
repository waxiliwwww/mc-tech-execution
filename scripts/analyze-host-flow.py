"""Offline accounting of retained host evidence; never contacts a model or solver.

Usage tokens are provider-reported, not a tokenizer estimate. Payload categories
are visible UTF-8 text only: hidden reasoning, provider envelopes and unrecorded
messages cannot be reconstructed from these evidence files.
"""
import argparse, csv, hashlib, json
from pathlib import Path

def compact(value):
    return json.dumps(value,ensure_ascii=False,separators=(',',':'))

def js_length(text):
    return len(text.encode('utf-16-le'))//2

def capped(text, marker):
    raw=text.encode('utf-16-le')
    return text if len(raw)<=48000 else raw[:48000].decode('utf-16-le',errors='replace')+'\n'+marker

def analyze(source, context_window):
    run=json.loads((source/'C-result.json').read_text())
    visibility=json.loads((source/'C-visibility.json').read_text())
    task=json.loads((source/'C-task.json').read_text())
    session=json.loads((source/'C.json').read_text())
    budget=json.loads((source/'budget.json').read_text())
    if not len(run['turns'])==len(run['usage'])==run['network_requests']:
        raise ValueError('Cannot align usage to turns: '+str(source))
    sizes={'system':len(visibility['system'].encode()),'tool_definitions':len(compact(visibility['schemas']).encode()),
           'user_text':len(task['input'].encode()),'assistant_text_and_arguments':0,'tool_results':0}
    projected_total=0;index=0;rows=[];calls=[];previous_end=budget['started'];previous_round=0
    for n,(turn,usage) in enumerate(zip(run['turns'],run['usage']),1):
        row={'request':n,**sizes,'projected_tool_results_counterfactual':projected_total,
             **usage['usage'],'tool_ms':0,'model_client_gap_ms':None,'gap_from_request':None}
        row['adapter_input_plus_cache_read']=row.get('inputTokens',0)+row.get('cacheReadTokens',0)
        if turn['blocks'] and any(b['type']=='tool-call' for b in turn['blocks']):
            first=run['called'][index]['started']
            row['model_client_gap_ms']=first-previous_end;row['gap_from_request']=previous_round+1
        for block in turn['blocks']:
            if block['type']=='text':sizes['assistant_text_and_arguments']+=len(block['text'].encode())
            if block['type']!='tool-call':continue
            sizes['assistant_text_and_arguments']+=len(compact(block).encode())
            call=run['called'][index];index+=1
            if call['name']!=block['name']:raise ValueError('Call order mismatch')
            raw=compact(call['result'])
            # Shell already has its own JSON render; MCP uses retained content.
            projected='\n'.join(b['text'] for b in call['result'].get('content',[]) if b.get('type')=='text') if call['name'].startswith('mcp__') else raw
            corrected='final-delivery' in source.name
            marker=('[TRUNCATED: retained raw tool results: '+session['host_result_path']+']') if corrected else (
                '[TRUNCATED: full tool result saved in '+session['directory']+'/result.json; read it with shell if necessary]')
            old=capped(raw,marker);good=capped(projected,marker)
            sent=good if corrected else old
            sizes['tool_results']+=len(sent.encode());projected_total+=len(good.encode())
            row['tool_ms']+=call['duration_ms'];previous_end=call['started']+call['duration_ms'];previous_round=n
            calls.append({'request':n,'name':call['name'],'duration_ms':call['duration_ms'],
                          'raw_json_chars':js_length(raw),'official_text_chars':js_length(projected),
                          'retained_sent_visible_bytes':len(sent.encode()),'projected_capped_bytes':len(good.encode()),
                          'truncated':js_length(projected if corrected else raw)>24000})
        rows.append(row)
        if not any(b['type']=='tool-call' for b in turn['blocks']) and n<len(run['turns']):
            sizes['user_text']+=len(task['continuation'].encode())
    if index!=len(run['called']):raise ValueError('Unused tool records')
    return {'source':source.name,'requests':run['network_requests'],'elapsed_ms':run['elapsed_ms'],
            'tool_ms':sum(c['duration_ms'] for c in calls),
            'unattributed_model_client_setup_ms':run['elapsed_ms']-sum(c['duration_ms'] for c in calls),
            'context_window':context_window,'rows':rows,'calls':calls,
            'source_sha256':{n:hashlib.sha256((source/n).read_bytes()).hexdigest() for n in ['C-result.json','C-visibility.json','C-task.json','C.json','budget.json']},
            'limits':['Provider usage is retained as reported; upstream accounting is not independently verified.',
                      'Visible UTF-8 categories exclude hidden reasoning, provider envelopes and unrecorded blocks; these are not per-category tokens.',
                      'Tools were executed sequentially by the probe. Gaps include model/network/client/file I/O; missing tool-free rounds cannot be split.',
                      'Total minus tool time includes startup, model/network/client and cleanup, not pure inference.',
                      'Original and short replay are different tasks and histories, not a controlled before/after performance benchmark.']}

def plot(runs,out,font_path=None):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    font=font_path or Path('/mnt/c/Windows/Fonts/msyh.ttc')
    if font_path and not font.is_file():raise ValueError('--font指定的字体文件不存在：'+str(font_path))
    if not font.is_file():
        for family in ['Noto Sans CJK SC','WenQuanYi Micro Hei','Microsoft YaHei','PingFang SC']:
            try:
                font=Path(font_manager.findfont(family,fallback_to_default=False));break
            except ValueError:pass
        else:raise ValueError('绘图需要中文字体；使用 --font /字体文件路径 指定，避免生成缺字图。')
    font_manager.fontManager.addfont(str(font))
    plt.rcParams['font.family']=font_manager.FontProperties(fname=str(font)).get_name()
    plt.rcParams['axes.unicode_minus']=False
    plt.rcParams.update({'font.size':10,'axes.grid':True,'grid.alpha':.2,'figure.facecolor':'white'})
    fig,axes=plt.subplots(2,2,figsize=(13,8),layout='constrained')
    for run,color in zip(runs,['#2865a8','#d17629']):
        x=[r['request'] for r in run['rows']];label='原旅程' if run['requests']==14 else '只复测交付'
        for key,style,name in [('inputTokens','-','input'),('cacheReadTokens','--','缓存读取'),('cacheWriteTokens','-.','缓存写入'),('outputTokens',':','输出')]:
            axes[0,0].plot(x,[r.get(key,0) for r in run['rows']],style,color=color,label=label+' / '+name)
        axes[0,1].plot(x,[r['adapter_input_plus_cache_read'] for r in run['rows']],marker='.',color=color,label=label)
    axes[0,0].set(title='每次请求的接口报告 token',ylabel='Token（保留原字段）');axes[0,0].legend(fontsize=7)
    window=runs[0]['context_window']
    axes[0,1].axhline(window,color='#b33434',ls='--',label=f'客户端窗口 {window:,}');axes[0,1].set(title='客户端判定超限时使用的数值',ylabel='input + 缓存读取');axes[0,1].legend(fontsize=8)
    original=runs[0];x=[r['request'] for r in original['rows']]
    for key,label in [('system','系统指引'),('tool_definitions','工具定义'),('user_text','用户正文'),('assistant_text_and_arguments','助手正文与调用参数'),('tool_results','累计工具结果')]:
        axes[1,0].plot(x,[r[key]/1024 for r in original['rows']],label=label)
    axes[1,0].plot(x,[r['projected_tool_results_counterfactual']/1024 for r in original['rows']],ls='--',label='同份结果：单份表示离线重放')
    axes[1,0].set(title='原旅程每次请求前的可见文本构成',ylabel='UTF-8 KiB（不是 token）');axes[1,0].legend(fontsize=7)
    for run,color in zip(runs,['#2865a8','#d17629']):
        axes[1,1].plot([r['request'] for r in run['rows']],[r['tool_ms']/1000 for r in run['rows']],marker='.',color=color,label='原旅程' if run['requests']==14 else '只复测交付')
    axes[1,1].set(title='每次请求后的工具实测耗时',ylabel='秒（不含模型等待）');axes[1,1].legend(fontsize=7)
    for ax in axes.flat:ax.set_xlabel('模型请求序号');ax.set_xticks(range(1,15));ax.tick_params(labelsize=8)
    fig.savefig(out/'flow-cost.png',dpi=155);plt.close(fig)
    fig,ax=plt.subplots(figsize=(12,3.5),layout='constrained')
    labels=['原旅程 117.623秒','只复测交付 37.251秒'];left=[0,0]
    groups=[('环境/检查/接入/改参',lambda n:any(x in n for x in ['environment','inspect','import','parameters','prepare_case','update_tally'])),
            ('预览',lambda n:'preview_input' in n),('提交与查询',lambda n:any(x in n for x in ['submit_job','job_status','list_artifacts'])),
            ('结果包',lambda n:'generate_result_bundle' in n),('Shell',lambda n:n=='shell')]
    for label,predicate in groups:
        values=[sum(c['duration_ms'] for c in r['calls'] if predicate(c['name']))/1000 for r in runs]
        ax.barh(labels,values,left=left,label=label);left=[x+y for x,y in zip(left,values)]
    values=[r['unattributed_model_client_setup_ms']/1000 for r in runs]
    ax.barh(labels,values,left=left,label='其余：模型/网络/客户端/初始化/清理',color='#b8bec8')
    ax.set(title='整体墙钟时间：工具可拆分，其余尚不能细分',xlabel='秒');ax.legend(fontsize=8,loc='lower right');ax.invert_yaxis()
    fig.savefig(out/'flow-time.png',dpi=155);plt.close(fig)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source-root',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--font',type=Path,help='中文字体文件；默认发现已装CJK字体或WSL中的微软雅黑');a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=True)
    names=['execution-preview-experience-20261120','execution-preview-final-delivery-20261120']
    context_file=a.source_root/names[0]/'offline-diagnosis.json'
    window=json.loads(context_file.read_text())['contextWindow']
    baselines=[json.loads((a.source_root/n/'config-baseline.json').read_text()) for n in names]
    if baselines[0]!=baselines[1]:raise ValueError('Model configuration baselines differ; cannot share context-window evidence')
    runs=[analyze(a.source_root/n,window) for n in names]
    (a.output/'metrics.json').write_text(json.dumps({'context_evidence':{'source':'offline-diagnosis.json','sha256':hashlib.sha256(context_file.read_bytes()).hexdigest(),'same_model_configuration_baseline':True},'runs':runs},ensure_ascii=False,indent=2))
    for run in runs:
        with (a.output/(run['source']+'.csv')).open('w') as f:
            w=csv.DictWriter(f,fieldnames=list(run['rows'][0]));w.writeheader();w.writerows(run['rows'])
    plot(runs,a.output,a.font)
    for run in runs:print(run['source'],run['elapsed_ms'],run['tool_ms'],run['rows'][-1]['adapter_input_plus_cache_read'],{k:run['rows'][-1][k] for k in ['system','tool_definitions','tool_results','projected_tool_results_counterfactual']})
if __name__=='__main__':main()
