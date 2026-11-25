# 按科学任务选择分支

所有参数以当前tools/list为准；下列是工具职责，不是固定业务算例或额外验收条件。

| 目标 | 工具与交接 | 判断与边界 |
| --- | --- | --- |
| 多参数组合 | `openmc_list_parameters` → `openmc_submit_scan` → `openmc_scan_status` → `openmc_plot_scan` / `openmc_export_scan_report` | 使用发现的参数键，确认组合数与资源。逐组合检查parameter_diff.verified及落盘值；失败/未运行项须报告。每点核对后才提交，不把请求值当实际值；统计设置对比不作物理趋势判断。 |
| 官方例题 | `openmc_list_official_examples`（limit=10, summary=true）→ `openmc_get_official_example` → `openmc_prepare_official_example` → `openmc_run_example_step` / `openmc_example_status` → `openmc_read_example_results` | 按描述返回的实际步骤顺序与前置条件执行；included不等于已验证。保留example实例、步骤与job编号的区别。内部work目录不直接当普通业务模型改参入口。 |
| STEP几何 | `openmc_inspect_step` → `openmc_convert_cad` → `openmc_build_dagmc_model` → 普通预览/校核/计算 | 用户明确材料标签/组成/密度、尺寸单位与缩放、源/边界。CAD形状不足以确定物理模型，先核对部署的CAD环境；不能把简单STEP工程测试称为复杂组件验证。 |
| 燃耗 | `openmc_configure_depletion` → `openmc_submit_job(mode="deplete")` → `openmc_read_depletion` | 先核对链文件、可燃耗材料体积、功率、时间步和单位；不要根据名称猜体积，也不把不同时间步的k混为同一结果。 |
| Slurm | `openmc_slurm_prepare` → `openmc_slurm_submit` → `openmc_slurm_status` / `openmc_slurm_cancel` → `openmc_slurm_collect` | 现场脚本可选；先复用已确认的站点设置，没有脚本用内置默认模板。说明本次资源/环境计划，只问缺失项；未知登录或核数据条件先补齐，不猜集群地址或路径。流程见下方；受控测试不当真实集群联调。 |
| 失败与修复 | `openmc_diagnose_job` → `openmc_prepare_repair`（或通用`preview_repair`）→ 展示diff并等用户确认 → `apply_repair` → `openmc_recheck_repair` | 自动准备修复只覆盖有明确实现的情形；其他诊断建议不能假装已能自动修复。保留首次失败和修复备份。 |

## 两个任务示例

批次失败：先查每个job_id终态/进程清理，解释原因并取得用户续算确认；openmc_resume_scan返回关联批次，成功项复用原编号，查询新scan_id确认实际补齐。条件变化需新批次，旧记录不改，不自动重试。


**“导入我的组件，把密度改成我指定的值，看网格通量。”**
先拿到输入和依赖；发现实际密度键与单位后才派生，读回差异。询问/确认网格范围及统计设置，追加flux计分。完成预览、校核、提交与状态查询后，按新job_id/tally_id读取并交付图和数据。若发现的是macro单位，向用户解释并确认目标，不把g/cm³数值直接填进去。

**“拿这个已完成作业生成报告，不重新算。”**
查询原job_id确认结果可读；发现已有tally、score和过滤维度，按用户目标选择。导出同选图、CSV/JSON与报告，列出产物并按需取回。本来没有计分的量不能由后处理补造，需说明需要另行计算并征求用户安排。

## Slurm：没有现场脚本也可以开始

- 先复用已确认的站点信息；用离线prepare返回的资源摘要和脚本核对，配置文件可读时再参考。纯远程MCP不能假设Agent能读取服务端私有配置。可问：“有以前跑通的提交脚本吗？没有也可以，我会用内置模板；先检查已有设置，只向你补问缺少的队列、计算资源或软件环境。”已有信息不重复问，每个会话不重新索取。
- 有脚本时，可粘贴内容，或提供Agent能读取的路径/上传文件；文件只在用户本机时，先按已有文件交接上传。只读脚本，解释拟复用的队列、资源、环境及启动方式；不直接执行参考脚本，不悄悄丢弃不认识的设置。需要保存/更新私有站点配置时，先展示简明差异并取得用户同意；已有授权按原范围执行。
- 没有脚本时直接用包内模板。无站点资源默认时建议1节点、1进程、每进程1线程、10分钟，队列用站点默认；这是小规模资源建议，不是物理模型默认或对集群限额的保证。目标登录、可写目录、OpenMC及核数据环境仍必须可确认；缺少时只询问这些项，不要求用户自行编写脚本。
- “36核”先区分进程与线程。多进程须站点确认MPI版OpenMC与启动器，并在私有配置设置mpi_command（不重复写进程数）；不自动猜mpirun/srun。已知36进程×1线程就按36启动，若申请36却只启动32，先确认是否有意预留。
- 提交前简述意图，例如：“这次用已确认的CPU队列，1节点、36进程×1线程，沿用核数据库和OpenMC环境；我会生成in.slurm、提交后跟踪并取回结果。”没有明确计算授权时先给计划，不擅自提交。已有计算授权且条件未变，不逐工具求批准。
- Agent处理prepare/submit/status/cancel/collect及结果交接，保存原submission_id和cluster_job_id。sbatch返回编号只表示已提交；scontrol查询活动任务，sacct补终态和退出码。查询不明沿原记录重查，不重投；取消返回成功仍须确认实际终态。取回后继续普通结果工具并下载给用户，不止于服务器路径。

查询与取消沿用现场熟悉的命令：有已授权的登录节点命令能力时，Agent可用`sinfo`查看可用队列；现有Slurm MCP没有单独查询队列的入口，不能假装已经查过，没有这种能力时只补问必要队列信息。提交后现有status内部调用`scontrol show job`并结合`squeue`/`sacct`区分排队、运行与结束；用户说取消，由现有cancel调用`scancel <cluster_job_id>`，再查实际终态。不要让用户自己轮流执行这些命令，也不把`top`看到进程或进程消失当作计算成功。当前聚焦Slurm；“HPC都会是Slurm”不作为跨站点保证。
