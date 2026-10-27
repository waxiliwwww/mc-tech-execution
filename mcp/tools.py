"""Only this registry and business.py change when connecting MC functionality."""
from adapters import ToolExecutionError
from business import (MCInputError, apply_repair, diagnose_log, get_case, list_cases, preview_repair,
                      read_repair_plan, search_knowledge)


def schema(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


TEXT = {"type": "string", "minLength": 1}
OUTPUT = {"type": "object"}
TOOLS = [
    {"name": "diagnose_log", "description": "读取工作目录内的日志并返回带行号的规则提示；不是专业正确性证明。",
     "inputSchema": schema({"path": TEXT}, ["path"]), "outputSchema": OUTPUT, "function": diagnose_log},
    {"name": "preview_repair", "description": "生成单处精确文本修复计划与差异，不修改业务输入。随后必须让用户查看差异并确认。",
     "inputSchema": schema({"path": TEXT, "old_text": TEXT, "new_text": {"type": "string"}}, ["path", "old_text", "new_text"]),
     "outputSchema": OUTPUT, "function": preview_repair},
    {"name": "read_repair_plan", "description": "读取修复计划差异、文件哈希和当前状态，供用户确认。",
     "inputSchema": schema({"plan_id": TEXT}, ["plan_id"]), "outputSchema": OUTPUT, "function": read_repair_plan},
    {"name": "apply_repair", "description": "修改输入文件。只可由已取得用户明确确认的宿主调用；检查原文件哈希，备份后写入。",
     "inputSchema": schema({"plan_id": TEXT}, ["plan_id"]), "outputSchema": OUTPUT, "function": apply_repair,
     "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False}},
    {"name": "search_knowledge", "description": "查询本地TXT/Markdown资料，返回原文、文件名和行号；无命中时不要编造。",
     "inputSchema": schema({"query": TEXT, "limit": {"type": "integer", "minimum": 1, "maximum": 10}}, ["query"]),
     "outputSchema": OUTPUT, "function": search_knowledge},
    {"name": "list_cases", "description": "列出基准例题目录和缺失材料；目录存在不代表已能运行。",
     "inputSchema": schema({}), "outputSchema": OUTPUT, "function": list_cases},
    {"name": "get_case", "description": "读取指定基准算例（包括CLEAR-S）的接入状态和待交材料；当前不执行蒙卡计算。",
     "inputSchema": schema({"case_id": TEXT}, ["case_id"]), "outputSchema": OUTPUT, "function": get_case},
]
for tool in TOOLS:
    tool.setdefault("annotations", {"readOnlyHint": tool["name"] != "preview_repair", "destructiveHint": False})


def safe_business(function):
    def call(**arguments):
        try:
            return function(**arguments)
        except MCInputError as error:
            raise ToolExecutionError(str(error)) from error
    return call


for tool in TOOLS:
    tool["function"] = safe_business(tool["function"])
