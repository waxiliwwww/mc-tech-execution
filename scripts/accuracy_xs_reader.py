#!/usr/bin/env python3
"""读取MGXS HDF5文件并派生齐次反射无泄漏系统的k∞=ΣΣνΣf/ΣΣa。

只做数据读取与求和，不做物理判断：多材料/多温度文件直接拒绝并说明，
调用方（accuracy_baseline.py）据返回值判定是否可用于解析参考。
"""
import json
from pathlib import Path
import sys

import h5py


def main() -> int:
    path = Path(sys.argv[1])
    with h5py.File(path, 'r') as handle:
        materials = [key for key in handle.keys() if key not in ('kTs',) and not key.startswith('_')]
        if len(materials) != 1:
            raise SystemExit('截面文件材料数不是1，无法按齐次单材料解析参考：' + ', '.join(materials)
                             + '；请为该基准提供常数参考值')
        material = handle[materials[0]]
        temperatures = [key for key in material.keys() if key not in ('kTs',)]
        if len(temperatures) != 1:
            raise SystemExit('温度组数不是1（' + ', '.join(temperatures) + '），解析参考不支持多温度文件')
        group = material[temperatures[0]]
        for name in ('absorption', 'nu-fission'):
            if name not in group:
                raise SystemExit('截面文件缺少' + name + '数据集')
        absorption = float(group['absorption'][...].sum())
        nu_fission = float(group['nu-fission'][...].sum())
        groups = int(group['absorption'].size)
        if absorption <= 0:
            raise SystemExit('Σa非正，不能派生k∞')
    print(json.dumps({'material': materials[0], 'temperature': temperatures[0],
                      'groups': groups, 'absorption': absorption,
                      'nu_fission': nu_fission, 'k_inf': nu_fission / absorption},
                     ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())