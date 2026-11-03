"""CAD→DAGMC conversion worker: runs inside the separate cadenv, no openmc dependency.

协议：stdin 读 JSON 参数；结果 JSON 写入 params['_result_path'] 指向的文件。
不经 stdout 回传 JSON，因为网格后端（Rust）可能直接向 fd 1 写进度、污染传输流；
stdout/stderr 留给父进程收集为日志。版本组合与系统库依赖见 docs/adr/0002。
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path
import sys


def _sha256(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _versions() -> dict:
    from importlib.metadata import version
    return {'cad_to_dagmc': version('cad-to-dagmc'), 'cadquery': version('cadquery'),
            'gmsh': version('gmsh')}


def execute(action: str, params: dict) -> dict:
    if action == 'probe':
        return {'cad_api': True, 'versions': _versions()}
    if action == 'inspect-step':
        import cadquery as cq
        assembly = cq.importers.importStep(str(params['step']))
        solids = assembly.solids().vals()
        try:
            bounds = assembly.val().BoundingBox()
            box = {'xmin': float(bounds.xmin), 'xmax': float(bounds.xmax),
                   'ymin': float(bounds.ymin), 'ymax': float(bounds.ymax),
                   'zmin': float(bounds.zmin), 'zmax': float(bounds.zmax)}
        except Exception:
            box = None
        return {'solids': len(solids), 'bounding_box': box,
                'length_unit_note': 'STEP内部长度通常为毫米；OpenMC使用厘米，转换时用scale_factor换算。',
                'versions': _versions()}
    if action == 'convert':
        from cad_to_dagmc import CadToDagmc
        converter = CadToDagmc()
        solids = converter.add_stp_file(str(params['step']),
                                        scale_factor=params.get('scale_factor', 1.0),
                                        material_tags=params['material_tags'])
        options = {key: params[key] for key in ('min_mesh_size', 'max_mesh_size',
                                                'implicit_complement_material_tag') if key in params}
        output = Path(converter.export_dagmc_h5m_file(str(params['output']), **options))
        return {'solids': int(solids), 'output': str(output), 'sha256': _sha256(output),
                'size_bytes': output.stat().st_size, 'source_sha256': _sha256(params['step']),
                'versions': _versions()}
    raise ValueError('Unsupported CAD action: ' + action)


if __name__ == '__main__':
    params = json.load(sys.stdin)
    result_path = Path(params.pop('_result_path'))
    try:
        with contextlib.redirect_stdout(sys.stderr):
            value = execute(sys.argv[1], params)
        payload = {'ok': True, 'result': value}
        exit_code = 0
    except Exception as error:
        payload = {'ok': False, 'error': type(error).__name__ + ': ' + str(error)[:2000]}
        exit_code = 1
    temporary = result_path.with_name(result_path.name + '.tmp')
    temporary.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    os.replace(temporary, result_path)
    sys.exit(exit_code)
