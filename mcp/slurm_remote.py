"""Small stdlib-only helper sent to the login node, never installed as a daemon.

All commands are administrator-configured argv; model files are data, not code.
The same helper runs in local/login-node and SSH transports.
"""
import base64
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''): h.update(chunk)
    return h.hexdigest()


def files(root):
    result = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink(): raise ValueError('远端目录含符号链接：' + str(path.relative_to(root)))
        if path.is_file():
            result[path.relative_to(root).as_posix()] = {'sha256': digest(path), 'bytes': path.stat().st_size}
        elif not path.is_dir(): raise ValueError('远端目录含特殊文件')
    return result


def execute(action, p):
    root = Path(p['directory'])
    if action == 'stage':
        root.parent.mkdir(parents=True, exist_ok=True)
        if root.exists():
            manifest = json.loads((root / '.forge-input.json').read_text())
            if manifest != p['inputs']: raise ValueError('远端已有不同输入，不覆盖')
            for name, expected in manifest.items():
                if digest(root / name) != expected: raise ValueError('远端输入已变化，不覆盖：' + name)
            return {'staged': True, 'reused': True}
        stage = Path(tempfile.mkdtemp(prefix='.forge-upload-', dir=root.parent))
        try:
            with tarfile.open(fileobj=sys.stdin.buffer, mode='r|') as tar:
                seen = set()
                for entry in tar:
                    if entry.name not in p['inputs'] or entry.name in seen or not entry.isfile():
                        raise ValueError('上传清单外或重复/非普通文件：' + entry.name)
                    target = stage / entry.name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with tar.extractfile(entry) as src, target.open('wb') as dest: shutil.copyfileobj(src, dest)
                    if digest(target) != p['inputs'][entry.name]: raise ValueError('上传指纹不一致')
                    seen.add(entry.name)
            if seen != set(p['inputs']): raise ValueError('上传不完整，未发布')
            (stage / '.forge-input.json').write_text(json.dumps(p['inputs']))
            # Unique UUID directory: a concurrently existing target is an error.
            if root.exists(): raise ValueError('远端目标并发出现，未覆盖')
            os.rename(stage, root)
        finally:
            if stage.exists(): shutil.rmtree(stage)
        return {'staged': True, 'reused': False}
    if action == 'command':
        run = subprocess.run(p['argv'], cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             timeout=p['timeout'])
        if len(run.stdout) + len(run.stderr) > 2 * 1024 * 1024: raise ValueError('调度命令响应过大；缩小查询范围')
        return {'returncode': run.returncode, 'stdout': run.stdout.decode(errors='replace'),
                'stderr': run.stderr.decode(errors='replace')}
    if action == 'solve':
        before = json.loads((root / '.forge-input.json').read_text())
        for name, expected in before.items():
            if digest(root / name) != expected: raise ValueError('计算前输入指纹不一致：' + name)
        version = subprocess.run(p['argv'][:-2]+['--version'],cwd=root,capture_output=True,text=True,timeout=30)
        runtime = {'openmc_version_output':(version.stdout+version.stderr).strip()[:2000],
                   'expected_version':p['expected_version'],'python_version':sys.version,
                   'nuclear_data_verified':False}
        if version.returncode or not re.search(r'(?<![0-9.])'+re.escape(p['expected_version'])+r'(?![0-9.])',runtime['openmc_version_output']):
            (root/'solver.log').write_text('远端OpenMC版本不符或无法核实：'+runtime['openmc_version_output'])
            raise ValueError('远端OpenMC版本不符或无法核实，未启动求解')
        truncated = False
        with (root / 'solver.log').open('wb') as log:
            run = subprocess.Popen(p['argv'], cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            written = 0
            for chunk in iter(lambda: run.stdout.read(65536), b''):
                allowed = max(0, p['max_log_bytes'] - written)
                log.write(chunk[:allowed]); written += min(allowed, len(chunk))
                truncated = truncated or len(chunk) > allowed
            code = run.wait()
        after = files(root)
        changed = [name for name, expected in before.items() if after.get(name, {}).get('sha256') != expected]
        outputs = {name: item for name, item in after.items()
                   if name not in before and not name.startswith('.forge-') and not (name.startswith('slurm-') and name.endswith('.out'))}
        receipt = {'exit_code': code, 'input_hashes': before, 'changed_inputs': changed,
                   'outputs': outputs, 'log_truncated': truncated, 'finished_at': time.time(),'runtime':runtime}
        temporary = root / '.forge-result.tmp'
        temporary.write_text(json.dumps(receipt))
        os.replace(temporary, root / '.forge-result.json')
        return {'solver_exit_code': code, 'changed_inputs': changed}
    if action == 'inventory':
        receipt_path = root / '.forge-result.json'
        current = files(root)
        if receipt_path.is_file():
            receipt = json.loads(receipt_path.read_text())
        elif p.get('allow_failed'):
            receipt = {'exit_code':p['exit_code'], 'input_hashes':json.loads((root/'.forge-input.json').read_text()),
                       'changed_inputs':[], 'outputs':{name:item for name,item in current.items()
                         if name not in p['inputs'] and not name.startswith('.forge-')},
                       'finished_at':time.time(),'log_truncated':False,
                       'receipt_source':'collection_after_failed_scheduler_terminal_no_solver_receipt'}
        else: raise ValueError('远端没有计算完成收据；查队列/日志，不把排队结束当计算成功')
        for name, expected in receipt['input_hashes'].items():
            if current.get(name, {}).get('sha256') != expected: raise ValueError('远端输入已变化：' + name)
        for name, item in receipt['outputs'].items():
            if current.get(name) != item: raise ValueError('远端原始结果已变化：' + name)
        if receipt['changed_inputs']: raise ValueError('求解器运行期间修改了输入')
        # Scheduler stdout may be flushed after the solver seals its outputs.
        # It is collected at the confirmed scheduler terminal, not solver-sealed.
        for name,item in current.items():
            if name.startswith('slurm-') and name.endswith('.out'):receipt['outputs'][name]=item
        return receipt
    if action == 'fetch':
        # Caller rechecks sizes and SHA256 after transport; never tar.extractall.
        with tarfile.open(fileobj=sys.stdout.buffer, mode='w|') as tar:
            for name in p['names']:
                path = root / name
                if path.is_symlink() or not path.is_file(): raise ValueError('结果文件不可取回：' + name)
                tar.add(path, arcname=name, recursive=False)
        return None
    raise ValueError('未知远端动作')


def main():
    try:
        action = sys.argv[1]
        payload = json.loads(base64.b64decode(sys.argv[2]))
        value = execute(action, payload)
        if value is not None: print(json.dumps(value, ensure_ascii=False))
        if action == 'solve': return value['solver_exit_code'] if value['solver_exit_code'] >= 0 else 1
        return 0
    except Exception as exc:
        print(type(exc).__name__ + ': ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__': sys.exit(main())
