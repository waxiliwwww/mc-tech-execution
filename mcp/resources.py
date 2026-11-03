"""MCP resources, 2026-11-03. Read-only views derived from existing modules and logs.

每个资源都只重组仓库里已有的事实：参数定义取自 openmc_parameters，例题清单取自
official_catalog，调用记录取自 logs/*.jsonl。这里不生成、不推断、不缓存业务结论。
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"

RESOURCES = [
    {"uri": "mc://parameters/settings",
     "name": "可调整运行参数",
     "description": "运行设置与面类型的可调字段：名称、类型、下界、单位。取自 openmc_parameters，不是文档抄写；具体模型读出多少条以 openmc_list_parameters 为准。",
     "mimeType": "application/json"},
    {"uri": "mc://examples/catalog",
     "name": "官方例题清单",
     "description": "固定版本官方例题的分组与状态统计，含暂缓条目。取自 config/official-examples.json，逐项准备用 openmc_prepare_official_example。",
     "mimeType": "application/json"},
    {"uri": "mc://logs/tool-calls",
     "name": "最近工具调用记录",
     "description": "最近 50 条工具调用与失败事件（时间、事件、工具名、是否成功）。取自 logs/*.jsonl；只给调用参数名，不给参数值。",
     "mimeType": "application/json"},
]


def _settings() -> dict:
    from openmc_parameters import SETTINGS, SURFACES
    return {
        "settings": [{"name": k, "type": v[0], "minimum": v[1], "unit": v[2]} for k, v in SETTINGS.items()],
        "surface_types": [{"type": k, "fields": list(v)} for k, v in SURFACES.items()],
        "note": "密度按材料自带 units 解释；材料密度与温度的单位来自模型本身。几何长度单位由模型给出。",
    }


def _catalog() -> dict:
    from official_catalog import load_catalog
    data = load_catalog()
    items = data.get("examples") if isinstance(data, dict) else data
    groups, workflows, deferred = {}, {}, []
    for item in items:
        groups[item.get("group") or item.get("source_group") or "unknown"] = \
            groups.get(item.get("group") or item.get("source_group") or "unknown", 0) + 1
        wf = item.get("workflow", "unknown")
        workflows[wf] = workflows.get(wf, 0) + 1
        if str(item.get("scope", "")).startswith("defer"):
            deferred.append({"example_id": item.get("example_id") or item.get("id"),
                             "reason": item.get("scope")})
    return {"total": len(items), "by_group": groups, "by_workflow": workflows,
            "deferred": deferred,
            "items": [{"example_id": item.get("example_id") or item.get("id"),
                       "group": item.get("group") or item.get("source_group"),
                       "workflow": item.get("workflow"),
                       "scope": item.get("scope")} for item in items]}


def _tool_calls(limit: int = 50) -> dict:
    events = []
    for path in sorted(LOG_DIR.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)[-6:]:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("event") not in ("tool.executed", "tool.failed"):
                continue
            events.append({"time": rec.get("time"), "event": rec.get("event"),
                           "tool": rec.get("name"), "ok": rec.get("event") == "tool.executed",
                           "argument_keys": sorted((rec.get("arguments") or {}).keys())})
    return {"count": len(events), "returned": min(limit, len(events)),
            "note": "只含调用参数名，不含参数值与结果内容。", "events": events[-limit:]}


def read(uri: str) -> str:
    table = {"mc://parameters/settings": _settings,
             "mc://examples/catalog": _catalog,
             "mc://logs/tool-calls": _tool_calls}
    factory = table.get(str(uri))
    if factory is None:
        raise ValueError(f"不支持的资源：{uri}；先读取 resources/list")
    return json.dumps(factory(), ensure_ascii=False, indent=2)