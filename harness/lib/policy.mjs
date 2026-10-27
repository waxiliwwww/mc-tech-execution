import { createHash } from 'node:crypto';

const digest = value => createHash('sha256').update(JSON.stringify(value)).digest('hex');
const cancelled = (signal, error) => signal?.aborted || error?.name === 'AbortError';

// Run only the current model sample on the backup. The core owns the turn and
// carries already-executed tool receipts; this adapter never restarts a turn.
export function createFailoverSampler({ primary, fallback, primaryName = 'primary', fallbackName = 'fallback', record = () => {} }) {
  return async function* sample(request, signal) {
    signal?.throwIfAborted();
    let committed = false;
    const pending = [];
    try {
      for await (const chunk of primary(request, signal)) {
        signal?.throwIfAborted();
        const meaningful = (['text_delta', 'reasoning_delta'].includes(chunk.type) && Boolean(chunk.delta))
          || chunk.type === 'tool_call_delta'
          || ['completed', 'response_completed', 'item_completed'].includes(chunk.type);
        if (!committed && !meaningful) { pending.push(chunk); continue; }
        committed = true;
        while (pending.length) yield pending.shift();
        yield chunk;
      }
      while (pending.length) yield pending.shift();
    } catch (error) {
      if (cancelled(signal, error)) throw signal?.reason ?? error;
      if (committed) {
        record('model.failover_blocked', { stepId: request.stepId, model: primaryName, reason: 'partial_output', error: error.message });
        // A stable, non-transient code also prevents the core retry policy from
        // replaying a partly emitted tool call or mixing two providers' output.
        throw new Error('mc_model_partial_output_no_replay');
      }
      if (!fallback) throw error;
      record('model.failover', { stepId: request.stepId, from: primaryName, to: fallbackName, reason: error.message });
      signal?.throwIfAborted();
      let backupCommitted = false;
      try {
        for await (const chunk of fallback(request, signal)) {
          signal?.throwIfAborted();
          backupCommitted = true;
          yield chunk;
        }
      } catch (backupError) {
        if (cancelled(signal, backupError)) throw signal?.reason ?? backupError;
        record('model.fallback_failed', { stepId: request.stepId, model: fallbackName, partialOutput: backupCommitted, error: backupError.message });
        throw new Error(backupCommitted ? 'mc_model_partial_output_no_replay' : 'mc_all_configured_models_failed');
      }
    }
  };
}

export function readPreview(result) {
  if (result?.isError) throw new Error('预览工具返回错误');
  let value = result?.structuredContent;
  if (!value) {
    const text = result?.content?.filter(part => part.type === 'text').map(part => part.text).join('\n');
    try { value = JSON.parse(text); } catch { throw new Error('预览必须返回 JSON 对象及 diff'); }
  }
  if (!value || typeof value !== 'object' || typeof value.diff !== 'string' || !value.diff.trim()) {
    throw new Error('预览缺少可展示的 diff，拒绝修改');
  }
  return value;
}

function denied(reason) {
  return { isError: true, executionState: 'not_started', content: [{ type: 'text', text: `未执行修改：${reason}` }] };
}

// Only trusted terminal input authorizes execution. A model-produced yes,
// confirmed:true, or direct /call follows the same callback and cannot bypass it.
export function createConfirmedCaller({ callTool, tools, writeTools, previewTools = {}, confirm, interactive, record = () => {}, validateInput }) {
  const protectedTools = new Set(writeTools);
  const definitions = new Map(tools.map(tool => [tool.name, tool]));
  const previews = { ...previewTools };
  const needsPreview = new Set(['mc__apply_repair']);
  for (const tool of tools) if (tool.originalName === 'apply_repair') {
    protectedTools.add(tool.name);
    needsPreview.add(tool.name);
    const preview = tools.find(candidate => candidate.serverName === tool.serverName && candidate.originalName === 'read_repair_plan');
    if (preview) previews[tool.name] = preview.name;
  }
  return async (name, input, context = {}) => {
    const definition = definitions.get(name);
    if (!definition) throw new Error(`工具未注册：${name}`);
    const validated = validateInput(definition.inputSchema, input);
    if (!validated.valid) throw new Error(`工具参数不符合 schema：${name}`);
    const args = structuredClone(validated.value);
    if (!protectedTools.has(name)) return callTool(name, args, context);
    const argumentDigest = digest({ name, args });
    context.signal?.throwIfAborted();
    if (!interactive) {
      record('confirmation.denied', { name, argumentDigest, reason: 'non_interactive' });
      return denied('非交互输入不能代替人工确认');
    }
    let preview;
    const previewName = previews[name];
    try {
      if (!previewName && needsPreview.has(name)) throw new Error('修复工具缺少可信预览入口');
      if (previewName) {
        if (!definitions.has(previewName) || protectedTools.has(previewName)) throw new Error('预览工具不可用或被配置为写入工具');
        preview = readPreview(await callTool(previewName, structuredClone(args), context));
        if (args.plan_id !== undefined && preview.plan_id !== args.plan_id) throw new Error('预览计划与申请执行的 plan_id 不一致');
      }
    } catch (error) {
      if (cancelled(context.signal, error)) throw context.signal?.reason ?? error;
      record('confirmation.denied', { name, argumentDigest, reason: 'preview_unavailable', error: error.message });
      return denied(error.message);
    }
    const previewDigest = preview ? digest(preview) : undefined;
    record('confirmation.requested', { name, arguments: args, argumentDigest, previewDigest });
    const decision = await confirm({ name, arguments: structuredClone(args), argumentDigest, preview: structuredClone(preview), signal: context.signal });
    context.signal?.throwIfAborted();
    if (decision !== true) {
      record('confirmation.denied', { name, argumentDigest, reason: 'user_rejected' });
      return denied('用户拒绝本次修改');
    }
    // Bind approval to the shown arguments and preview, including any edits made
    // by another process while the user was reading the terminal prompt.
    if (digest({ name, args }) !== argumentDigest) throw new Error('确认后的参数发生变化');
    if (previewName) {
      try {
        const current = readPreview(await callTool(previewName, structuredClone(args), context));
        if (digest(current) !== previewDigest) throw new Error('确认期间修复计划发生变化，请重新预览');
      } catch (error) {
        if (cancelled(context.signal, error)) throw context.signal?.reason ?? error;
        record('confirmation.denied', { name, argumentDigest, reason: 'preview_changed', error: error.message });
        return denied(error.message);
      }
    }
    context.signal?.throwIfAborted();
    record('confirmation.approved', { name, arguments: args, argumentDigest, previewDigest });
    return callTool(name, args, context);
  };
}
