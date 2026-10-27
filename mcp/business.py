"""MC integration helpers. Domain rules and reference cases remain supplied by specialists."""
from __future__ import annotations

import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import uuid

ROOT = Path(__file__).resolve().parent.parent
WORKSPACE = Path(os.environ.get("MC_WORKSPACE", str(ROOT / "workspace"))).resolve()
_repair_lock = threading.Lock()


class MCInputError(ValueError):
    """Reviewed messages that are safe and useful to return to the caller."""



def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _path(relative: str) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise MCInputError("path 必须是工作目录内的相对路径")
    candidate = WORKSPACE / relative
    if any(part in {"..", ".mc-plans", ".mc-backups"} for part in Path(relative).parts):
        raise MCInputError("不允许访问上级目录或内部修复记录")
    current = WORKSPACE
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            raise MCInputError("本轮修复不接受符号链接路径")
    resolved = candidate.resolve()
    if not resolved.is_relative_to(WORKSPACE) or not resolved.is_file():
        raise MCInputError("文件不存在或超出工作目录")
    return resolved


def _read_text(path: Path) -> str:
    if path.stat().st_size > 2 * 1024 * 1024:
        raise MCInputError("文本超过 2 MiB；请先提取有关片段")
    return path.read_text(encoding="utf-8")


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".pending-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(value, out, ensure_ascii=False, indent=2)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def preview_repair(path: str, old_text: str, new_text: str) -> dict:
    """Prepare an exact, single-occurrence text change without changing the input file."""
    target = _path(path)
    before = target.read_bytes()
    if len(before) > 2 * 1024 * 1024:
        raise MCInputError("文件超过 2 MiB")
    text = before.decode("utf-8")
    if not old_text or text.count(old_text) != 1:
        raise MCInputError("old_text 必须在文件中恰好出现一次；请缩小或补全匹配片段")
    if old_text == new_text:
        raise MCInputError("修改前后内容相同")
    after_text = text.replace(old_text, new_text, 1)
    after = after_text.encode("utf-8")
    if len(after) > 2 * 1024 * 1024:
        raise MCInputError("修改后的文件超过 2 MiB")
    plan_id = uuid.uuid4().hex
    diff = "".join(difflib.unified_diff(text.splitlines(True), after_text.splitlines(True),
                                     fromfile=path, tofile=path + " (proposed)"))
    plan = {"plan_id": plan_id, "path": path, "old_text": old_text, "new_text": new_text,
            "before_sha256": _sha(before), "after_sha256": _sha(after), "diff": diff,
            "state": "proposed", "validation": "仅精确文本替换；未进行蒙卡专业校核"}
    _write_json(WORKSPACE / ".mc-plans" / (plan_id + ".json"), plan)
    return {key: value for key, value in plan.items() if key not in {"old_text", "new_text"}}


def read_repair_plan(plan_id: str) -> dict:
    if not isinstance(plan_id, str) or not re.fullmatch(r"[a-f0-9]{32}", plan_id):
        raise MCInputError("无效的修复计划编号")
    path = WORKSPACE / ".mc-plans" / (plan_id + ".json")
    if not path.is_file() or path.is_symlink():
        raise MCInputError("修复计划不存在")
    plan = json.loads(_read_text(path))
    target = _path(plan["path"])
    plan["file_unchanged"] = _sha(target.read_bytes()) == plan["before_sha256"]
    return {key: value for key, value in plan.items() if key not in {"old_text", "new_text"}}


def apply_repair(plan_id: str) -> dict:
    """Host must obtain human confirmation for this call; there is no model-controlled yes flag."""
    with _repair_lock:
        public = read_repair_plan(plan_id)
        if public["state"] != "proposed":
            raise MCInputError("计划已经执行；不会重复写入")
        if not public["file_unchanged"]:
            raise MCInputError("文件在预览后发生变化；请重新生成修复计划并确认")
        plan_path = WORKSPACE / ".mc-plans" / (plan_id + ".json")
        plan = json.loads(_read_text(plan_path))
        target = _path(plan["path"])
        original = target.read_bytes()
        if _sha(original) != plan["before_sha256"]:
            raise MCInputError("文件已改变")
        replacement = original.decode("utf-8").replace(plan["old_text"], plan["new_text"], 1).encode("utf-8")
        if _sha(replacement) != plan["after_sha256"]:
            raise MCInputError("计划内容不一致")
        backup = WORKSPACE / ".mc-backups" / (plan_id + ".original")
        backup.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with backup.open("xb") as out:
            out.write(original)
        backup.chmod(0o600)
        fd, temporary = tempfile.mkstemp(dir=target.parent, prefix=".mc-repair-")
        try:
            with os.fdopen(fd, "wb") as out:
                out.write(replacement)
                out.flush()
                os.fsync(out.fileno())
            os.chmod(temporary, target.stat().st_mode & 0o777)
            if target.read_bytes() != original:
                raise MCInputError("写入前文件再次改变；修改已取消")
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        plan["state"] = "applied"
        _write_json(plan_path, plan)
        return {"plan_id": plan_id, "path": plan["path"], "state": "applied",
                "before_sha256": plan["before_sha256"], "after_sha256": _sha(target.read_bytes()),
                "backup": str(backup.relative_to(WORKSPACE)),
                "validation": "文件修改已完成；蒙卡输入校核和重算仍需业务校核工具"}


def diagnose_log(path: str) -> dict:
    text = _read_text(_path(path))
    rules = json.loads((ROOT / "config" / "error-rules.json").read_text(encoding="utf-8"))
    findings = []
    for number, line in enumerate(text.splitlines(), 1):
        for rule in rules:
            if re.search(rule["pattern"], line, re.IGNORECASE):
                findings.append({"line": number, "excerpt": line[:1000], "category": rule["category"],
                                 "suggestion": rule["suggestion"], "rule_status": rule["status"]})
        if len(findings) >= 50:
            break
    return {"path": path, "findings": findings[:50], "diagnosis_status": "rule_hints_only",
            "note": "仅按文本规则提示；空结果不代表输入正确。规则尚待业务人员用真实日志审定。"}


def search_knowledge(query: str, limit: int = 5) -> dict:
    """Small transparent local text search; no invented physics knowledge or vector service."""
    if not query.strip() or len(query) > 500 or not 1 <= limit <= 10:
        raise MCInputError("query须为1..500字符，limit须为1..10")
    terms = list(dict.fromkeys(re.findall(r"[\w-]+", query.lower())))
    hits = []
    directory = ROOT / "knowledge"
    for path in sorted(directory.rglob("*")):
        if path.is_symlink() or not path.is_file() or path.suffix.lower() not in {".md", ".txt"}:
            continue
        for number, paragraph in enumerate(_read_text(path).splitlines(), 1):
            score = sum(term in paragraph.lower() for term in terms)
            if score:
                hits.append({"source": str(path.relative_to(directory)), "line": number,
                             "text": paragraph[:2000], "score": score})
    hits.sort(key=lambda h: (-h["score"], h["source"], h["line"]))
    return {"query": query, "matches": hits[:limit],
            "note": "返回原文片段和出处；没有命中时不得编造参数说明。PDF/DOCX需先转换成经核对的文本。"}


def list_cases() -> dict:
    cases = json.loads((ROOT / "config" / "cases.json").read_text(encoding="utf-8"))
    return {"cases": cases, "note": "目录不等于算例可运行；pending_material的条目不能用于性能验收"}


def get_case(case_id: str) -> dict:
    for case in list_cases()["cases"]:
        if case["id"] == case_id:
            return {"case": case, "runnable": False,
                    "next_step": "由业务方提供授权算例、依赖、运行脚本和参考结果，再接入执行工具"}
    raise MCInputError("未找到算例；可先调用list_cases")
