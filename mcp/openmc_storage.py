"""Read-only disk admission; never deletes data or promises a solver output quota."""
from __future__ import annotations
import os
from pathlib import Path
import shutil
import shlex
import stat

from openmc_runtime import Error

DEFAULT_RESERVE_BYTES = 1024 ** 3


def reserve_bytes(cfg):
    value = cfg.get('min_free_disk_bytes', DEFAULT_RESERVE_BYTES)
    if type(value) is not int or value < 0:
        raise Error('min_free_disk_bytes须为非负整数字节数；0只关闭保留空间，不关闭输入副本检查')
    return value


def check_paths(cfg):
    values = cfg.get('disk_space_check_paths', [])
    if not isinstance(values, list) or any(not isinstance(p, str) or not p.strip()
            or '\0' in p or not Path(p).is_absolute() for p in values):
        raise Error('disk_space_check_paths须为非空绝对路径字符串的数组；默认[]不检查额外卷')
    normalized = [os.path.normpath(p) for p in values]
    if len(normalized) != len(set(normalized)):
        raise Error('disk_space_check_paths不能包含重复路径')
    return normalized


def _volume_usage(path):
    try:total, used, free = shutil.disk_usage(path)
    except OSError as exc:raise Error('无法读取磁盘检查卷容量，未提交：' + str(path)) from exc
    return {'path':str(path), 'total_bytes':total, 'used_bytes':used, 'free_bytes':free}


def disk_usage(cfg, root):
    return {**_volume_usage(root),
            'min_free_disk_bytes': reserve_bytes(cfg),
            'additional_volumes': [_volume_usage(path) for path in check_paths(cfg)],
            'note': '保留空间为工程配置，不是验收指标或计算产物配额；提交检查不保证求解期间或并发下空间充足。'}


def input_copy_bytes(source, preserve_outputs, is_old_output):
    """Match copytree's per-directory ignore predicate without reading file data."""
    size = 0
    def failed(error):raise error
    for folder, directories, files in os.walk(source, onerror=failed):
        directories[:] = [name for name in directories if preserve_outputs or not is_old_output(name)]
        for name in [*directories, *files]:
            if not preserve_outputs and is_old_output(name):continue
            path = Path(folder)/name
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise Error('算例包含符号链接或特殊文件，未提交：' + str(path))
            if stat.S_ISREG(info.st_mode):size += info.st_size
    return size


def check_submission_space(source, root, cfg, preserve_outputs, is_old_output):
    try:
        snapshot = disk_usage(cfg, root)
        copied_bytes = input_copy_bytes(source, preserve_outputs, is_old_output)
    except OSError as exc:
        raise Error('无法核对工作区磁盘或输入副本大小，未提交：' + str(root)) from exc
    required = copied_bytes + snapshot['min_free_disk_bytes']
    for volume in [snapshot, *snapshot['additional_volumes']]:
        if volume['free_bytes'] >= required:continue
        raise Error('磁盘空间不足，未创建作业或启动计算；workspace=' + str(root)
                    + f"；volume={volume['path']}；free_bytes={volume['free_bytes']}；required_bytes={required}"
                    + f'（输入副本={copied_bytes}，保留空间={snapshot["min_free_disk_bytes"]}）。'
                    + '先用 ./cleanup.sh --workspace ' + shlex.quote(str(root)) + ' --older-than-days 7 预览；'
                    + '在维护窗口确认清理范围后再提交，不会自动删除旧产物。')
    return {**snapshot, 'input_copy_bytes': copied_bytes, 'required_bytes': required}
