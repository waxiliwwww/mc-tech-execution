# ADR-0002 CAD→DAGMC 转换半链验证记录（RF002）

日期：2026-11-03；2026-11-04复核更正　状态：转换半链有产物证据，尚无同一自转换STEP完成输运的完整记录。

## 验证结论

STEP → .h5m的转换产物及DAGMC几何读回有记录；另有官方DAGMC例题作业b9ef8754使用现成H5M完成输运。这是不同输入的两段证据，不能合称同一自转换STEP的端到端验证。`openmc_build_dagmc_model`已有模型组装实现，具备接口不等于同链实跑完成。

## 本机实测版本组合（记录，不等于可复建的CAD依赖锁）

| 组件 | 版本 | 备注 |
|---|---|---|
| cad_to_dagmc | 0.15.1 | pip，`.runtime/cadenv`（Python 3.14.4 venv） |
| cad-to-dagmc-mesher | 0.3.7 | Rust 网格后端，**无需 pymoab** |
| cadquery / cadquery-ocp | 2.8.0 / 7.9.3.1.1 | STEP 生成与读取 |
| gmsh | 4.15.2 | 需系统库（见下） |
| OpenMC | 0.16.0（dagmc_nompi build，DAGMC 3.2.4） | `.runtime/openmc`（Python 3.12.16），读取 h5m ✓ |

**系统依赖（无 root 方案）**：gmsh 原生库缺 libGLU.so.1 / libXft.so.2 / libOpenGL.so.0，用 `apt-get download` + `dpkg -x` 本地解包到 `.runtime/glu/extracted/`，运行 cadenv 需 `LD_LIBRARY_PATH=<repo>/.runtime/glu/extracted/usr/lib/x86_64-linux-gnu`。sudo apt 安装（libglu1-mesa libxft2 libopengl0）是等价的正式环境做法。

## 调用方式（实测有效）

```python
from cad_to_dagmc import CadToDagmc
c = CadToDagmc()
c.add_stp_file("model.step", material_tags=["steel"])  # 每实体一个标签；字符串形式仅收 'assembly_materials'/'assembly_names'
c.export_dagmc_h5m_file("dagmc.h5m")                   # 默认走 cad-to-dagmc-mesher 后端
```

坑记录：① `cq.exporters.export(shape, path, "STEP")` 参数序是 path 在前、类型在后；② material_tags 传普通字符串会报 ValueError，必须按实体列表；③ 类方法是 `add_stp_file`/`export_dagmc_h5m_file`，不是 import_*/export_dagmc。

## 证据

- 输入：cube.step（`cq.box(10,10,10)`，按CadQuery/STEP的mm尺寸解释，15426 B）
- 输出：cube.h5m（36808 B，sha256 `fa15c4fdc2c68a3d2bc555a8b96b50a1a8c94a9dfc62b575aa55b240093b00cf`）
- 引擎读取：`openmc.DAGMCUniverse('/tmp/cube.h5m')` 实例化 OK，bounding_box = (-5,-5,-5)~(5,5,5)，为未缩放坐标读回；不能据此把原STEP的mm误称为cm。工具测试另以scale_factor=0.1将10mm转换为1cm
- 复现脚本与产物：`artifacts/runs/20261103-cad-dagmc-chain/`（reproduce.sh + cube.step + cube.h5m）

## 当时安排及当前范围

1. 工具层封装（需求 A5）：`openmc_convert_cad` 工具——STEP 进、h5m 出 + `<dagmc>` geometry 生成（volume id 指派材料、graveyard/implicit complement），走独立 cadenv（LD_LIBRARY_PATH 固化进封装）。
2. 使用同一STEP经转换、模型组装后求解，并按实际样例检查粒子丢失及结果。原记录中的“1000粒子/lost=0”是工程检查建议，未见于原技术规格书或甲方三页指引，不作为新增合同验收承诺。
3. 复杂装配体（多实体、imprint='full'）与网格质量参数（min/max_mesh_size）留待真实样例驱动。

## 补记（2026-11-03 当日）

工具层封装已落地（0.9.0）：`openmc_inspect_step` / `openmc_convert_cad` 两工具，转换经独立 cadenv 子进程桥（结果文件回传，因 Rust 网格后端可能直写 stdout）；`cad_python_command`/`cad_library_path`/`cad_timeout_seconds` 入部署配置，LD_LIBRARY_PATH 固化在配置项而非人工环境。本 ADR"下一步"第 1 条完成；第2、3条仍需按本页复核后的范围处理。快速安装未自动安装CAD环境，当前部署需单独准备cadenv及原生库配置。
