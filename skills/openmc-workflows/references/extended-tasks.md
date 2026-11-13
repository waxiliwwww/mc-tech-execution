# 按科学任务选择分支

所有参数以当前tools/list为准；下列是工具职责，不是固定业务算例或额外验收条件。

| 目标 | 工具与交接 | 判断与边界 |
| --- | --- | --- |
| 多参数组合 | `openmc_list_parameters` → `openmc_submit_scan` → `openmc_scan_status` → `openmc_plot_scan` / `openmc_export_scan_report` | 使用发现的参数键，确认组合数与资源。逐组合失败也须报告；统计设置对比不作物理趋势判断。 |
| 官方例题 | `openmc_list_official_examples`（limit=10, summary=true）→ `openmc_get_official_example` → `openmc_prepare_official_example` → `openmc_run_example_step` / `openmc_example_status` → `openmc_read_example_results` | 按描述返回的实际步骤顺序与前置条件执行；included不等于已验证。保留example实例、步骤与job编号的区别。内部work目录不直接当普通业务模型改参入口。 |
| STEP几何 | `openmc_inspect_step` → `openmc_convert_cad` → `openmc_build_dagmc_model` → 普通预览/校核/计算 | 用户明确材料标签/组成/密度、尺寸单位与缩放、源/边界。CAD形状不足以确定物理模型，先核对部署的CAD环境；不能把简单STEP工程测试称为复杂组件验证。 |
| 燃耗 | `openmc_configure_depletion` → `openmc_submit_job(mode="deplete")` → `openmc_read_depletion` | 先核对链文件、可燃耗材料体积、功率、时间步和单位；不要根据名称猜体积，也不把不同时间步的k混为同一结果。 |
| Slurm | `openmc_slurm_prepare` → `openmc_slurm_submit` → `openmc_slurm_status` / `openmc_slurm_cancel` → `openmc_slurm_collect` | 先确认部署者已经配置目标集群登录、资源及核数据环境；不把本地/模拟调度证据当目标集群联调。缺配置停止该分支，不索取聊天明文密码。 |
| 失败与修复 | `openmc_diagnose_job` → `openmc_prepare_repair`（或通用`preview_repair`）→ 展示diff并等用户确认 → `apply_repair` → `openmc_recheck_repair` | 自动准备修复只覆盖有明确实现的情形；其他诊断建议不能假装已能自动修复。保留首次失败和修复备份。 |

## 两个任务示例

**“导入我的组件，把密度改成我指定的值，看网格通量。”**
先拿到输入和依赖；发现实际密度键与单位后才派生，读回差异。询问/确认网格范围及统计设置，追加flux计分。完成预览、校核、提交与状态查询后，按新job_id/tally_id读取并交付图和数据。若发现的是macro单位，向用户解释并确认目标，不把g/cm³数值直接填进去。

**“拿这个已完成作业生成报告，不重新算。”**
查询原job_id确认结果可读；发现已有tally、score和过滤维度，按用户目标选择。导出同选图、CSV/JSON与报告，列出产物并按需取回。本来没有计分的量不能由后处理补造，需说明需要另行计算并征求用户安排。
