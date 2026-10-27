"""Shared public tool registry for the standalone CLI and standard MCP service."""
from adapters import ToolExecutionError
from business import (MCInputError, apply_repair, diagnose_log, get_case, list_cases, preview_repair,
                      read_repair_plan, search_knowledge)
from openmc_tools import (create_model, environment, export_report, read_results,
                          submit_job, validate_inputs)
from openmc_jobs import cancel as cancel_job, status as job_status
from openmc_scan import list_parameters, prepare_case, submit_scan, status as scan_status, cancel as cancel_scan
from openmc_plots import plot_results, plot_scan
from openmc_reports import export_scan
from openmc_artifacts import list_artifacts


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
TOOLS += [
    {"name": "openmc_environment", "description": "检查OpenMC运行时、API与可执行程序版本；安装成功不代表专业计算验收。",
     "inputSchema": schema({}), "outputSchema": OUTPUT, "function": environment},
    {"name": "openmc_create_model", "description": "用公开API创建一个新算例目录。synthetic-mgxs使用合成截面做工程联调；pwr-pin-cell为官方示例，需核数据。不会覆盖已有目录，不是微调模型生成。",
     "inputSchema": schema({"case_dir": TEXT, "template": {"type": "string", "enum": ["synthetic-mgxs", "pwr-pin-cell"]},
                            "particles": {"type": "integer", "minimum": 1}, "batches": {"type": "integer", "minimum": 2},
                            "inactive": {"type": "integer", "minimum": 0}}, ["case_dir"]),
     "outputSchema": OUTPUT, "function": create_model,
     "annotations": {"readOnlyHint": False, "destructiveHint": False}},
    {"name": "openmc_validate_inputs", "description": "检查输入XML结构及基础参数，可选公开API模型解析。结构/API通过不代表几何、核数据或物理正确。",
     "inputSchema": schema({"case_dir": TEXT, "api_check": {"type": "boolean"}}, ["case_dir"]),
     "outputSchema": OUTPUT, "function": validate_inputs},
    {"name": "openmc_submit_job", "description": "快照输入后提交本地OpenMC计算/绘图/几何调试，立即返回作业编号；随后查询终态与产物。Linux/WSL运行，不代表提交即完成。",
     "inputSchema": schema({"case_dir": TEXT, "mode": {"type": "string", "enum": ["run", "plot", "geometry-debug"]},
                            "threads": {"type": "integer", "minimum": 1, "maximum": 64}}, ["case_dir"]),
     "outputSchema": OUTPUT, "function": submit_job,
     "annotations": {"readOnlyHint": False, "destructiveHint": False}},
    {"name": "openmc_job_status", "description": "按作业编号查询真实终态、退出码、日志尾部与产物；服务重启后仍可查询，未知终态不冒充成功。",
     "inputSchema": schema({"job_id": TEXT}, ["job_id"]), "outputSchema": OUTPUT, "function": job_status},
    {"name": "openmc_cancel_job", "description": "请求取消本工具管理的作业，查询到cancelled才表示取消完成；重复取消不会重新执行。",
     "inputSchema": schema({"job_id": TEXT}, ["job_id"]), "outputSchema": OUTPUT, "function": cancel_job,
     "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True}},
    {"name": "openmc_read_results", "description": "通过官方StatePoint读取已成功计算的keff及tally均值/标准差；保留计分维度、数据来源和截断说明，不进行专业验收。",
     "inputSchema": schema({"job_id": TEXT, "max_values": {"type": "integer", "minimum": 1, "maximum": 10000}}, ["job_id"]),
     "outputSchema": OUTPUT, "function": read_results},
    {"name": "openmc_export_report", "description": "导出已完成计算的Markdown/CSV/PDF/DOCX或真实Word97 DOC报告，保留来源及不可用统计量提示。PDF/DOC需初始配置LibreOffice。",
     "inputSchema": schema({"job_id": TEXT, "format": {"type": "string", "enum": ["markdown", "csv", "pdf", "docx", "doc"]}}, ["job_id"]),
     "outputSchema": OUTPUT, "function": export_report,
     "annotations": {"readOnlyHint": False, "destructiveHint": False}},
]
NUMBERS = {"type": "object", "additionalProperties": {"type": "number"}}
SELECTION = {"type": "array", "items": schema({"type": TEXT, "bins": {"type": "array", "minItems": 1,
             "items": {"anyOf": [{"type": "number"}, {"type": "array", "items": {"type": "number"}}]}}}, ['type', 'bins'])}
PLOT_FIELDS = {"tally_id": {"type": "integer", "minimum": 1}, "score": TEXT, "nuclide": TEXT,
               "filter_selection": SELECTION, "format": {"type": "string", "enum": ["png", "svg"]},
               "title": {"type": "string", "maxLength": 200}}
WRITE = {"readOnlyHint": False, "destructiveHint": False}
TOOLS += [
    {"name": "openmc_list_artifacts", "description": "列出计算或扫描已经生成的服务端文件、大小、SHA256和位置。路径属于服务端，本轮不提供远程下载；不暴露本机配置或任意目录。",
     "inputSchema": schema({"kind": {"type": "string", "enum": ['job', 'scan']}, "identifier": TEXT}, ['kind', 'identifier']),
     "outputSchema": OUTPUT, "function": list_artifacts},
    {"name": "openmc_list_parameters", "description": "列出当前模型可调整的运行设置、CSG表面系数、材料密度/温度和源强，带对象ID、当前值、单位与范围；后续扫描请使用这些参数名。",
     "inputSchema": schema({"case_dir": TEXT}, ['case_dir']), "outputSchema": OUTPUT, "function": list_parameters},
    {"name": "openmc_prepare_case", "description": "从原模型复制一个新算例并按已发现的公开属性调整参数。保留密度单位，不累积修改或覆盖原模型，不作专业正确性保证。",
     "inputSchema": schema({"case_dir": TEXT, "target_dir": TEXT, "parameters": {**NUMBERS, "minProperties": 1}}, ['case_dir', 'target_dir', 'parameters']),
     "outputSchema": OUTPUT, "function": prepare_case, "annotations": WRITE},
    {"name": "openmc_submit_scan", "description": "按axes的笛卡尔组合串行计算；先检查全部参数组合，再返回scan_id。复用计算作业，可查询和取消；默认记录失败并继续，禁止把统计设置比较当物理响应验证。",
     "inputSchema": schema({"case_dir": TEXT, "axes": {"type": "object", "minProperties": 1, "additionalProperties": {"type": "array", "minItems": 1, "items": {"type": "number"}}},
                            "threads": {"type": "integer", "minimum": 1, "maximum": 64}, "stop_on_error": {"type": "boolean"}}, ['case_dir', 'axes']),
     "outputSchema": OUTPUT, "function": submit_scan, "annotations": WRITE},
    {"name": "openmc_scan_status", "description": "查询计算参数批次的逐组合状态、作业编号、结果/失败与计数。completed仅表示每个组合完成；partial/failed/cancelled保留，不自动重新提交。",
     "inputSchema": schema({"scan_id": TEXT}, ['scan_id']), "outputSchema": OUTPUT, "function": scan_status},
    {"name": "openmc_cancel_scan", "description": "取消当前扫描计算并阻止后续组合提交；查询终态确认取消完成。",
     "inputSchema": schema({"scan_id": TEXT}, ['scan_id']), "outputSchema": OUTPUT, "function": cancel_scan,
     "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True}},
    {"name": "openmc_plot_results", "description": "从完整StatePoint按tally/score/nuclide/filter选择数据，生成PNG或SVG及对应数据JSON/CSV；默认选首个tally/score，不消费预览截断片段，不做功率归一化。",
     "inputSchema": schema({"job_id": TEXT, **PLOT_FIELDS, "style": {"type": "string", "enum": ['line', 'bar']}}, ['job_id']),
     "outputSchema": OUTPUT, "function": plot_results, "annotations": WRITE},
    {"name": "openmc_plot_scan", "description": "按x_parameter绘制扫描曲线，metric选keff或tally。多维扫描须用fixed固定其它维度；tally可选计分与bin_index。失败/缺失保留空缺，不填0或连过缺口。",
     "inputSchema": schema({"scan_id": TEXT, "x_parameter": TEXT, "metric": {"type": "string", "enum": ['keff', 'tally']},
                            "fixed": NUMBERS, "bin_index": {"type": "integer", "minimum": 0}, **PLOT_FIELDS}, ['scan_id', 'x_parameter']),
     "outputSchema": OUTPUT, "function": plot_scan, "annotations": WRITE},
    {"name": "openmc_export_scan_report", "description": "导出扫描的Markdown/CSV/PDF/DOCX/Word97 DOC报告，逐项保留参数、状态和结果；若已生成PNG扫描曲线则纳入Word/PDF报告。",
     "inputSchema": schema({"scan_id": TEXT, "format": {"type": "string", "enum": ['markdown', 'csv', 'pdf', 'docx', 'doc']}}, ['scan_id']),
     "outputSchema": OUTPUT, "function": export_scan, "annotations": WRITE},
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
