---
name: openmc-workflows
description: 用户要OpenMC模型接入、建模、改参计算、扫描、结果绘图报告或失败诊断时，用已连接的OpenMC Forge MCP组织完整任务，自己处理参数发现、路径、等待和文件交接；只询问必要业务条件。概念解释不计算，不替用户决定物理条件。
metadata:
  version: "0.24.4"
  toolkit: openmc-forge
---

# OpenMC任务工作流

用户表达需求、补充必要业务信息、取得成果；Agent负责内部操作。需要已连接的OpenMC Forge MCP；技能不启动服务、不改模型配置、不安装依赖。优先复用已有模型、完成作业和结果，只有用户要求计算才准备新作业。

通过MCP读本技能时，文件交接调用 `openmc_get_workflow(chapter="files")`，扩展任务用 `chapter="advanced"`；独立安装可读下述相对参考。参数以当前工具定义为准，不一次加载全部Schema或例题清单。

## 连接时速查

- 接入、建模/改参、计算/扫描、绘图/报告和诊断优先用对应MCP，无需用户点名；不先用Shell重写已有工具。连接/能力不足时说明原因再选CLI/脚本后备。概念解释不计算。
- 首次连接用 `openmc_environment`核对服务身份、引擎和工作区；同连接无漂移信号时复用，不反复探查。代码/版本漂移先请管理员处理。cross_sections_env_set只说明环境变量，实际核数据按模型检查。
- 自己发现参数名、路径、计分ID和调用顺序，不让用户回答内部字段问题。只问影响本次任务的对象、单位、物理条件、计分选择和未明确的重要资源取舍；明确授权和条件不逐步确认。
- 用 `openmc_list_parameters`的object名称/ID、current、unit映射请求。多个材料均可能符合时展示候选让用户选，不默认取第一个。macro是无量纲宏观截面倍率，不是g/cm³；单位冲突先问，不套数值，不按名称猜物理角色。
- 常见改参/同一原网格补计分优先 `openmc_run_case`：case_dir为当前模型，target_dir为新副本，parameters用已发现键，mesh_scores只追加到同一已有网格；默认submit=false只准备，用户明确要求计算才submit=true。包内完成派生、读回、最终预览和绑定提交，不再拆开新增全局计分。多个/没有网格时needs_selection，向用户确认；复杂/新网格等仍用原工具。中途追加从已准备副本再派生新target_dir，带上新增要求后再算；不在计算完成后改输入冒充新结果。
- 保存原job_id，用 `openmc_job_status(wait_seconds=30)`有界等待；宿主可用随包follow_job.py连续跟踪，断线/到期沿原编号接续，不重投、不自动扩大规模。
- 已知作业/计分直接 `openmc_generate_result_bundle`；核对selection、data_quality和warnings，保留完整路径、原始单位和不确定性。用户要文件就用宿主固定命令 `mc-agent-tools-transfer --mcp-url <实际地址> receive <transfer_id> <本机目标>`实际下载并校验；无需拼接mkdir，路径分别引用，计算完成、成果生成、文件交付分别报告；服务器路径不等于本机文件，下载失败不重算。最终交付直接引用宿主客户端delivery_summary中的本机完整路径和完整64位SHA256，每项单行，不缩写、不用目录树拼路径。已有旧下载回执先用宿主`mc-agent-tools-transfer verify-download --receipt <原回执>`离线重新核验；result.local_file_verified=false是服务端视角，不是本机失败或未核验的结论，详见files章节。
- 数值表直接引用结果包的selected_table/selected_value_rows，或read_results的tallies[].value_rows；每行已绑定分箱、核素、score和误差，不按多score的扁平mean数组重排坐标。用户问实际密度时查input_materials（status=verified），逐材料保留value和units；ao/wo是核素比例，不能据此判断密度未知。摘要材料列表截断时用read_results(detail="full")；旧服务没有此字段才查原job_status的execution_receipt.parameter_changes或已核实提交输入，不能把当前算例配置冒充提交值。无法核实时明确缺口。
- 术语、单位和源收敛说明直接引用result_summary/result_interpretation：score是计分类型，nuclide=total是所有核素汇总；scores实际包含total时，score=total是合法总反应计分，不能把两种轴混写。最终答复逐score保留raw_unit，不能只写per-source或让不同score共用单位；普通flux为particle-cm/source（未除体积），fission为reactions/source。单位待核实时照原文说明，不套普通单位。归一化标题使用“每源粒子归一化的原始计分；未作工程源强/功率换算”，不能笼统写“未归一化”。材料来源直接引用result_summary/result_interpretation.material_source_summary；所有引用的SHA256（材料输入、StatePoint、成果文件均包括）保留64位，不以省略号或前缀替代。没有核验依据时说明未核实，不补造指纹。零值解释直接沿用zero_value_evidence：成因未确定，tracklength不是事件计数；不能把mean=0/std_dev=0改写成没有裂变事件、没有径迹贡献或低统计的正常表现。仅在有独立诊断证据时说明具体成因及依据。工具未做成因诊断时，最终答复只说明成因未确定，不自行补充“可能与……有关”的成因清单或低统计增加本次零估计概率等判断；核查建议写为待执行动作，不把动作对象改写成本次可能成因。源收敛not_verified只写“未验证”，批次/相对σ阈值不证明已收敛或未收敛，不能断言系统偏差存在、已消除或大小。

- 结果表述基准：反应score使用工具meaning中的“反应计分/估计”，保留raw_unit；reactions/source不是实际裂变事件计数，不能把tracklength写成事件计数器。网格直接用value_rows/selected_table；不额外推导用户没问的扁平公式。确需说明时引用mesh_indexing：1-based[i,j,k]对应0-based mesh_offset=(i-1)+nx*(j-1)+nx*ny*(k-1)，首箱为0；此offset只在该RegularMesh内，不是包含其他filter/核素/score轴的数组序号。专业验收引用professional_acceptance_status：本工具未执行、没有结论；旧professional_acceptance=false不表示验收失败，不能写成“不合格/验收未通过”。作业计算失败与专业验收状态分别说明。

## 按条件选择步骤

| 当前条件 | Agent处理 |
|---|---|
| 文件在本机，服务在另一环境 | 通过宿主字节客户端上传，使用返回目录。客户端路径不会自动上传；见[文件交接](references/file-transfer.md)。 |
| 工作区已有有效原生输入 | 可直接发现参数、派生副本，不必每次检查/导入。依赖不明或跨服务器接入时才用 `openmc_inspect_model`，按真实绑定 `openmc_import_model`；ready仅证明依赖存在。 |
| 明确参数修改/同一网格追加 | 优先`openmc_run_case`派生、读回并核对mesh_check；submit=false不会计算。提交时true，不必再单独prepare/update/preview/submit。仅原子派生或复杂流程仍可prepare_case。歧义问用户。 |
| 新建模型 | 模板用 `openmc_create_model`；组合模型先 `openmc_model_capabilities`，需要时取当前完整Schema后 `openmc_build_model`。用户Python需先在适用环境导出原生输入，import不会执行脚本。 |
| 已有计分满足要求 | 保留。新增/调整用 `openmc_define_tally`、`openmc_update_tally`，确需删除才 `openmc_delete_tally`。不每轮重复定义。 |
| 使用原子工具修改和计分最终确定 | 需执行说明/绑定条件时 `openmc_preview_input(mode="run",threads=实际线程)`，默认不取全文。用户中途追加后，先修改再更新预览，不沿用旧preview_id。 |
| 用户明确要求计算，条件和资源无歧义 | 常见改参/同网格任务在 `openmc_run_case`传submit=true即可；原子流程用 `openmc_submit_job`，不在组合已提交后再次提交。不逐步审批。提交本身保留结构/语义检查；额外API/几何检查按需用 `openmc_validate_inputs`。错误不能绕过，未做的检查如实说明。 |
| 已有作业或提交结果未知 | 保存/查询原job_id，不重复提交。已有结果分析不重算，旧作业缺计分不能靠后处理补造。 |

执行说明仅绑定输入、核数据定位、配置和线程，不是用户同意凭证。绑定提交传同case_dir/mode/threads及preview_id；过期时查明变化再核对，不自动沿用新条件。原直接提交仍可用。未知物理条件不套例题默认值，不为收敛擅自扩大统计规模。

## 等待和接续

- `openmc_job_status(job_id,wait_seconds=30)`最多等待30秒至终态，默认0立即返回。到期仍运行不是失败，不取消或重投；进度/日志解析不到就说明未知，不编预计时间。
- 有宿主执行能力时，让随包 `examples/follow_job.py --mcp-url <地址> --job-id <原编号> --timeout <等待秒数> --receipt <本机回执>`连续等待，减少模型反复查询。它不提交、不取消；到期或中断后沿同一命令/编号继续。只有服务端包时不能假设宿主已有脚本和依赖。
- 断开回来先查原编号；编号不在上下文时用 `openmc_list_jobs`按任务名称找，多个候选核对后选，不猜“最新一个”。completed才处理结果，failed/interrupted先诊断，cancelled不算成功。取消仍查真实终态和process_cleanup。
- 在已有任务上下文保留：来源/当前副本、最新要求、对象和单位、参数差异、job_id、必要时preview_id和输入指纹、结果选择、传输回执及本机目标。不另建台账，不要求用户抄内部编号。

## 从结果到可用文件

1. 选择未知或只需结果事实时 `openmc_read_results(max_values=20)`发现tally/score/维度，并取result_summary；它不接收计分选择参数。已知选择直接 `openmc_generate_result_bundle`，无需先读全结果或再list_artifacts。同作业/计分生成图、完整数据、报告、脚本和清单；原单项绘图/报告、自定义分析继续可用。预览之外的完整数值用结果包或原生数据，不以20行冒充完整数组。
2. 核对state、selection、source、normalization及data_quality。data_quality只统计本次选择的完整箱数、零均值/不可用箱；不同score不能共用计数，不确定零值原因。3D层、能量箱和MeshFilter从1起始的[i,j,k]按当前Schema，不用截断预览冒充完整数组。
3. 优先沿用工具的delivery_summary和同文delivery_file，完整路径不缩写。重要警告说明影响及下一步；保留低统计、非有限、零标准差的不确定性，不能把“可能”改为“确定”。原始flux为particle-cm/source，未除体积不写每平方厘米；fission为reactions/source，未换算源强/功率不写每秒。统计误差不证明源收敛或专业准确率。
4. 用户要文件就继续[文件交接](references/file-transfer.md)，用包返回的实际路径下载；宿主校验并保存后才给本机位置。不能止于服务端路径。无宿主文件能力时明确“成果已生成，文件尚未取回”及缺少的能力，不伪造已下载。
5. 下载会话返回host_action.entrypoint与argv，照实际参数执行单条命令，不自行拼复合Shell或改成位置参数download。服务端不能替宿主保存文件；宿主未安装客户端时先核对可用入口，不把服务器Python路径当宿主安装。
6. 分别报告计算终态、成果生成和文件交付。下载失败沿同一回执/原成果恢复，不重新出包或求解。已校验保存但暂存关闭未确认时，说明文件已可用、暂存清理待重试。工具摘要仍不保证模型忠实呈现或宿主直接展示。

改图用取回的plot_script及同名JSON/CSV，在客户端用NumPy/Matplotlib重画并保持目录关系，不重新计算，不覆盖原包。图表可改样式，数据、坐标和误差不改。

## 扩展与失败

扫描、官方例题、CAD、燃耗、Slurm及修复按需读[扩展任务](references/extended-tasks.md)。特殊分析可用原生Python/Shell，保留来源和选择。失败先区分输入、依赖/环境、工具及传输，保留原记录；结果未知先查询。修复实际diff需确认后才 `apply_repair`，计算授权不等于接受未知修复。新资源和真实模型调用先明确预算，不自动重试扩大范围。

完整使用、部署和接口在随包docs稳定导航；技能可独立安装，不依赖个人目录。
