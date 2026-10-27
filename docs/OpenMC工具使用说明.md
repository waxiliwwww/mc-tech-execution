# OpenMC工具使用说明

面向调用开发者和联调人员。工具包封装OpenMC公开API和命令行；支持直接CLI及标准MCP，二者使用相同实现和参数检查。无需先了解反应堆建模即可运行合成示例；正式模型仍需专业人员提供或审定输入与结果。

## 1. 先配置运行时与工作区

先执行根目录`./quickstart.sh`；已有OpenMC环境可指定`--openmc-prefix`。也可手工准备后运行`python3 mcp/configure_openmc.py --prefix 环境目录`，该脚本保留已有本机其他设置。配置含：

| 配置 | 用途 |
| --- | --- |
| runtime | 首版为native；在Linux/WSL直接运行，也可在已有容器内以native方式部署整个服务 |
| expected_version | 本轮0.16.0；实际API及求解器须匹配 |
| python_command | 安装了OpenMC API的Python命令数组 |
| openmc_command | OpenMC可执行命令数组，允许管理员配置固定包装器；不接受模型任意传入命令 |
| api_timeout_seconds | 短API调用的工程超时设置 |
| job_timeout_seconds | 本地作业工程运行时限，按部署和作业调整；不是新增合同性能指标 |
| max_log_bytes | 每作业保存的日志上限，超限仍排空管道并返回截断标志 |
| max_scan_cases | 单次扫描组合数上限，默认100；提交前检查，可按实际资源修改 |
| max_plot_points | 单张图点数上限，默认20000；超限要求缩小filter选择，禁止静默截断 |
| max_report_rows | Word/PDF的计分行数上限，默认1000；截断明确说明，原始StatePoint保留 |
| office_command | PDF/Word97 DOC转换程序参数数组；Quick Start设置，不接受模型传入任意程序 |
| report_timeout_seconds | 转换工程超时，默认120秒，可调整；不是合同承诺 |
| font_path | 可选CJK字体文件，图表中文标题使用 |

`MC_WORKSPACE`指定业务工作目录，默认项目`workspace/`。工具参数的文件路径相对该目录；不接受绝对路径、上级路径或符号链接。CLI的`--workspace`只影响本次直接调用，MCP客户端不能以此修改服务端根目录。

## 2. 工具清单

实时定义以`./mc.sh list`或MCP `tools/list`为准。下表中的默认参数是工程使用设置，可按需要调整，不是验收门槛。

| 工具 | 参数 | 输出与用途 |
| --- | --- | --- |
| openmc_environment | 无 | API/求解器可用性、实际版本及匹配状态；核数据配置状态单独呈现 |
| openmc_create_model | case_dir；template默认synthetic-mgxs；particles=200、batches=10、inactive=2 | 在新目录生成真实XML/合成核数据或官方pin-cell模型，不覆盖已有目录；返回来源和文件 |
| openmc_validate_inputs | case_dir；api_check=false | XML结构及基础运行参数检查；true再通过官方Model API读取，返回具体检查层次 |
| openmc_submit_job | case_dir；mode=run/plot/geometry-debug；threads=1 | 快照输入并返回job_id；mode分别对应普通计算、-p绘图、-g几何调试 |
| openmc_job_status | job_id | queued/running/completed/failed/cancelled/interrupted、日志尾部、退出码、本作业输出文件 |
| openmc_cancel_job | job_id | 请求取消；查询到cancelled确认完成；重复取消不重新执行 |
| openmc_read_results | job_id；max_values=1000，可调1..10000 | 仅读取成功计算的本作业statepoint，返回keff均值/不确定度及tally的scores、nuclides、filters、shape、mean/std_dev |
| openmc_export_report | job_id；format=markdown/csv/docx/pdf/doc | 单次计算报告，PDF/DOC依赖LibreOffice，DOC为二进制Word97格式 |
| openmc_list_parameters | case_dir | 列出本模型已适配参数、对象ID、当前值、单位和范围 |
| openmc_prepare_case | case_dir、target_dir、parameters | 复制并修改新算例，不能覆盖原目录；仅修改指定XML字段 |
| openmc_submit_scan | case_dir、axes；threads=1、stop_on_error=false | 笛卡尔组合串行运行，返回scan_id；默认失败后保留失败项并继续 |
| openmc_scan_status | scan_id | 参数、逐组作业编号与结果、状态计数；服务重启后可查 |
| openmc_cancel_scan | scan_id | 取消当前计算并阻止后续提交；需查询终态确认 |
| openmc_plot_results | job_id；tally_id、score、nuclide、filter_selection；style=line/bar；format=png/svg、title | 读取完整StatePoint选定数据，图像与数据JSON/CSV一起生成；默认首个tally/score及total核素 |
| openmc_plot_scan | scan_id、x_parameter；fixed、metric=keff/tally、bin_index=0及上述数据选择 | 生成扫描曲线；多维扫描固定其他轴；失败/缺失留缺口 |
| openmc_export_scan_report | scan_id；format=markdown/csv/docx/pdf/doc | 全组合状态/数值报告，纳入最近生成的PNG扫描曲线 |
| openmc_list_artifacts | kind=job/scan、identifier | 本作业/扫描的服务端产物清单、大小、MIME和SHA256，无下载接口 |

XML检查不等于OpenMC所有规范校核；API可读取不等于几何无重叠或物理正确。未知业务约束不作通过判定。`synthetic-mgxs`是合成单群数据，`pwr-pin-cell`调用官方`openmc.examples.pwr_pin_cell()`且需要相应真实核数据。

## 3. 直接CLI逐步调用

项目根目录执行，无需模型密钥或MCP服务：

```bash
./mc.sh call openmc_environment
./mc.sh call openmc_create_model '{"case_dir":"demo/first","template":"synthetic-mgxs"}'
./mc.sh call openmc_validate_inputs '{"case_dir":"demo/first","api_check":true}'
./mc.sh call openmc_submit_job '{"case_dir":"demo/first","threads":1}'
```

从返回值复制实际作业编号，替换下面的`实际作业编号`：

```bash
./mc.sh call openmc_job_status '{"job_id":"实际作业编号"}'
./mc.sh call openmc_read_results '{"job_id":"实际作业编号"}'
./mc.sh call openmc_export_report '{"job_id":"实际作业编号","format":"markdown"}'
./mc.sh call openmc_export_report '{"job_id":"实际作业编号","format":"csv"}'
```

只有状态为completed才读取计算结果。图像作业使用`mode:"plot"`提交，完成后查看files中的PNG/PPM等产物；绘图作业不返回计算statepoint。几何调试使用`mode:"geometry-debug"`，依赖实际程序和对应输入，不以基础XML检查代替。

如需取消：

```bash
./mc.sh call openmc_cancel_job '{"job_id":"实际作业编号"}'
```

同一个case_dir再次创建会拒绝覆盖；可换一个新目录。作业提交会创建新作业，重试提交不是查询；客户端拿到job_id后应继续查询该编号，避免重复计算。

## 4. MCP与第三方调用

启动`./mcp/start.sh`，客户端按Streamable HTTP接入配置的MCP URL，使用`tools/list`发现及`tools/call`调用。参数与CLI一致。例如调用工具`openmc_submit_job`，参数对象为`{"case_dir":"demo/first","threads":1}`。

标准Python MCP客户端可直接运行：

```bash
mcp/.venv/bin/python examples/openmc_demo.py --mcp-url http://127.0.0.1:8770/mcp
```

这是可执行的客户端示例源码，可供下游复用。远程Bearer令牌从客户端环境变量`MCP_AUTH_TOKEN`读取，不写入脚本。`/info`方便人阅读，不取代标准MCP发现。

输入与产物位于MCP服务器工作区；返回路径不代表文件已下载到调用者机器。需要远程文件传输时使用项目认可的文件共享/传输方式。工具包提供结构化结果，基座如何编排和展示由基座接口适配处理。

## 5. 结果与来源

- `data_kind=synthetic_mgxs`：合成截面，真实求解器运行仍不是实际堆芯物理验证。
- `execution_kind=mock_process_fixture`：仅进程控制测试，不进行蒙卡计算；不会作为正常运行的自动后备。
- `public_example_requires_nuclear_data`：公开模型样例，正式运行需要指定真实核数据库。
- `professional_acceptance=false`：本工具没有作专业验收判定。

keff保留不确定度；遇到非有限统计值时返回null并给出warnings，不填成0；tally采用官方`mean/std_dev`，不把HDF5累计和直接作为均值。原始计分没有自动按功率/源强重新归一化。结果包含shape与过滤器信息；超过max_values时有`values_truncated=true`，不能据截断片段作完整分析。CSV每行保留shape、截断和归一化提示，完整元数据见同作业results.json。

每次提交在`workspace/.mc-jobs/作业编号/`中保存记录、输入快照、solver.log及结果/报告。旧statepoint、summary、图像等运行产物不复制为新作业输入；首版不支持拿旧statepoint直接重启计算。输出记录包含哈希，读取时核对，避免把旧结果或后改文件归到本作业。

## 6. 失败、重启与资源

程序缺失/版本不符会阻止计算提交；坏输入有问题清单；非零退出与超时为failed，日志可查询。取消请求与取消完成分开。MCP/CLI重启不自动重提作业；持久记录与独立监控进程支持继续查询。监控进程丢失时状态为interrupted，不推断计算成功；本轮没有自动续算承诺。

作业结果会累积。确认不再需要且作业已结束后，由部署人员归档/清理对应作业目录；清理会失去该编号的查询和读取能力。不要在运行中删除或移动目录。

原7项日志/修复/知识/基准目录接口仍保留。它们的专业规则和实际基准接入状态见开发计划；不能把目录存在或字符串修改成功当作专业业务完成。

## 7. 参数扫描与结果图表

先运行完整示例：`mcp/.venv/bin/python examples/scan_report_demo.py`，也可加`--mcp-url`使用同一工具。示例中的macro密度是宏观截面倍率，不是g/cm³；核数据/几何适用性仍需专业核对。

逐步操作（编号从实际返回值复制）：

```bash
./mc.sh call openmc_create_model '{"case_dir":"demo/scan"}'
./mc.sh call openmc_list_parameters '{"case_dir":"demo/scan"}'
./mc.sh call openmc_submit_scan '{"case_dir":"demo/scan","axes":{"materials.1.density":[0.8,1.0,1.2]}}'
./mc.sh call openmc_scan_status '{"scan_id":"实际扫描编号"}'
./mc.sh call openmc_plot_scan '{"scan_id":"实际扫描编号","x_parameter":"materials.1.density","metric":"tally","score":"flux"}'
./mc.sh call openmc_export_scan_report '{"scan_id":"实际扫描编号","format":"pdf"}'
./mc.sh call openmc_list_artifacts '{"kind":"scan","identifier":"实际扫描编号"}'
```

`materials.1`只对应这个示例，请用参数发现返回的真实名称，不猜业务模型ID。支持运行设置particles/batches/inactive/generations_per_batch/seed、材料density与temperature、已适配CSG平面/球/柱/锥的系数、独立源strength。密度保留原单位，sum单位不提供直接密度修改；温度须有适用核数据。未知参数拒绝，原始XML其他字段保持。未适配参数不表示OpenMC不支持，后续按真实需求增加明确映射，不接受任意属性路径执行。

axes每轴是非空数值列表，全组合先检查再提交；默认上限是工程保护设置，可在配置调整。更改颗粒数、批次或seed属于统计设置比较，不当作物理响应验证。几何系数满足数值范围也不表示模型有效，运行失败仍如实保存。

多轴扫描如密度×seed，画一条密度曲线须传`fixed:{"settings.seed":1}`。`metric:"tally"`可指定tally_id/score/nuclide/filter_selection/bin_index，`keff`保留其不确定度。所选计分采用官方Tally.get_values读取完整StatePoint，不使用read_results的截断预览。filter_selection例如`[{"type":"EnergyFilter","bins":[[0.0,625.0]]}]`；需与实际过滤箱完全匹配，可用类型和单位以实际结果为准。bin_index是选择后按OpenMC顺序展开的下标；当前图为曲线/柱图，不擅自插值成二维物理分布。

图像、同名JSON与CSV保留选择条件、数据来源和原始归一化方式。缺失统计量为null，误差线不可用时说明；失败行不填0，不连过缺口。PNG经MCP返回标准ImageContent预览（不超过2MiB时），宿主是否显示取决于其图像支持；SVG及所有文件仍保存服务端。

## 8. 报告与服务端文件

单作业用openmc_export_report，扫描用openmc_export_scan_report，支持markdown/csv/docx/pdf/doc。Word/PDF包含来源、状态、keff及计分，DOC为真正Word97复合文件，不是重命名DOCX。原始归一化、统计不足与报告截断均明确说明。扫描报告包含全部组合；单作业计分超过行数限制时参考results.json及原始StatePoint。重复导出同一格式会覆盖该作业/扫描的同名report文件；独立图像文件使用不同名称。

扫描目录`workspace/.mc-scans/<scan_id>/`包含基线/各组输入、持久记录、summary.json/csv、曲线及report文件；每次真实计算仍在`.mc-jobs/<job_id>/`。openmc_list_artifacts只列已生成业务产物，不列运行配置和内部控制记录；原始输出给出matches_recorded_output便于发现事后改动。这里是服务端存储清单，不是下载授权或客户端路径。部署人员安排磁盘容量、归档与删除，运行中不能移动或清理目录。

扫描取消后尚未运行项为not_run；stop_on_error为true时首个失败即不再提交下一组，默认false继续并最终partial/failed。MCP服务退出不等于取消扫描。扫描监控被终止则interrupted，不自动重提；查询active_job_id并取消该计算，再以新scan_id重新提交需要的范围。结果保留原批次，不能把重跑记录覆盖为首次成功。
