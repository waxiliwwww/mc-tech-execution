"""CAD worker进程fixture：只做协议与边界验证；不做真实网格化，不产有效h5m。

动作取最后一个参数（argv[-1]），与真实worker的[python, cad_worker.py, action]布局兼容：
测试配置cad_python_command=[python, mock_cad.py]时，argv=[python, mock, cad_worker路径, action]。
模式经环境变量MOCK_CAD_MODE控制：success（默认）/fail（结果文件报失败）/crash（不留结果文件）。
"""
import json
import os
from pathlib import Path
import sys

params = json.load(sys.stdin)
result_path = Path(params.pop('_result_path'))
action = sys.argv[-1]
mode = os.environ.get('MOCK_CAD_MODE', 'success')
print('MOCK CAD FIXTURE; no real meshing', flush=True)
if mode == 'crash':
    print('fixture intentional crash detail', file=sys.stderr)
    raise SystemExit(3)
if mode == 'fail':
    payload = {'ok': False, 'error': 'ValueError: fixture intentional failure'}
elif action == 'probe':
    payload = {'ok': True, 'result': {'cad_api': True, 'versions': {'cad_to_dagmc': 'mock'}}}
elif action == 'inspect-step':
    payload = {'ok': True, 'result': {
        'solids': 2,
        'bounding_box': {'xmin': 0.0, 'xmax': 10.0, 'ymin': 0.0, 'ymax': 10.0, 'zmin': 0.0, 'zmax': 10.0},
        'length_unit_note': 'STEP内部长度通常为毫米；OpenMC使用厘米。',
        'versions': {'cad_to_dagmc': 'mock', 'cadquery': 'mock', 'gmsh': 'mock'}}}
elif action == 'convert':
    output = Path(params['output'])
    output.write_bytes(b'MOCK-H5M-NOT-PHYSICS')
    tags = params['material_tags']
    payload = {'ok': True, 'result': {
        'solids': len(tags) if isinstance(tags, list) else 1,
        'output': str(output), 'sha256': '0' * 64,
        'size_bytes': output.stat().st_size, 'source_sha256': '1' * 64,
        'versions': {'cad_to_dagmc': 'mock', 'cadquery': 'mock', 'gmsh': 'mock'}}}
else:
    payload = {'ok': False, 'error': 'ValueError: Unsupported CAD action: ' + str(action)}
result_path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
raise SystemExit(0 if payload.get('ok') else 1)
