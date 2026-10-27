// Terminal rendering and credential redaction, 2026-10-12.
// Only presentation belongs here; the Harness owns models, sessions and tools.
export function createRedactor(secrets) {
  const forms = [...new Set([...secrets].filter(Boolean).flatMap(value => [value, JSON.stringify(value).slice(1, -1)]))].sort((a, b) => b.length - a.length);
  const redact = (value, interrupted = false) => {
    const filter = createStreamRedactor(forms);
    return filter.write(String(value)) + filter.finish({ interrupted });
  };
  const value = item => {
    if (typeof item === 'string') return redact(item);
    if (Array.isArray(item)) return item.map(value);
    if (item && typeof item === 'object') return Object.fromEntries(Object.entries(item).map(([key, child]) => [redact(key), value(child)]));
    return item;
  };
  return { redact, value, stream: () => createStreamRedactor(forms) };
}

// Retain only a possible credential prefix between packets. Even cancellation
// must hide that suffix: emitting an incomplete credential would expose it.
export function createStreamRedactor(forms = []) {
  forms = forms.filter(Boolean).sort((a, b) => b.length - a.length);
  let pending = '', bearer = false;
  const consume = (final, interrupted = false) => {
    let output = '';
    while (pending) {
      if (bearer) {
        const boundary = pending.search(/[\s"\\]/);
        if (boundary < 0) { pending = ''; break; }
        pending = pending.slice(boundary);
        bearer = false;
      }
      const lower = pending.toLowerCase();
      const possibleSecret = forms.some(secret => secret.startsWith(pending) && secret.length > pending.length);
      const possibleBearer = 'bearer'.startsWith(lower) || /^bearer\s*$/i.test(pending);
      if (possibleSecret || possibleBearer) {
        if (!final) break;
        const complete = forms.find(secret => pending.startsWith(secret));
        if (complete) { output += '[REDACTED]'; pending = pending.slice(complete.length); continue; }
        output += possibleSecret && interrupted ? '[REDACTED]' : pending;
        pending = '';
        break;
      }
      const match = forms.find(secret => pending.startsWith(secret));
      if (match) { output += '[REDACTED]'; pending = pending.slice(match.length); continue; }
      const bearerPrefix = /^Bearer\s+/i.exec(pending);
      if (bearerPrefix) {
        output += 'Bearer [REDACTED]';
        pending = pending.slice(bearerPrefix[0].length);
        bearer = true;
        continue;
      }
      // JSON providers may split a UTF-16 surrogate pair between deltas.
      if (!final && pending.length === 1 && /^[\uD800-\uDBFF]$/.test(pending)) break;
      output += pending[0];
      pending = pending.slice(1);
    }
    return output;
  };
  return {
    write(value) { pending += String(value); return consume(false); },
    finish({ interrupted = false } = {}) { const tail = consume(true, interrupted); bearer = false; return tail; },
  };
}

export function createTerminalRenderer({ redactor, record, write = text => process.stdout.write(text), stream = true, reasoning = 'auto' }) {
  const states = new Map();
  let current, completedMessage = false;
  const endLine = () => { if (current) write('\n\n'); current = undefined; };
  const print = text => { endLine(); write(redactor.redact(text) + '\n\n'); };
  const compact = value => { const text = JSON.stringify(redactor.value(value)); return text.length > 3500 ? text.slice(0, 3500) + '…（完整结果见日志）' : text; };
  const keyOf = e => `${e.kind.split('.')[0]}:${e.messageId ?? e.turnId ?? ''}`;
  function stateFor(e) {
    const key = keyOf(e);
    if (!states.has(key)) states.set(key, { key, family: e.kind.split('.')[0], filter: redactor.stream(), displayed: false, last: e });
    const state = states.get(key);
    state.last = e;
    return state;
  }
  function display(state, text) {
    if (!stream || !text) return;
    if (current !== state.key) { endLine(); write(state.family === 'reasoning' ? '推理> ' : '助手> '); current = state.key; }
    write(text);
    state.displayed = true;
  }
  function recordEvent(e) { record(e.kind, { ...e.content, ...(e.turnId ? { turnId: e.turnId } : {}), ...(e.messageId ? { messageId: e.messageId } : {}) }); }
  function finishState(state, interrupted = false) {
    const tail = state.filter.finish({ interrupted });
    if (tail) { display(state, tail); recordEvent({ ...state.last, kind: `${state.family}.delta`, content: { delta: tail } }); }
  }
  function finish({ interrupted = true } = {}) {
    for (const state of states.values()) finishState(state, interrupted);
    states.clear();
    endLine();
  }
  function onEvent(e) {
    if (e.kind.startsWith('reasoning.') && reasoning === 'off') return;
    if (e.kind === 'turn.started') { finish(); completedMessage = false; }
    if (e.kind === 'message.delta' || e.kind === 'reasoning.delta') {
      const state = stateFor(e);
      const safe = state.filter.write(e.content.delta ?? '');
      display(state, safe);
      if (safe) recordEvent({ ...e, content: { ...e.content, delta: safe } });
      return;
    }
    if (e.kind === 'message.completed' || e.kind === 'reasoning.completed') {
      const state = states.get(keyOf(e));
      if (state) finishState(state, Boolean(e.content.interrupted));
      if (e.content.interrupted) e = { ...e, content: { ...e.content, text: redactor.redact(e.content.text ?? '', true) } };
      endLine();
      if (!stream || !state?.displayed) {
        const text = e.content.text ?? '';
        if (text) print(`${e.kind === 'reasoning.completed' ? '推理' : '助手'}> ${text}`);
      }
      if (e.kind === 'message.completed' && (e.content.text || state?.displayed)) completedMessage = true;
      states.delete(keyOf(e));
      recordEvent(e);
      return;
    }
    if (e.kind === 'turn.completed' || e.kind === 'turn.failed' || e.kind === 'turn.cancelled') finish({ interrupted: e.kind !== 'turn.completed' });
    recordEvent(e);
    if (e.kind === 'tool.started') print(`[调用] ${e.content.name} ${compact(e.content.arguments)}`);
    else if (e.kind === 'tool.result') print(`[${e.content.result.isError ? '工具错误' : '返回'}] ${e.content.name} ${compact(e.content.result)}`);
    else if (e.kind === 'turn.completed' && !completedMessage && e.content.answer) print(`助手> ${e.content.answer}`);
    else if (e.kind === 'turn.failed' || e.kind === 'turn.cancelled') {
      print(`[${e.kind === 'turn.failed' ? '失败' : '取消'}] ${e.content.message}${e.content.message === 'harness_model_http_400' ? '（模型服务拒绝请求，请检查地址、协议和模型参数）' : ''}`);
      if (e.content.rejected) print(compact(e.content.rejected));
    } else if (e.kind === 'warning' || e.kind === 'model.retry') print(`[模型提示] ${compact(e.content)}`);
  }
  return { onEvent, finish, print };
}
