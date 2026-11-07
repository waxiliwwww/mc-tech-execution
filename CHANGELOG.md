# 变更记录

## 0.12.0 — 2026-11-07

- 模型输入不再声明或暴露 schema 版本；仓库删除旧格式专用测试、兼容说明、能力限制和发布门禁项，随包示例、schema 和文档统一为当前声明式格式。
- D4：例题步骤的公开 `case_dir` 统一指向用户准备的算例目录，不再混入 `.mc-jobs/<job>/work`；作业工作目录另以 `job_case_dir` 保留。
- D6：绘图和报告输出名改为基于作业、选择和格式的确定性哈希；同一选择重复导出复用同一组图片，不再无限新增文件。
- D7：网格热图增加低统计告警（相对标准差过高或不可计算的分箱计数），并写入 JSON/CSV；热图本身仍不是专业判据。
- N3：报告正文预览优先显示非零分箱并保留原分箱编号，完整值仍在 CSV/JSON；不再让前 20 个零箱占满表格。
- 新增/更新回归测试覆盖上述各项；删除旧格式 MCP 反例和测试说明。

## 0.11.11 — 2026-11-07

- `openmc_job_status` 的摘要 k 不再在 Combined k 为 `-nan` 时取批次表 Average：新增 RESULTS 的 Collision/Track-length/Absorption 估计解析，按 Absorption→Collision→Track-length→Average 回退并标 `keff_source`；新增回归测试构造 `Combined=-nan` 的真实日志。
- `statistical_adequacy` 不再只看 σ：同时检查活跃/非活跃批次数，默认 200×10/inactive 2 会降级为 `low_statistics_preview` 并保留 `downgraded_from`；不拒绝低统计作业，只防把它标成工程结论。
- `openmc_build_model` 的 `files` 改为磁盘上重建所需的全集（XML、`build_model.py`、`model-spec.json`、`model_validation/` 等），并单列 `xml_files`；真实 MCP 用例同步按磁盘核对。
- `openmc_import_model` 不再复制生成的 `views/`；新增导入回归测试。
- 测试体系落到发布门禁：`config/capability-baseline.json` + `tests/test_capability_baseline_contract.py` 记录已支持能力与输入契约的真实收窄；`config/release-critical.txt` + `scripts/check-release-evidence.py` + `./verify.sh --release` 拒绝关键真实用例 skip/缺失。`AGENTS.md` 与 `docs/测试与验收说明.md` 补四层测试、能力基线和发布判据。
- 修正过期测试：删除 `tests/test_manual_real.py`（旧 `compute.html` 的 `data-run` 教学块已随手册整合消失，保留只会测一个兼容跳转）；新增 `tests/test_manual_compute_redirect.py` 守旧章节入口；`test_downstream_real` 的扫描统计断言接受有限值与“不可用”两种真实输出。重新生成 `docs/reference.html`。


## 0.11.10 — 2026-11-07

- 修复k口径：`openmc_read_results` 的 keff 带 `source`（`statepoint:k_combined` / `statepoint:k_generation`），k_combined/std 含非有限值时改用逐批k合并估计并给 `fallback_from`；`openmc_job_status` 的进度标 `keff_source`；`openmc_job_compare` 改为优先读StatePoint（日志Combined行仅作fallback），`keff_combined.source` 与 `within_2sigma_engineering` 标明来源与判据。此前两个都“已完成”的作业会因日志里的 `-nan` 被拒绝比较k。
- `openmc_read_results` 增加 `statepoint_context`（燃耗作业有多个时间步时标出读到第几个并指向 `openmc_read_depletion`）与 `statistical_adequacy`（按k相对σ的工程分级；明确不是收敛检验、不检测源收敛与欠采样）。
- tally逐bin对比把“一侧均值0且σ=0”的零计数bin单列 `zero_count_bins`，排除在2σ判决与 `bins_compared` 之外；warnings 按 `keff:`/`tally:` 标明作用域，不再把keff的非有限值混成tally级警告。
- 新增 `config/score-names.json`（由 `openmc.data.REACTION_MT` 生成，473个合法score）与 `mcp/openmc_scores.py`：`openmc_define_tally` 在写盘前拒绝未知score并给最接近候选；`openmc_validate_inputs(semantic_check=true)` 把 tallies.xml 里的未知score记为 `invalid_score`。此前 `fluxx` 能通过全部MCP侧检查，直到输运阶段才以 `Unknown reaction name` 终止。
- 几何检查把算例引用的相对数据文件（`<cross_sections>` 元素、`library`/`filename` 属性引用的 h5/h5m）一并复制进临时副本；缺文件时返回 `checked=false`+`skipped_reason`+`missing_data_files`，引擎在读数据阶段失败时 `ok=null` 且 `engine_started=false`，不再把“引擎没启动”记成 `ok=false` 的几何结论（此前多群算例必然复现）。
- `openmc_view_geometry` 增加引擎直读原XML的出图回退（`render_method=engine_native_plot_xml`）：多群算例经SDK单文件导出会丢 `<macroscopic>`，引擎随即中止；现在可出图，连续能算例仍走 `python_api_export`。
- hex-lattice 模板修两处：反射六角棱柱按栅格外径（环数×栅距）推导，旧值内切圆半径只有0.75cm，7根棒里6根落在反射边界外；O16 质量份额按UO2化学计量由U质量折算（O/U=2，旧值0.1184对应1.76）；plot宽度随栅格外径。旧模板会把“只算了1根棒”当成7根棒的k且不报错。
- 收敛图与网格热图不再让非有限值进入JSON：k_combined 非有限时 `combined=null` 并写入 `warnings`，逐批k非有限时曲线断开不补0；零均值网格提示改为“x/n个箱均值为0”，全零时另给提示。
- `openmc_plot_scan` 增加 `x_label`/`y_label`（省略时仍为“参数名 [单位]”）。
- `config/error-rules.json` 补 `invalid_score`、`missing_cross_sections`、`source_outside_geometry`、`dagmc_geometry` 四类，供 `openmc_diagnose_job` 命中上述引擎中止。
- 新增6个测试文件（一个场景一个文件，文件头写明功能/类别/前置/输入/预期/不证明）：score名单与拒绝路径、零计数bin判决、k来源与统计分析、几何检查数据依赖、hex模板几何与化学计量、多群几何切片与连续能对照；真实OpenMC用例由 `MC_TEST_OPENMC_CONFIG` 门控，未设置时skip。全套 451 项运行、98 项跳过、无失败。
- 本轮未改：DAGMC固定源、产物远程下载、本机模型上传、`apply_repair` 确认令牌、Notebook结果筛选、参数层暴露栅格几何量；服务进程与磁盘代码漂移只按部署文档提示核对（本部署实测为旧进程加载新代码，非 schema 声明缺陷）。
- 声明式输入统一为类型化 `region`（`halfspace`/`intersection`/`union`/`complement`），不保留字符串表达式兼容层；变更同时更新 `openmc_build_model`、能力发现、随包示例和使用文档。
- 参数校验拆成独立 `model_validation` 包：优先 Pydantic 严格模型，Pydantic 不可导入时自动使用纯标准库 fallback；真实校验失败不会静默改用另一后端。静态 schema 带 `$id`，可独立校验，也可随算例复制。
- 专家覆盖以显式 tag 记录：当前只允许 `outer_boundary_transmission`，必须给出 `reason` 与 `scope`；未触发的覆盖保留 warning，构建结果、`mc-case.json` 和作业 provenance 都携带 `validation` 报告。
- 根宇宙外边界 anti-hang 保留：只被一个根宇宙直接栅元使用的面视为外边界，`boundary` 不得为 `transmission`，须显式 `vacuum`/`reflective`/`white`，或使用上述覆盖。该规则是近似判定，不证明栅元覆盖或无重叠。
- 随算例复制的 `build_model.py` 与 `model_validation/` 增加 `--validate`：无 OpenMC 环境可只校验 `model-spec.json`，默认 `auto` 后端并返回 assumptions、overrides；Pydantic 缺失时仍可用标准库 fallback。
- 新增 4 条每次独立启动真实 MCP 服务的声明式模型用例，按场景拆到 `tests/model_validation_mcp/`，一个用例一个文件：2 条正例、1 条反例、1 条外边界覆盖边界例；文件头写明功能/类别/前置/操作/预期/不证明，均经实际 MCP 协议调用 `openmc_model_capabilities`、`openmc_build_model` 或 `openmc_preview_input`，检查拒绝原因、无残留目标目录、构建产物与覆盖 tag。
- MCP 工具失败时透传经审阅的 `MCInputError` 文本（不泄露原始输出），使参数错误在宿主侧可见、可操作。
- `openmc_model_capabilities` 增加校验后端作用域和构建器后端策略；`openmc_build_model` 分别返回服务端校验后端与构建器实际后端，避免把 OpenMC runtime 的 fallback 冒充服务端 Pydantic。

## 0.11.9 — 2026-11-06

- 声明式模型提交返回 `input_integrity`，报告 `mc-case.json` 指纹与当前 XML/spec 是否一致；不一致只提示、不自动拦截。
- `openmc_build_model` 返回 `assumptions`，列出省略字段实际采用的默认值，避免把默认值当成用户给定。
- 几何检查结果增加 `probabilistic` 与 `does_not_prove_no_overlap` 标志，减少把抽样通过当成无重叠证明。

- 保留included，说明官方目录静态状态、历史验证与甲方本部署实例执行的区别。
- 补已有模型导入的完整操作衔接、随包材料和历史证据入口；补热图实际字段与异常章节的修复练习直达链接。
- 交付前onboarding修复：启动横幅补版本、URL、传输/鉴权；DSH配置移到文档作为宿主示例，不在启动输出写死；quickstart检查并补装Eigen头文件；openmc_example_status与openmc_job_status增加human_summary。修复两个C++自定义源compile失败，复测5/5。
- 新增官方例题MCP复测记录（46完成/2取消/0失败）和onboarding自测；使用手册补“输入参数生成”章节，并改为初学者/熟手双路径：3分钟导览、最小可运行JSON、3×2组件全程例子；熟手可直接展开命令链，字段表和覆盖矩阵收进折叠区；随后重写开头及第2–4节行文，删除自我指涉、口号和重复免责，保留命令、数字、表格与边界。例题页标明只验证执行路径、不验证入参生成。已重新打包并校验。
- MCP标识从`mc`改为`openmc-forge`：服务名、启动横幅、health、DSH serverName、文档与quickstart示例同步；Python包名`mc-agent-tools`保留，避免破坏安装入口。
- 启动横幅精简为服务名/版本、MCP 地址、传输协议、鉴权方式、内置工具数量和事件日志路径；删除55个工具清单、接入说明、下一步和connection路径，需要时查文档。

## 0.11.8 — 2026-11-06

- MCP环境工具新增与握手/health同源、启动时固定的service版本；CLI不宣称在线服务身份。
- 首页给出组件流程12个常用工具与case_dir/job_id/tally_id关系，沿用原教程，不新增固定demo。
- 修正曲线/热图CSV精度说明及报告20点预览与完整数据的核对路径，补核数据环境字段的放行依据。

## 0.11.7 — 2026-11-06

- 重新交付包含定稿手册的源码包，纳入启动/诊断入口、输入指纹字段对应、摘要读取和Windows清理说明；保留0.11.6归档。
- 发行核对区分归档自洽与定稿源码一致性，逐文件核对交付内容；计算与MCP工具实现未改变，不新增专业验收承诺。

## 0.11.6 — 2026-11-05

- 报告元数据不再把模板名冒充生成方式；汇总/所选Markdown与DOCX共享去重显示，保留明确记录的实际生成方式。
- 打包自动生成分版清单、摘要及兼容副本；临时归档先校验，普通发布失败回退旧组合；新增只读归档校验入口。
- 部署/使用说明区分服务Schema与宿主简略签名，补运行代码辅助证据、写响应未知对账及交付物校验规则；不改DSH或模型配置。

## 0.11.5 — 2026-11-05

- 预览清单与普通作业复制范围对齐，包含来源标记及相对资源；大文件流式指纹，二进制不展开正文。
- 两个工作流示例双向比较完整输入清单，拒绝漏项或不完整预览。
- 计分过滤器用简短说明和实际参数替代对象repr；部署说明补DSH参数Schema刷新核对，保留低统计专业判读边界。

## 0.11.4 — 2026-11-05

- 几何查看在临时目录渲染，保护原输入与已有图片；默认采用有限包围盒，无界时要求用户提供视野。
- 网格热图支持明确选择多score计分，并保留网格/能量过滤顺序；报告头按实际数据来源表述。
- 官方结果可配置每tally预览上限，标明总数与返回数；原默认1000值截断保留。
- 默认服务版本从VERSION读取并在启动时固定，wheel使用同源安装元数据；部署/试用记录与修复练习说明同步。

## 0.11.3 — 2026-11-05

- 修复无Entropy列日志的批次进度解析，保留末批、平均k与Combined k区别及非有限值提示。
- 环境新增部署核数据候选路径/来源与env_set，保留原字段兼容；官方清单增加可选分页/摘要。
- 网格零均值加标记/计数/说明，保留数据；报告按已知OpenMC默认补energy_mode并标来源。
- 部署说明补独立终端/systemd用户模板、Windows连接诊断和文件取回；未启用托管或改变现有服务，远程MCP下载仍未实现。

## 0.11.2 — 2026-11-05

- 产物清单补列网格热图、收敛图及CSV/JSON；公开组件/全堆MCP复现脚本保留增量调用记录，校验不完整即停止，未知结果不自动重投。

- 修正集成XML的tally语义检查、材料非有限值和模型显式截面索引优先级；修正粒子过滤bins的接口类型。新增能力范围与官方模型DSH/MCP复现说明。未宣称本轮真实模型或求解验证。

## 0.11.1 — 2026-11-04

- 修复所选结果的DOCX/PDF/DOC报告头未接收运行元数据的问题，与Markdown使用同一份来源、设置、引擎版本、运行时间和随机种子；数值选择与图保持不变。

## 0.11.0 — 2026-11-04

- CAD初配进入quickstart默认流程，支持跳过CAD或仅补CAD，以及选择新CAD目录；固定Python依赖并核验原生包，真实STEP转换探针通过后才发布CAD配置。保留已有不匹配环境，不原地混装。
- DAGMC模型增加明确的真空包围；单文件model.xml可追加计分、保留相对几何引用，并支持网格坐标和报告元数据读取。修复从转换到计算、图报告之间的实际交接问题。
- 新增同一STEP经CLI或标准MCP完成转换、模型组装、计算、16格通量图和同源报告的示例与说明。核对原生结果、输入快照和产物；工程测试不替代专业验收。
- 架构说明的三图与样式内嵌HTML，单独拷贝文件也可显示；保留可编辑SVG源和重生成入口，补单文件离线显示检查。


## 0.10.1 — 2026-11-04

- 两周计划改为精简交付总览，接口和验证细节保留为补充；新增Windows DSH接入页、插件配置示例和无模型MCP发现检查脚本。
- 作业提交前检查实际输入快照估算量和可配置磁盘保留空间，低空间拒绝且不创建作业/启动计算；存储报告增加磁盘余量。没有新增自动删除、后台服务或运行期配额承诺。
- 明确测试后清理可再生成临时内容，保护原输入、核数据、运行时和必要证据。


## 0.10.0 — 2026-11-04

- 增加原生模型导入、受控结构化组合建模与同源能力Schema；不要求例题ID，生成可独立重跑Python源码。材料、CSG/宇宙/二维矩形格架、源及基础tally均可配置。
- 声明式改参同步spec/Python/XML与指纹，预检和实际修改范围一致；副本经修改、读回后原子发布，拒绝覆盖和输入漂移，原生模型兼容路径保留。
- 本地作业/扫描支持用户任务名，按名称与状态检索；重名仍以ID区分，状态可核对预览与输入快照指纹。
- 新增模型工作流示例、两周开发计划和接入/建模手册；真实MCP非目录模型与简化全堆短计算核对原生数据、图和报告。自然语言模型联调按用户决定后续安排；HPC、微调生成和专业验收未完成。
- 工具发现与安装Schema同源检查替代工具数量硬编码；源码包与独立wheel的资产/配置边界分别说明。


## 0.9.0 — 2026-11-03

- 新增 CAD 转换工具（RF002 工具层封装）：`openmc_inspect_step`（STEP 实体数/包围盒/单位提示）与 `openmc_convert_cad`（STEP→DAGMC `dagmc.h5m`，材料标签映射、统一缩放、网格尺寸限、隐式补集标签；产物由 DAGMC 版 OpenMC API 读回包围盒，失败保留错误文本）。转换链工程验证与版本组合见 `docs/adr/0002`（cad_to_dagmc 0.15.1 + cadquery 2.8.0 + gmsh 4.15.2，mesher 后端无需 pymoab）。
- 转换在独立 cadenv 子进程执行：业务层仍不装 openmc/cadquery；结果经结果文件回传（Rust 网格后端可能直写 stdout，不作为 JSON 通道）。部署配置新增可选键 `cad_python_command`/`cad_library_path`/`cad_timeout_seconds`，未配置时工具给出可行动错误。
- `openmc_validate_inputs` 新增语义校验第一刀（`semantic_check`）：连续能材料的核素/S(a,b)名对照生效截面索引（解析顺序与运行时一致：部署配置→环境变量→受管缓存→随包离线索引），返回缺失清单与索引来源；多群/宏观材料如实跳过并说明原因。仅校验名字可用性，不校验温度、光子数据或物理适用性。
- `openmc_worker` 新增 `inspect-h5m` 动作；新增离线协议测试 `tests/test_openmc_cad.py` 与实跑档 `tests/test_openmc_cad_real.py`（`MC_TEST_CAD_CONFIG` 启用），夹具 `tests/fixtures/cube.step` 为 cadquery 生成的 10 mm 立方体。
- 新增 `openmc_preview_input`（提交前看清输入）：逐文件截断内容预览、字节数/SHA256 与结构摘要（运行设置、材料核素、几何与 tally 计数）；只读，算例内符号链接拒绝读取，防越界读工作区外文件。
- `openmc_job_status` 返回新增结构化 `progress` 节：按 solver.log 尾部批次行解析（最新批次/代、k、熵、平均 k±σ、RESULTS 的 Combined k、batches/inactive 总数、percent、elapsed_seconds）；固定源/绘图模式或启动初期解析不到时如实说明原因，不编造百分比，不替代 `openmc_read_results` 统计量。
- 新增 `openmc_job_compare`（两作业对比第一刀）：逐输入文件 SHA256 异同四分类、状态/模式/线程/批数对照、双方 Combined k 与差值及按独立假设合成的 σ（注明仅工程参考，不构成统计显著性判定）；tally 分布对比未包含，如实说明。
- 安全强制规则固化为离线测试（tests/test_server_security.py，11 项）：非回环监听缺 MCP_AUTH_TOKEN 或 publicUrl 拒绝启动；publicUrl 回环/通配/路径不匹配拒绝；token 含空白或控制字符拒绝；连接配置只写 `${MCP_AUTH_TOKEN}` 占位符，不落盘 token 本身；主机名作 MCP_HOST 拒绝。无生产代码变更。
- 语义校验第二刀：`semantic_check` 结果重构为统一 `semantic.problems` 定位清单（kind/severity/location/detail），新增静态检查——材料密度正定与单位合法（引擎 0.16.0 实测拒绝负密度："Need to specify a positive density"）、温度正定、tally filter 类型对照安装版注册表（`_FILTER_TYPES` 33 类源码内省固化）与 bins 规则（整数 id/严格递增/mu∈[-1,1]/polar∈[0,π]）、tally <filters> 整数 id 引用完整性，以及常见误置检测（tally 内单数 <filter> 子元素会被引擎忽略——按安装版 0.16.0 源码 get_elem_list(elem,"filters",int) 实证），`complete` 字段如实标注哪些检查未执行。验收原文 5 类缺陷中，XML 语法错、核素缺失、负密度、filter 非法现已静态报出且定位；几何重叠归几何检查。合法值集合全部取自安装版 0.16.0 源码内省，非凭记忆。
- 几何重叠检查并入 `validate_inputs` 可选档：`geometry_check=true` 在临时副本上以削减批次（≤2 批、inactive 归零、单线程、超时默认 120 秒可配 `geometry_check_timeout_seconds`）运行 `openmc -g`，结构化返回重叠单元明细（cells/universe）、逐单元重叠检查次数汇总、settings 削减记录与日志尾部；概率性结论如实标注（通过不能证明无重叠），引擎缺失/结构未过/超时/非零退出无明细分别如实报告；原算例文件不动，符号链接拒绝。`validation_level` 增加 `+static_semantics`/`+openmc_geometry_debug` 层。离线 stub 引擎测试 + 真实引擎门控测试（重叠算例报 cells 2,1 on universe 0，rc 255；干净算例返回逐单元检查次数）。
- prepare_case 逐参数差异（B2）：返回新增 `parameter_diff`——`changes`（应用时刻前后值，含单位/类别）加 `readback`（写入后再次调用 OpenMC API 重新读取文件、按参数核对 expected/found/match），`verified=true` 表示全部请求参数读回一致；不一致或缺读回项如实列出。把“修改意图”升级为“落盘证据”；顶层 `changes` 字段保留，兼容不变。
- 工作区存储只读报告（H2 第一刀）：新工具 `openmc_storage_report`——内部记录（作业/扫描/例题）逐类计数与字节、状态分布、最大记录 Top N；请求 `older_than_days` 时按与 `cleanup.sh` 完全一致的口径预览可回收量（仅终态记录、引用保护与修复关联如实列出）。实现按文件路径加载并复用 `scripts/cleanup_artifacts.py` 的 inventory/fingerprint/plan，口径单一来源；本工具不删除任何内容，删除仍须维护窗口 `cleanup.sh` 预览后 `--apply`。
- 作业对比第二刀（D2）：`openmc_job_compare` 增加可选 `compare_tallies` 档——经 `read_results` 读取两作业 StatePoint，按 tally id 对齐（shape/scores/nuclides/estimator/filters 类型不一致时如实不对比并列结构差异），逐 bin 给出差值、独立假设合成 σ 与是否在两倍 σ 内（工程判据，不构成统计显著性判定）；非有限 bin 不替换为 0，截断如实标记仅对比前 `max_values` 个 bin，问题 bin 至多列 50 条，`summary` 给全量计数，双方 warnings 合并返回。进度表工程辅助段补 `openmc_preview_input`（B1 文档欠账）与 `openmc_job_compare` 两行。
- A5 后续（CAD 链补最后一步）：新工具 `openmc_build_dagmc_model`——为 `openmc_convert_cad` 产出的 h5m 组装可运行 `model.xml`（材料/设置/源，公开 API 组装后引擎回读验证）；DAGMC 文件名改写为相对 `dagmc.h5m`（与 create_model 改 cross_sections 同款，保证算例可搬移）；材料标签须与转换时 `material_tags` 逐一一致（含隐式补集标签），密度正定、单位限 g/cm3|g/cc|atom/b-cm|atom/cm3，核素组成可缺省但如实告警"输运前须补全"；不覆盖已有 XML、不重做转换。实测：cube.step→h5m→model.xml 全链通过（引擎回读 ok，material_tags=steel）。甲方 STEP 验收（lost particle=0）仍待料。
- D6 第一刀（表4-2 准确率比对 harness）：新增 `scripts/accuracy_baseline.py` + `./accuracy.sh` 入口与参考值清单 `tests/fixtures/accuracy/manifest.json`。流程：生成/复用算例→登记核数据版本（模板/引擎版本/截面 sha256/设置）→比对前校验（结构+引擎解析）→相同设置运行→读取 keff→按容差判定→表格+JSON 报告；退出码 0/1/2 对应 PASS/FAIL/INCONCLUSIVE。参考值两类：解析型从算例自身 MGXS 派生 `k∞=ΣνΣf/ΣΣa`（齐次反射无泄漏，不硬编码），常数型必须给 value+source、缺值判 INCONCLUSIVE 不冒充通过。实测：合成单群解析基准全链 PASS（σ内），C5G7 多群条目如实 INCONCLUSIVE（官方例题不带参考值，待取证/甲方值）。离线判定测试 + 真实门控测试。
- H1 准备件（离线/内网部署）：新增 `scripts/offline_check.py`（可查项自动检查：vendor例题与knowledge官方资料清单sha256、依赖全钉版、网络调用点静态扫描、运行时版本登记）与 `docs/离线部署演练清单.md`（目标环境逐条演练步骤+记录表+回滚）。**如实标注未演练**：本机不允许网络命名空间隔离（unshare: Operation not permitted），断网实跑与离线安装须在目标环境执行。可查项实测全过；静态扫描仅列可疑点（服务端socket监听、维护脚本update_openmc_sources.py按设计需联网），不构成"运行期无网络依赖"的证明。
- D5（表4-1 4.3 报告头元数据）：`openmc_export_report` 报告头带齐五项——模板版本（含生成方式）、参数（run_mode/energy_mode/particles/batches/inactive/seed）、OpenMC版本、运行时间（起止+时长）、随机种子；全部取自作业目录落盘文件（job.json/work/settings.xml/mc-case.json），缺失如实写"未记录"不推测。覆盖 markdown 作业汇总、所选结果报告 markdown、DOCX/PDF/DOC 报告头；返回值新增 `metadata` 字段。离线测试+真实实跑档（downstream 148s、所选报告 48s）通过；过程留痕：所选报告与下游连跑曾出现一次不可复现的13s报错，单独复跑两次均通过，记录为瞬时干扰、未掩盖。
- A3（表4-1 tally 定义）：新工具 `openmc_define_tally`——向已有算例追加自定义 tally（score 列表 + 能量/材料/粒子/规则网格过滤），写 `tallies.xml`（网格内联，引擎回读验证）；不改几何/材料/设置，拒绝与已有 tally_id 冲突；过滤参数严格校验（能量分界递增、材料编号、粒子名、网格维数与包围盒）。实测：合成例题加 MeshFilter+flux tally（2×2×1，覆盖-10..10）→ api_check 通过 → 实跑完成 → read_results 读出形状[2,2,1,1]与分箱值。
- A1（表4-1 1.1 组件模型基础）：create_model 模板库扩容——`assembly-17x17`（OpenMC官方 BEAVRS 反射17×17组件，公开基准几何与材料）、`core-lattice`（OpenMC官方 OECD/NEA 蒙卡性能基准全堆，21×21栅格）、`control-rod`（公开文献常用参数自测：UO2栅元+B4C控制棒，反射盒）、`hex-lattice`（公开参数自测：7根钠冷燃料棒，HexLattice+反射六角棱柱）。marker 登记 lattices（RectLattice/HexLattice）与官方基准 source_sha256；真实实跑档：三模板 `openmc -g` 零重叠、无丢失粒子，17×17 组件短实跑 keff=1.1526±0.0055。**不是甲方组件参数**，"按甲方参数表生成"仍属 RF004 未实现范围。
- D3（表4-1 图型）：新工具 `openmc_plot_mesh`（规则网格 tally 二维分布热图：坐标取自算例 `tallies.xml` 的网格定义，3D 网格必须显式 `level`、带能量过滤必须显式 `energy_bin`，绝不静默投影或补 0；输出 PNG + 完整 JSON/CSV）与 `openmc_plot_convergence`（k 逐批收敛：数据取 statepoint 的 `k_generation`——单批 k，按活跃批累计均值 ± 标准误画带，并标最终合并 k；不平滑、不删跳点）。能谱图由既有 `openmc_plot_results` 覆盖（能量过滤器→对数轴+误差棒），新增 x_label/y_label/colorbar_label 自定义轴标题。真实实跑档：2×2×1 网格 4 格、3D 2×2×2 层切片、6 个活跃批收敛带全部通过；网格 tally 多 score 明确报错而非静默取值。
- C3/C4（表4-1 3.1 运维上限）：单作业日志上限 `max_log_bytes` 10MB→50MB（配置项，`config/openmc.json` 与本地配置同步；响应侧仍截断保护宿主上下文，截断标记不变）；输入文件结构检查上限 10MiB 改为配置项 `max_input_bytes`（缺省 10MiB，实测本仓库最大输入 geometry.xml 386KB，余量充足）；新增按 CPU 核数限并发——提交时统计运行中（含排队）作业占用线程数，超核直接拒绝并提示调小 threads/等结束/先取消，可选 `max_concurrent_jobs` 再限作业个数（默认不限）。真实探针：threads=16 占满后 threads=4 被拒；取消并等到终态后再提交成功。**PPT v14.0 第 5/9 页"10MB"字样未同步（按冻结要求不动）**。
- A2/D1（表4-1 燃耗参数化与燃耗结果提取）：新增 `openmc_configure_depletion`（功率/时间步/单位/链文件写算例）与 `openmc_read_depletion`（逐时间步 k-eff、燃耗估算、关键核素质量），`openmc_submit_job` 新增 mode=deplete（独立燃耗运行器 `mcp/deplete_runner.py`，走同一作业状态机/取消/日志截断），模板 `pincell-depletion`（公开压水堆栅元参数自测，燃料体积显式）。链：`chain_casl_thermal.xml`（CASL 热谱链，981,527 字节，sha256 9c71578f…，取自 openmc-dev/data/depletion，非本项目生成；离线部署须随包分发）。燃耗值口径为功率×时间/初始重金属质量（MWd/kgHM）估算——引擎 0.16 的 deplete.Results 无 get_burnup。真实门控：300 粒/6 批/2 步，HM=97.054g、U235 2.3308g（=2.4%）、Pu239 生成、k≈0.93/1.32/1.30（统计很差，仅证明链路，不作物理结论），测试 45.9s 通过。**另修正三处模板组成标注**：O16 原按重量 2.0 标注（氧占质量 2/3、重金属质量偏小），改为化学计量重量分数 0.1184；混用原子/重量分数被引擎拒绝（`ERROR: Cannot mix atom and weight percents`，实测）。control-rod/hex-lattice 数值受此前错误影响，已重跑 A1 真实门控（27.0s）确认仍通过。
- F1（表4-1 1.3 模型查看）：新增 `openmc_view_geometry`——按算例输入出 2D 切片图（xy/xz/yz、原点/宽度 cm、像素 50..1200、按材料或栅元着色），走引擎官方 `SlicePlot.from_geometry` 渲染，PNG 写入算例 `views/` 并返回路径；返回值带实际采用的 width/origin 与材料/栅元计数。真实门控：synthetic-mgxs 与 pwr-pin-cell 出图（PNG 头校验、>400 字节），人工核对 pin-cell 图（燃料/包壳/水三色）。**已知限制（实测）**：外边界为六角棱柱的模型（hex-lattice 模板）引擎报 `ValueError: The geometry does not appear to be bounded in the xy plane`，即六角外边界目前无法出切片图；矩形/圆柱/组件模型可用。渲染时会临时生成 plots.xml/plot_*.png 并在成功后清理（仅删本次生成的文件）。
- E1（表4-1 5.1 验收判据）：故障样本库入库 17 条——11 条构造坏算例（settings/geometry/materials/tallies 语法、DTD、根元素、粒子/非活跃批范围、缺文件、缺密度、负密度、缺核素、负温度）由 `validate_inputs` 定位到文件与具体问题；5 条真实 OpenMC 0.16.0 引擎报错日志（XML 处理失败、非法 run_mode、核素缺失、缺截面文件、lost particle）由 `config/error-rules.json` 定位到行号。新增 `openmc_list_fault_samples` 与 `openmc_check_fault_sample`（可单条或全量自检，返回逐条命中证据）。**顺带修真实缺口**：引擎把一条消息折成两行（lost particle 的 "could not be located in any" + "cell and it did not leak."），旧规则逐行匹配会漏；`diagnose_job` 与样本自检改为"当前行+下一行"拼接匹配，并给语法/核素/缺截面三条规则补上真实引擎措辞。另补 1 条几何重叠样本（`s17-geometry-overlap`）：两个燃料单元区域重叠时引擎在源抽样阶段报 "Exceeded maximum number of source rejections"，规则库只做症状级识别并写明不定位重叠单元。全量自检 17/17 通过。样本是自检素材，不是甲方故障清单，也不证明专业归因能力。
- 文档（随 F1/E1 与缺陷修复补齐）：`docs/compute.html` 增"燃耗"与"看几何"两节——燃耗的材料体积/原子-质量分数约束、链文件来源与缺失报错、burnup 为功率×时间/重金属质量估算；几何切片只画输入、六角无界限制与工具改写后的提示。`docs/env.html` 补链文件部署要求（不在核数据安装脚本内，换机要带上，附本机 sha256）。
- 缺陷修复：`openmc_view_geometry` 返回的 `path` 原为嵌套对象（`path.path` 才是相对路径），与其它工具"path=相对路径、server_path=绝对路径"的约定不一致，已统一；六角等无界几何的引擎原话改为可操作提示（给 origin/width 或换外边界）。故障样本库补 1 条几何重叠样本：重叠几何在引擎里只表现为 "Exceeded maximum number of source rejections"（不给重叠单元清单），新增 `source_rejection` 规则只做症状级识别并写明不定位重叠单元，样本总数 16→17、全量自检 17/17。
- 工具注册表 37→44；`docs/reference.html` 由生成器重生成；CI 工具计数断言同步。进度表 RF002 行更新为部分实现（工程已验证）。
- 仍未做：h5m→可运行模型 XML 生成（材料定义与 DAGMC 几何接线）、甲方 STEP 件验收判据（1000 粒子 lost particle=0）、复杂装配体质量参数封装。

## 0.8.0 — 2026-11-03

- 新增 3 个只读 MCP 资源：可调整参数、官方例题清单、最近工具调用记录；内容取自现有模块与日志，调用记录只给参数名。
- 工具包可安装：`pip install .` 提供 `mc-agent-tools`（直调）与 `mc-agent-tools-serve`（服务），依赖只认 `mcp/requirements.txt`；模块布局不变，入口在 `mcp/launcher.py`。
- 新增 GitHub Actions 校验（`.github/workflows/verify.yml`），跑同一套 Python 测试并检查安装后的工具清单。
- source-pincell 首次从 MCP 通道走完准备、核数据、计算、后处理四步（keff 1.162620±0.003084，作业 164ef008）。
- 测试增至 153 项（新增资源契约与 MCP 资源用例），`./verify.sh --tools-only` 通过。
- 仍未做：许可证与正式发布形态、Helix 联调、其余例题的 MCP 覆盖。

## 0.7.0 — 2026-10-30

- 新增固定官方例题目录和统一CLI／标准MCP工作流：13个主仓工作流、9个公开工厂、28份Notebook；其中两份依赖已移除共振重构能力的Notebook按用户决定暂缓，本轮范围48个入口。
- 初配准备固定OpenMC 0.16.0、真实官方多温度核数据、Notebook原文与网格输入、OpenMOC及报告组件。普通运行使用本包内环境与缓存，平台宿主可选。
- 官方例题按步骤保留输入、日志、计算与后处理产物；Notebook清除上游缓存后真实执行，保存每次求解的XML、原始结果及读取关联；外部网格/截面记录身份，不冒充完整独立重跑包。
- 支持燃耗与重启、脉冲、自定义编译源、随机射线、CAD与网格Notebook等公开流程；显式覆盖的参数在实际求解入口生效并留存原值。
- 取消／超时按Linux作业归属清理Jupyter内核及其求解器，保留首次失败和重复提交记录；清理未知时不报告成功。
- 状态查询支持最多30秒有界等待；只读工具提供标准MCP注解。Helix完整自然语言任务按用户决定留待验证，不纳入本次已通过范围。
- 工具包独立校验新增 `./verify.sh --tools-only`；初配与公开例题使用说明见HTML手册。公开例题实跑不替代合同指定基准、专业准确率、HPC、CAD转换、微调生成或生产验收。

## 0.6.2 — 2026-10-28

- 计算报告新增与绘图一致的可选计分/核素/过滤条件；传入选择时所有格式以同一完整数据生成图和数表，CSV/JSON保留完整所选数据。未传选择的旧全作业摘要继续兼容。
- 单一能量过滤器的横轴改为真实eV区间中点，并保留上下界与原始归一化；多过滤器明确标为展平分箱索引，避免冒充物理坐标。
- 所选报告正文以图、摘要及最多20点预览组织，标明完整数据位置与数值显示精度；公开例题和HTML教程统一传递相同选择。
- PDF转换使用无损图像设置，避免默认JPEG压缩改变科学图表；实际嵌图像素纳入核对。
- 测试增加实际报告内容、独立官方API数据/坐标对照、逆序区间、零下界、多过滤器、显式空选择、坏参数不覆盖旧报告检查；工程验证不替代专业验收。

## 0.6.1 — 2026-10-28

- 下游操作文档改为离线 HTML 手册，README 仅作入口；围绕同一算例说明输入、修改目的、预期变化及产物位置，分清 demo、工具参数和原生模型字段。
- 新增自有模型/源码扩展与独立 MCP 路线，参数参考由实际注册表生成可搜索折叠 HTML；旧 Markdown 保留定位链接。
- 修复共享 Schema 节点造成参数说明串写的问题，分别保留新目录、扫描轴、计分类型和核素等字段的正确含义；输入校验和运行行为不变。
- 初始化提示和公开例题帮助改指向手册，不再把第二次 demo 当作调参说明；不改变计算或协议行为，不增加验收承诺。


## 0.6.0 — 2026-10-27

- README按使用OpenMC/封装已有程序给出两入口；主使用指南覆盖配置、例题、已有输入、调参、计算/结果/产物、清理与标准MCP交接。重复手册保留兼容导航，专题/项目资料下沉。
- 30个工具的参数和关键返回增加实际发现可见的语义、示例与默认/省略说明；嵌套参数、路径和编号来源明确。仅为符合原Schema的真实函数默认添加注解，不改变运行默认或null/类型约束。
- 修复注册/参考生成只接受function的问题，支持program/http及async函数；参数文档与服务Schema同源，新参数缺说明时明确提示。
- create-mcp.sh离线从固定源码快照创建Python/TypeScript独立可编辑服务，拒绝覆盖并校验模板；默认同服函数/程序两工具，TypeScript参数说明和可移植入口补齐，原vendor快照不改。
- 公开例题增加particles/batches/inactive可选参数。新增参数契约、创建边界、多工具真实协议/HTTP/程序、源码与部署包、已有输入参数副本计算的用例；不新增专业或合同验收承诺。

## 0.5.1 — 2026-10-27

- 增加统一Shell入口`./cleanup.sh`，透传参数、工作区环境及退出码，内部沿用Python清理实现；不自动安装依赖或启动服务。
- README、清理文档与部署包同步入口；现有清理用例改走Shell，并覆盖异地调用、含空格路径和相对工作区，删除边界保持。

## 0.5.0 — 2026-10-27

- README作为入口，补齐部署配置、完整调用流程、产物与清理、标准MCP、接口参考、架构扩展和故障排查文档；具体宿主接入归入可选扩展。
- 四个业务演示默认输出中文摘要、警告、计算编号和服务端绝对位置；加`--json`保留完整结构化输出，原CLI/MCP保持JSON契约。依赖旧演示stdout的脚本需加此选项。
- 文件响应追加工作区与服务端位置；提供不依赖Harness和模型的标准MCP发现/调用示例，接口文档从实际工具注册表生成。
- 新增维护窗口产物清理脚本，默认预览、显式应用，只删除经检查的内部终态记录；检查扫描/修复引用和进程，保留原算例、配置及修复计划/备份，部分删除失败明确报告。
- 新增摘要、清理保护和真实OpenMC下游流程测试，覆盖不同客户端目录、实际产物路径、五种报告及真实删除后查询。工程自测不代替专业或合同验收。

## 未发布

- 启用固定纯核提供的四个本地工具 `read`、`bash`、`edit`、`write`（核心公开 API `createBuiltinTools`），工作目录默认项目根目录、`--cwd` 可覆盖；其中三个写工具并入既有确认层：非交互一律拒绝，交互终端逐次输入 `yes` 才执行。`vendor/` 里的纯核 tarball 未改，但 `cli.mjs` 改动后 `terminal.mjs` 与 `BUILD.json` 哈希随之重建。
- 这次放开改变了交付口径：工作目录不是沙箱（绝对路径、`..` 可达，`bash` 以当前用户身份执行并可联网），本地工具也不经过 MCP 服务审计。已在 Harness 手册与 README 写明边界、收窄办法（改 `cli.mjs` 的 `localToolNames` 后重建）和「改输入文件仍优先走 MCP 修复链」的理由。
- `tests/harness-terminal.mjs` 增加本地工具回归：只读 `read` 非交互可用、`bash`/`edit`/`write` 非交互被拒且不落盘、pty 下 `yes` 才写入而 `no` 不写入。
- 本机启动不再需要显式 `--config`：`harness/run.sh` 在未传 `--config` 且同目录存在 `model.local.json` 时默认加载它；没有该文件（例如打包分发出去的副本）时仍走 `MC_PRIMARY_*` / `MC_FALLBACK_*` 环境变量模式。
- `harness/model.local.json` 填为本机实测可用的端点与模型（主 `deepseek-v4.1-flash`、备 `qwen3.7-plus`，同一网关），密钥只从环境变量读取；`model.example.json` 降为通用模板，并注明它不是本机配置。
- 本机密钥存放在 `~/.config/mc-tech-execution/env`（权限 600），由 `~/.bashrc` 引入新终端；项目目录内不含任何密钥明文，`package.sh` 仍排除 `model.local.json` 与 `.env*`。
- README 补齐前置条件、模型凭据配置、环境自检和常见失败对照；演示文件路径修正为 `workspace/examples/`。

## 0.1.0 — 2026-10-13

- 建立不依赖其他工作目录的合同技术执行项目，保留规格书、技术PDF和既有文档草稿。
- 复用官方Python MCP模板和固定纯净Harness核心；协议服务与宿主可独立使用。
- 增加修复计划、实际终端确认、哈希保护与备份；增加模型主备切换和不重放边界。
- 增加本地文本查询、日志规则提示和明确标注缺料的算例目录，专业业务仍待接入。
- 以真实MCP和终端、受控模型、搬迁包验证工程能力，未宣称业务或合同验收完成。
## 0.2.0 — 2026-10-27

- 新增8个OpenMC接口和共用直接CLI：示例生成、分层校核、本地计算作业、StatePoint结果及Markdown/CSV报告。
- 固定OpenMC0.16.0；新增运行时配置、合成MGXS示例及CLI/MCP客户端示例。
- 输入快照排除旧产物，读取核对本作业输出哈希；取消/超时清理进程组，非有限统计量明确保留为空。
- 更新15项封装/API/测试/协作计划和使用、测试说明；原辅助能力及独立Harness保留。
- 初始化私有Git；打包加入mc.sh，排除本机配置、运行数据和原始合同资料。
