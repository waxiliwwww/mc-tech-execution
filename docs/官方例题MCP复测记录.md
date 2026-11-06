# 官方例题 MCP 复测记录（2026-11-06）

本文件是[官方例题工作流](official-examples.html)的本轮实跑记录，也可以当作“让智能体通过 MCP 逐步跑官方例题”的操作手册。它记录的是这次部署、这次版本、这次实例的真实状态；清单里的 `execution_status=not_run` 是静态占位，不能拿来当运行结论，历史开发快照也不能代替本记录。

## 1. 范围、版本与环境

- **范围**：官方清单 50 个入口；48 个 `scope=included` 在本轮测试；2 个核数据 Notebook（`notebook-nuclear-data`、`notebook-nuclear-data-resonance-covariance`）沿用既有用户决定暂缓，不测。
- **方式**：标准 MCP 会话（Streamable HTTP），由智能体按文档流程调用；不调用 CLI，不读工具源码，不手工改模型。工具顺序为
  `openmc_list_official_examples` → `openmc_get_official_example` → `openmc_prepare_official_example` → `openmc_example_status` → `openmc_run_example_step` → `openmc_read_example_results`。
- **版本**：工具包 0.11.9；服务 `http://127.0.0.1:8770/mcp`，`/health` 返回 0.11.9，`/info` 55 个工具；OpenMC 0.16.0；核数据 `official-endfb-vii.1`。
- **机器**：16 核、15 GB 内存、844 GB 空闲；计算与文件都在服务端 `workspace/`。
- **采样**：transport / api 例题使用官方默认（每代 1000 粒子、100 批、10 个非活跃批）；depletion、depletion-restart、pulsed 按各自单步脚本原设置；Notebook 按原文顺序执行，不接受通用覆盖。本轮没有用缩配，因此不是“低统计练习”记录，但仍不是物理验收。
- **判定**：实例 `state=completed` 且 `next_step=null`，并且 `openmc_read_example_results` 返回 `state=completed`，记为**流程完成**。任何一步失败保留首次失败，不重跑覆盖。所有结果的 `professional_acceptance` 均为 `false`，本记录不作物理正确性结论。

## 2. 结论

- 48 个 included 入口中，**46 个流程完成，0 个失败，2 个未完成（操作者取消）**；另外 2 个核数据 Notebook 按既有决定暂缓，不在本轮。
- 首跑的两个 C++ 自定义源例题都在 `compile` 步骤失败，原因是运行时 OpenMC 的 CMake 包指向缺失的 `include/eigen3`；已在 `quickstart.sh` 中加“检查并补装 Eigen”并在本机复测，两例均 5/5 通过（见 5.1）。这不是 MCP 调用或例题输入错误。
- 两个未完成项：`notebook-cad-based-geometry` 在最后一个代码单元长时间无输出，`source-pincell-depletion-restart` 的 execute 已完成、restart 步在窗口内未跑完；两者都保留实例编号和首错/现状，未判为工具失败。
- **产物与磁盘**：全量跑一遍产生约 3 GB 计算产物（`.mc-jobs`）；本轮已按用户要求停止全量方式，改为**一例一跑、跑完清理**，并清理了最大的几个产物目录（释放约 2.2 GB）。记录 JSON 仍在，重产物已删，见第 7 节。
- 所有完成项都能用记录中的 `instance_id` / `job_id` 追溯；每一步的工具参数、作业终态和结果摘要都在 `artifacts/runs/20261106-official-examples/<example_id>.json`。
- 本轮同时验证了两点：MCP 工具链足以让智能体用自然语言逐步推进；不同例题的步骤数、输入要求和失败位置确实不同，不能套一个固定脚本。

## 3. 自然语言复现步骤（操作手册）

### 3.1 前置

服务必须先启动，并且健康检查通过：

```bash
cd <包根>
./mcp/start.sh          # 另开终端；本记录用的是前台进程
curl -s http://127.0.0.1:8770/health
```

确认 `status=ok`、`version=0.11.9`；`openmc_environment` 的 `executable_available`、`api_available`、`version_matches` 应为 true。

启动输出本身应给出足够信息：版本、MCP URL、传输与鉴权、`connection.json` 路径、工具清单和下一步。宿主配置按各自格式填写，DSH 示例在部署页。0.11.9 的 `./mcp/start.sh` 已按此输出；若只看到“已启动”而没有地址/传输信息，先核对启动的是不是当前版本。

C++ 自定义源还需要 `include/eigen3`。`quickstart.sh` 会检查并在缺失时补装 `eigen=3.4`；若用 `--openmc-prefix` 指向自备环境，需要自行保证该目录存在，否则 `compile` 会失败。

### 3.2 单个例题

对智能体说一句自然语言即可，例如：

> 用 MCP 跑官方例题 `source-pincell`，新目录用 `cases/my-trial/pincell`；按 `next_step` 逐步执行，完成后读结果，告诉我实例编号、每步作业编号和 keff。

智能体按下面顺序调用（**每一步以 status 返回的 `next_step` 为准，不能跳步**）：

1. `openmc_get_official_example(example_id)`：看输入条件、公开参数、步骤链。
2. `openmc_prepare_official_example(example_id, case_dir[, parameters])`：目录必须是新的；返回 `instance_id`、`next_step`。
3. `openmc_example_status(instance_id[, wait_seconds])`：取真实状态、`next_step`、各步 `job_id`。`wait_seconds` 只是有上限等待，超时不等于失败。
4. `openmc_run_example_step(instance_id, step)`：`step` 取 `next_step.id`；返回 `job_id`，状态先是 `queued`，不代表完成。
5. 重复 3–4，直到 `next_step=null`。
6. `openmc_read_example_results(instance_id[, max_values])`：读 keff、tally 维度/截断、Notebook 单元与图。
7. 需要图或报告时，用 compute 步骤的 `job_id` 调 `openmc_plot_results` / `openmc_export_report`。

**判断完成**：`state=completed` 且 `next_step=null`；transport 例题再看 `results.state=completed` 且 `keff.mean` 非空；Notebook 再看 `executed_code_cells=code_cells` 且 `errors=[]`。

**失败处理**：某步 `state=failed` 时，记下 `failed_step` 和 `job_id`，用 `openmc_job_status(job_id)` 看 `log_tail`；保留原实例，换一个新目录/new instance 重试，不覆盖首次失败。

### 3.3 特殊输入

| 例题 | 额外参数 |
| --- | --- |
| `api-slab-mg` | `parameters.mgxs_file` 必填，指向工作区内真实多群 HDF5，例如 `inputs/c5g7-uo2-as-mat1.h5` |
| `api-random-ray-pin-cell` | 可选 `parameters.factory.second_temp=true`（本轮显式开启） |

### 3.4 一例一跑（默认）与批量（仅磁盘有余量时）

**默认一次只跑一个例题**：读结果、确认不再需要后，用 `./cleanup.sh --example-id <实例编号>` 预览并加 `--apply` 清理该实例的步骤作业与输入快照；原 `workspace/cases/...` 输入不受影响。这样磁盘占用是“一例的量”，不会累积。

本轮先按全量清单跑了一遍，48 例的 `.mc-jobs` 合计约 3 GB；在磁盘紧张时这是错误做法。下面的批量驱动只适合维护窗口、且有足够空间时使用，不能作为日常复测方式。

批量驱动（不是工具包的一部分，只是把上面的步骤自动化，仍然走标准 MCP，不绕过协议）：

```bash
cd <包根>
mcp/.venv/bin/python artifacts/runs/20261106-official-examples/driver3.py --resume
```

- 每个例题新开一个 MCP 会话，按 `status.next_step` 推进；一例一份 JSON。
- `--resume` 跳过已有记录；`--ids a,b` 只跑指定例题。
- 驱动只做三件事：准备实例、循环“查状态→跑下一步”、读结果；不替用户选模型、不改运行设置。

### 3.5 记录字段

| 字段 | 含义 |
| --- | --- |
| `calls` | 每次工具调用：工具名、参数、instance_id、job_id、错误 |
| `steps` | 每个已完成步骤的 `state`、`exit_code`、`job_id`、耗时 |
| `results` | keff、tally 的 shape/总数/截断、Notebook 单元/图/求解阶段 |
| `state` | `completed` / `failed` / `prepare_failed` / `driver_error` 等 |
| `case_dir` | 本次实例的输入目录（服务端相对工作区） |

## 4. 结果总表

本轮收录 **48** 个入口：完成 **46**，失败 **0**，未完成/取消 **2**；暂缓 2 不在表内。

| 例题（ID） | 中文名 | 组 | 步骤链 | 实例 | 本轮结果 | 用时 |
| --- | --- | --- | --- | --- | --- | --- |
| `source-assembly` | assembly | 主仓 | prepare → data → compute | `c10c944ca3bf4ce381bdd7ed1209b333` | k=1.15679 ± 0.00267；0 个计分/0 值 | 63.9s |
| `source-custom-source` | custom_source | 主仓 | compile → prepare → data → compute → postprocess | `c3ca862e65cc4ddbad632a263aff8d8e` | k=—；0 个计分/0 值 | 128.6s |
| `source-jezebel` | Jezebel钚金属球临界计算 | 主仓 | prepare → data → compute | `b753b19a52494ba79e8b1623515b873e` | k=0.99894 ± 0.00048；0 个计分/0 值 | 18.8s |
| `source-lattice-hexagonal` | lattice/hexagonal | 主仓 | prepare → data → compute | `4c27f5ab775b4d0cab96639235e199f0` | k=0.06028 ± 0.00048；1 个计分/17 值 | 40.4s |
| `source-lattice-nested` | lattice/nested | 主仓 | prepare → data → compute | `bf10b7ad2dea4daa87f6b6a55586443a` | k=0.05953 ± 0.00044；1 个计分/16 值 | 24.1s |
| `source-lattice-simple` | lattice/simple | 主仓 | prepare → data → compute | `6a070e0894754ca6b9aa9d786bada330` | k=0.05890 ± 0.00029；1 个计分/16 值 | 21.6s |
| `source-parameterized-custom-source` | parameterized_custom_source | 主仓 | compile → prepare → data → compute → postprocess | `9ab7824566294b988ba58d162ee5f220` | k=—；0 个计分/0 值 | 22.7s |
| `source-pincell` | 连续能燃料棒模型 | 主仓 | prepare → data → compute → postprocess | `5420bced93dd48fa99569abf1e6ebf74` | k=1.16262 ± 0.00308；2 个计分/30500 值 | 196.4s |
| `source-pincell-depletion` | 燃料棒燃耗计算 | 主仓 | execute | `65bd51bc87874a21b18d4146f3437e44` | k=—；0 个计分/0 值 | 519.2s |
| `source-pincell-depletion-restart` | 燃料棒燃耗续算 | 主仓 | execute → restart | `1007c36f4c6a40379ad68c68a79fbb34` | 已取消（execute 已完成） | 2265.2s |
| `source-pincell-multigroup` | C5G7多群燃料棒模型 | 主仓 | prepare → data → compute | `583bf825568d417f806213bcb40f436d` | k=1.32606 ± 0.00242；2 个计分/30500 值 | 60.9s |
| `source-pincell-pulsed` | 脉冲中子源与时间响应 | 主仓 | execute | `07029f25241d407983f0dad236728d9e` | k=—；0 个计分/0 值 | 431.3s |
| `source-pincell-random-ray` | 随机射线燃料棒计算 | 主仓 | prepare → data → compute | `f0c177ae79e048dcaf5a408842bb3481` | k=1.32616 ± 0.00054；1 个计分/84 值 | 120.7s |
| `api-pwr-assembly` | PWR组件内置模型 | 内置工厂 | prepare → data → compute | `2796ebc3c8bb4de1b751750661b80186` | k=1.16580 ± 0.00026；0 个计分/0 值 | 15.3s |
| `api-pwr-core` | PWR堆芯内置模型 | 内置工厂 | prepare → data → compute | `728082f61ecf4a9fa610f248946373ed` | k=1.01089 ± 0.06657；0 个计分/0 值 | 21.8s |
| `api-pwr-pin-cell` | PWR燃料棒内置模型 | 内置工厂 | prepare → data → compute | `f838b0236dea493ab990ff4a86776dc2` | k=1.21337 ± 0.02704；0 个计分/0 值 | 29.0s |
| `api-random-ray-lattice` | 随机射线晶格内置模型 | 内置工厂 | prepare → data → compute | `ce48d1d6de074c5e9a0852d64a320049` | k=0.84003 ± 0.00802；1 个计分/84 值 | 24.6s |
| `api-random-ray-pin-cell` | 随机射线燃料棒内置模型 | 内置工厂 | prepare → data → compute | `347a5d289bcc43bb8e7e1e8723c5fe29` | k=1.32579 ± 0.00051；1 个计分/3 值 | 55.5s |
| `api-random-ray-three-region-cube` | 随机射线三区立方体 | 内置工厂 | prepare → data → compute | `1569200fa26c45a69c23b50e750ca49b` | k=—；3 个计分/3 值 | 19.5s |
| `api-random-ray-three-region-cube-with-detectors` | 带探测区域的随机射线立方体 | 内置工厂 | prepare → data → compute | `630a6c329b2349408563d34791965017` | k=—；5 个计分/5 值 | 22.5s |
| `api-slab-mg` | 多群一维平板内置模型 | 内置工厂 | prepare → data → compute | `f1332628f31946988838513b8fcec13c` | k=0.74174 ± 0.00613；0 个计分/0 值 | 17.2s |
| `api-sphere-with-shielded-pocket` | 带屏蔽凹腔的球体模型 | 内置工厂 | prepare → data → compute | `c1cd1de848e64443bbb8937b41a584ac` | k=—；0 个计分/0 值 | 22.8s |
| `notebook-cad-based-geometry` | CAD几何与DAGMC | Notebook | execute | `aabc410c0709410bb6b9e07846331fc4` | 已取消 | 2372.0s |
| `notebook-candu` | CANDU燃料束建模 | Notebook | execute | `1d96f4e6e82b4f17a4587826e21064b0` | Notebook 16/16 单元，4 图，4 次求解 | 18.8s |
| `notebook-capi` | C API分批计算与运行中改参 | Notebook | execute | `8cb00b7485d14fcfb9b19fa3f418825b` | Notebook 19/19 单元，1 图，4 次求解 | 99.2s |
| `notebook-depletion` | 燃耗与核素随时间变化 | Notebook | execute | `f68d26e04c2341dab296756cd290107a` | Notebook 40/40 单元，6 图，10 次求解 | 64.8s |
| `notebook-expansion-filters` | 展开过滤器与统计量 | Notebook | execute | `1228811427b34304ba2043b279e8d17c` | Notebook 37/37 单元，8 图，2 次求解 | 30.9s |
| `notebook-flux-spectrum` | 通量能谱 | Notebook | execute | `65284b786e124f388e7ff598f5d7a26c` | Notebook 10/10 单元，2 图，1 次求解 | 53.7s |
| `notebook-gamma-detector` | 伽马探测器 | Notebook | execute | `d303fa9723634654a7928b6fb5cf282f` | Notebook 16/16 单元，3 图，2 次求解 | 7.2s |
| `notebook-hexagonal-lattice` | 六角形晶格建模 | Notebook | execute | `675f3ffe382449f6b23273e15f20e644` | Notebook 13/13 单元，3 图，0 次求解 | 3.8s |
| `notebook-mdgxs-part-i` | 多域截面生成（第一部分） | Notebook | execute | `afa8ceeec4454a04be7e5bc3747fd7f0` | Notebook 25/25 单元，4 图，1 次求解 | 31.6s |
| `notebook-mdgxs-part-ii` | 多域截面生成（第二部分） | Notebook | execute | `15a1f66972d14347b2d1ad5e960c21bc` | Notebook 22/22 单元，2 图，1 次求解 | 21.7s |
| `notebook-mg-mode-part-i` | 多群模式（第一部分） | Notebook | execute | `96b44e25f2854b03948ebbae40de531a` | Notebook 16/16 单元，2 图，2 次求解 | 24.9s |
| `notebook-mg-mode-part-ii` | 多群模式（第二部分） | Notebook | execute | `2b7bd350dea648db9c942de8e9711d98` | Notebook 47/47 单元，5 图，5 次求解 | 503.6s |
| `notebook-mg-mode-part-iii` | 多群模式（第三部分） | Notebook | execute | `eea4711ccd81425787354bd4722955b6` | Notebook 42/42 单元，4 图，5 次求解 | 199.4s |
| `notebook-mgxs-part-i` | 多群截面生成（第一部分） | Notebook | execute | `0e751f3c7dbf49eb9ff4a1dbd4173f93` | Notebook 25/25 单元，1 图，1 次求解 | 8.3s |
| `notebook-mgxs-part-ii` | 多群截面生成（第二部分） | Notebook | execute | `e0b59655f7fc4d04a1d96c8e13e099b8` | Notebook 32/32 单元，2 图，1 次求解 | 168.4s |
| `notebook-mgxs-part-iii` | 多群截面生成（第三部分） | Notebook | execute | `dfabb157c6524129ba7b4ff01658d0bd` | Notebook 41/41 单元，2 图，1 次求解 | 57.1s |
| `notebook-pandas-dataframes` | 结果转DataFrame表格 | Notebook | execute | `f69ec84b04ca4ffdacb0fd0f563625b8` | Notebook 34/34 单元，5 图，1 次求解 | 7.8s |
| `notebook-pincell` | 燃料棒建模与计算 | Notebook | execute | `7be0d7bb2ec44a26bbe4b2064a8a6023` | Notebook 47/47 单元，5 图，4 次求解 | 11.8s |
| `notebook-post-processing` | 计算结果后处理 | Notebook | execute | `48402271a9a3475db250ba822b8a57ee` | Notebook 27/27 单元，5 图，1 次求解 | 88.4s |
| `notebook-search` | 参数搜索与临界条件查找 | Notebook | execute | `bce6bbafe36f4370a681289436db154b` | Notebook 4/4 单元，1 图，9 次求解 | 113.7s |
| `notebook-shielded-room-weight-window` | 屏蔽房间与权窗 | Notebook | execute | `87167787907a4de69d6fb206344ca80b` | Notebook 13/13 单元，0 图，6 次求解 | 272.3s |
| `notebook-tally-arithmetic` | 计分结果运算 | Notebook | execute | `0eaabb9d7cdc40c1acf019c92241b571` | Notebook 36/36 单元，1 图，1 次求解 | 8.1s |
| `notebook-tally-power-normalization` | 功率归一化 | Notebook | execute | `cb9996b79d2c4f45b37205c4189c15c2` | Notebook 13/13 单元，0 图，3 次求解 | 29.0s |
| `notebook-triso` | TRISO颗粒建模 | Notebook | execute | `1f6ed55ac4194c9eb12e25093e2fa80a` | Notebook 13/13 单元，2 图，0 次求解 | 6.9s |
| `notebook-unstructured-mesh-part-i` | 非结构网格（第一部分） | Notebook | execute | `5f0be3a6ffd24aafb5af4173b5fc33df` | Notebook 19/19 单元，7 图，5 次求解 | 141.3s |
| `notebook-unstructured-mesh-part-ii` | 非结构网格（第二部分） | Notebook | execute | `5564ee14a34e4489a9615d55451a4019` | Notebook 19/19 单元，2 图，3 次求解 | 900.5s |

## 5. 已修复项与未完成项

两个 C++ 自定义源例题首跑在 `compile` 步骤失败；首错保留如下，随后已修复并复测通过：

| 例题 | 首跑实例 | 首跑 compile job_id | 修复后复测实例 | 复测结果 |
| --- | --- | --- | --- | --- |
| `source-custom-source` | `b8fd8701dcda4c8b953845e17f6389f3` | `2674771120ba42d19d4ec77b90df96d1` | `c3ca862e65cc4ddbad632a263aff8d8e` | 5/5 完成（compile、prepare、data、compute、postprocess 均 completed） |
| `source-parameterized-custom-source` | `fba8277c69574a75b4f28013ca475878` | `b71080aa04b441e0b16afefb58887cdb` | `9ab7824566294b988ba58d162ee5f220` | 5/5 完成 |

两例首跑日志尾相同的关键行：

```text
CMake Error in CMakeLists.txt:
  Imported target "OpenMC::libopenmc" includes non-existent path
    ".../.runtime/openmc/include/eigen3"
  in its INTERFACE_INCLUDE_DIRECTORIES.
```

判断：编译器本身可用（日志显示 GNU 15.2.0、CMake 找到了 OpenMP 和 OpenMC 0.16.0），问题在运行时 OpenMC 的 CMake 包引用了未安装的 `include/eigen3`。这属于**部署运行时/打包**问题，不是 MCP 工具或例题适配错误。修复方式：`quickstart.sh` 在 OpenMC 环境检查后增加 `include/eigen3` 校验，缺失时用 micromamba 补装 `eigen=3.4`；已有环境也可手工执行同一安装。补装后两例均复测 5/5，首跑失败保留在上表，不重跑覆盖。

### 5.1 未完成（操作者取消，不判失败）

| 例题 | 状态 | 实例 | 现象 |
| --- | --- | --- | --- |
| `notebook-cad-based-geometry` | `execute` 取消 | `aabc410c0709410bb6b9e07846331fc4` | 最后一个代码单元 47/47 长时间无新输出（约 12 分钟）；当时机器上另有重负载，操作者取消。 |
| `source-pincell-depletion-restart` | `restart` 取消 | `1007c36f4c6a40379ad68c68a79fbb34` | `execute` 已完成 6 步燃耗（0–5 天 keff 序列）；`restart` 步在 45 分钟窗口内未完成，操作者取消。 |

这两项不能算“通过”，也不能算工具或例题缺陷。重跑时应一次只跑这一例，留足时间，并先确认 CPU 与磁盘余量；CAD Notebook 还应先确认 DAGMC/CAD 环境与最后单元是否需要交互。

### 5.2 待复核的读数异常

`source-lattice-simple`、`source-lattice-nested`、`source-lattice-hexagonal` 三例由 `openmc_read_example_results` 返回的 eigenvalue k 约为 0.059–0.060，量级与常见燃料晶格临界结果不符。本轮没有改模型、没有做物理复核，只把它记为**待复核读数**，不作为通过或失败依据。复核时应单独跑一例，检查 `settings.xml` 的 `run_mode`/边界、StatePoint 的 k 与 tally，确认是模型本身（例如故意深次临界）还是读数口径问题。

## 6. 方法偏差与说明

- 最初用 4 个分片并行跑，服务端在多个 30 秒等待调用下出现短暂排队（`/health` 超时），不构成工具缺陷；已停止分片，改为**单路顺序 + 8 秒轮询 + 断点续跑**。
- 之后又把剩余例题按两路跑完；用户随即指出**产物大、磁盘不足，不应一次全测**。本轮已停止该方式，清理了最大的几个产物目录，并把默认方法改为一例一跑（见 3.4）。
- 测试期间机器上还有与本任务无关的重负载（helix 测试进程占满多核），单例耗时明显偏高；`notebook-cad-based-geometry` 和 `source-pincell-depletion-restart` 的取消与此有关。
- `source-pincell` 与 `notebook-pincell` 另有一次手工会话（直接逐个 MCP 调用）作为对照；表中列的是驱动实例，手工实例见第 7 节。
- `source-jezebel` 首次试点后因并行分片复用了同名目录，已用新目录重跑并在表中保留重跑实例；首次试点目录仍在。
- 2 个核数据 Notebook 按既有用户决定暂缓，本轮不执行；未完成不等于失败。

## 7. 证据位置

- 每例记录：`artifacts/runs/20261106-official-examples/<example_id>.json`
- 汇总表：`artifacts/runs/20261106-official-examples/summary.md`；机器可读汇总：`summary.json`
- 驱动与日志：`driver3.py`、`resume3.log`（单路）；`shard*.log`（已废弃的并行尝试，保留首错）
- 实例目录：`workspace/.mc-examples/<instance_id>/`；步骤作业：`workspace/.mc-jobs/<job_id>/`。**重产物已部分清理**：`notebook-cad-based-geometry`、两个 `unstructured-mesh` 实例的作业/实例目录已用 `cleanup.sh --apply` 删除（约 2.2 GB）；对应 JSON 记录仍在。其余批量产物约 720 MB，后续按一例一跑继续清理。
- 手工会话实例：`source-pincell` = `b8365f40d21446d99ce4c426ab0fec08`；`notebook-pincell` = `26491c11d1814392865eb2640cb4e01e`
- 修复后复测实例：`source-custom-source` = `c3ca862e65cc4ddbad632a263aff8d8e`；`source-parameterized-custom-source` = `9ab7824566294b988ba58d162ee5f220`；首跑实例/作业见第 5 节。
- onboarding 验证实例：用户参数文件生成 `cases/user-trial/sphere-001`，提交 job `257f4f19934041738076c3e430246d8f`，导入 `cases/user-trial/sphere-import`。
- 服务版本证据：`/health`（0.11.9）、`/info`（55 tools）、`openmc_environment` 报告的可执行程序/API 可用

## 8. 边界

- 本记录证明**流程可复现**，不证明物理结果正确；官方设置、真实核数据、真实 OpenMC 已执行，但没有参考值/容差/专业判定。
- Notebook 只按原文执行并检查单元完成/错误/图数量，未逐单元人工审阅物理含义。
- 两个 compile 失败已修复并用新实例复测；CAD/restart 两个未完成项没有重跑，保留原状态和原因。
- 本记录不覆盖 HPC、CAD、甲方模型、远程下载和正式验收；这些按各自计划另行执行。

## 9. 交付前 onboarding 验证（0.11.9）

本轮在例题复测之后，又按“别人拿到仓库 → 配环境 → 启动 MCP → 在 DSH 连接 → 用自然语言处理自己的输入 → 跑作业”走了一遍，重点不是后台能否跑，而是**每一步是否给人必要信息**。

| 环节 | 验证动作 | 人实际看到的信息 | 结论 |
| --- | --- | --- | --- |
| 环境 | `quickstart.sh` 增加 `include/eigen3` 检查；缺失时补装 `eigen=3.4` | 补装后两例 C++ 自定义源 5/5 | 通过；首跑失败已修复 |
| 启动 MCP | `./mcp/start.sh` | `v0.11.9`、URL、传输与鉴权、`connection.json`、55 个工具、下一步、事件日志 | 通过；不再只有一句“已启动” |
| 客户端连接 | `examples/mcp_client.py --url ... --tool openmc_environment`；重启后从 DSH 调 `openmc_environment` | 工具返回 `service.version=0.11.9`、OpenMC 0.16.0、API/可执行程序可用、核数据库状态 | 通过 |
| 自己的参数文件 | 把 `workspace/cases/user-trial/sphere-params.json` 交给智能体，`openmc_build_model` 生成 `cases/user-trial/sphere-001` | 生成摘要、XML 哈希、材料/栅元/tally 数量、`api_parsed=true`、`professional_acceptance=false`、重建命令 | 通过 |
| 校验 | `openmc_validate_inputs` 同时开 API/语义/几何检查 | 结构、语义、核素可用性、几何重叠 0、elapsed 秒数、明确“通过不等于物理正确” | 通过 |
| 提交与追踪 | `openmc_submit_job` → `openmc_job_status` | job_id、queued、进度百分比、k 均值/标准差、日志尾、输入/输出文件、进程清理结果 | 通过 |
| 结果与报告 | `openmc_read_results`、`openmc_export_report` | keff/tally、StatePoint 路径与 SHA256、report.md/图/CSV/JSON 的服务端路径 | 通过 |
| 导入已有 XML | `openmc_import_model` 导入同一套 XML 到新目录 | 文件清单、依赖、结构/API 校验、原目录未修改 | 通过 |

本轮新增/修改的人机信息：启动横幅补版本、URL、DSH 配置、连接文件和下一步；`openmc_example_status` 与 `openmc_job_status` 增加首字段 `human_summary`（例如“已完成 3/5 步；下一步 …”），让智能体和人都能先看结论再决定是否展开大字段。

仍未验证或不能由本轮证明的：全新机器上从零跑 `quickstart.sh` 的完整网络安装；在真实 DSH GUI 里手工粘贴配置（本轮用已连接的 DSH 会话和标准客户端验证）；集群调度器/HPC 提交；远程 TLS/Bearer 网关。它们按部署环境另行验收。