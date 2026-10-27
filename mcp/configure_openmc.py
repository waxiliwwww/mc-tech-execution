#!/usr/bin/env python3
"""Configure an existing Conda/micromamba prefix without changing the global environment."""
import argparse
import json
import os
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--prefix', required=True, help='已安装OpenMC的Conda/micromamba环境目录')
args = parser.parse_args()
prefix = Path(args.prefix).resolve()
config = json.loads((root / 'config/openmc.json').read_text())
target = root / 'config/openmc.local.json'
if target.exists():config.update(json.loads(target.read_text()))
python = prefix / 'bin/python'
solver = prefix / 'bin/openmc'
try:
    api_version = subprocess.check_output([str(python), '-c', 'import openmc; print(openmc.__version__)'], text=True, timeout=30).strip()
    solver_version = subprocess.check_output([str(solver), '--version'], text=True, timeout=30)
    if api_version != config['expected_version'] or config['expected_version'] not in solver_version:
        raise ValueError('OpenMC版本不符；本轮要求' + config['expected_version'])
except (OSError, subprocess.SubprocessError, ValueError) as error:
    raise SystemExit('配置未修改：' + str(error))
config.update(python_command=[str(python)], openmc_command=[str(solver)])
target.write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n')
os.chmod(target, 0o600)
print('已配置OpenMC ' + api_version + '：' + str(target))
print('下一步：./mc.sh call openmc_environment')
