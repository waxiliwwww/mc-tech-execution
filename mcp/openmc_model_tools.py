"""Transactional model-spec adapter shared by direct CLI and standard MCP."""
from __future__ import annotations
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

from openmc_model_spec import MODEL_SCHEMA, validate_spec
from openmc_import import publish_directory
from openmc_runtime import Error, ROOT, api, atomic_json, config, file_location, path_in_workspace


def model_capabilities() -> dict:
    return {'schema_version':'1','max_tally_values':1000000,'spec_schema':deepcopy(MODEL_SCHEMA),
            'supported':['explicit_nuclide_materials','CSG_planes_cylinders_spheres','boolean_regions',
                         'universes','2d_rectangular_lattices','point_box_neutron_sources',
                         'eigenvalue_fixed_source','energy_material_mesh_tallies'],
            'unsupported':['arbitrary_python_execution','CAD_in_model_spec','hexagonal_lattice_in_model_spec',
                           'automatic_physical_design','fine_tuned_model_generation'],
            'note':'宿主模型可按schema组合新输入；工具不猜材料与物理参数。生成成功不代表几何、核数据或专业计算正确。'}


def build_model(case_dir: str, spec: dict) -> dict:
    try:validate_spec(spec)
    except (ValueError,TypeError,KeyError) as exc:raise Error('model-spec校验失败：'+str(exc)) from exc
    target=path_in_workspace(case_dir,exists=False)
    if target.exists():raise Error('目标算例目录已存在；请选择新目录')
    target.parent.mkdir(parents=True,exist_ok=True)
    staging=Path(tempfile.mkdtemp(prefix='.building-',dir=target.parent))
    try:
        atomic_json(staging/'model-spec.json',spec)
        # The saved helper is executable with only OpenMC and stdlib; no repo path.
        shutil.copyfile(Path(__file__).resolve().parent/'openmc_model_spec.py',staging/'build_model.py')
        value=api('build-model',staging,{'spec':spec},config())
        source_hash=hashlib.sha256((staging/'model-spec.json').read_bytes()).hexdigest()
        # The trusted builder has written the marker alongside XML/spec, including
        # API version and builder identity. Keep that single source of provenance.
        if target.exists():raise Error('目标算例目录已出现；不覆盖并发创建的输入')
        publish_directory(staging,target)
    except Exception:
        # This directory was created exclusively by this attempt and never published.
        shutil.rmtree(staging,ignore_errors=True)
        raise
    return {'case_dir':case_dir,**value,**file_location(target),'generation_method':'declarative_public_python_api',
            'fine_tuned_model_used':False,'professional_acceptance':False,'model_spec_sha256':source_hash,
            'rebuild_command':'在算例目录运行：<OpenMC Python> build_model.py',
            'note':'已生成并通过API读回；提交前仍应预览、语义/几何校核。不是微调模型生成。'}


MODEL_TOOLS=[
    {'name':'openmc_model_capabilities','description':'发现受控组合建模范围和完整参数schema；不是OpenMC全部API覆盖声明。',
     'inputSchema':{'type':'object','properties':{},'additionalProperties':False},'outputSchema':{'type':'object'},'function':model_capabilities,
     'annotations':{'readOnlyHint':True,'idempotentHint':True,'openWorldHint':False}},
    {'name':'openmc_build_model','description':'按结构化模型参数组合新模型，经OpenMC Python API导出XML并读回；保留可重跑Python源码。无任意代码执行，不代表物理正确或微调生成。',
     'inputSchema':{'type':'object','properties':{
         'case_dir':{'type':'string','description':'MC_WORKSPACE内新算例相对目录；拒绝覆盖、越界和符号链接。'},
         'spec':deepcopy(MODEL_SCHEMA)},'required':['case_dir','spec'],'additionalProperties':False},
     'outputSchema':{'type':'object'},'function':build_model}]
