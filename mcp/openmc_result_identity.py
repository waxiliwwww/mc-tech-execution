"""Select and verify a final StatePoint without calling job status (no recursion)."""
from pathlib import Path
from openmc_runtime import Error

def select_point(directory, record):
    import openmc_jobs as jobs
    candidates = [directory/'work'/name for name in record.get('output_files', {})
                  if name.startswith('statepoint.') and name.endswith('.h5') and '/' not in name]
    if not candidates: raise Error('作业没有statepoint产物；绘图作业请查看files，计算作业请检查日志')
    try: point = max(candidates, key=lambda p:int(p.name.split('.')[1]))
    except (IndexError,ValueError) as exc: raise Error('statepoint文件名不能识别') from exc
    if point.is_symlink(): raise Error('结果文件不能是符号链接')
    if not point.is_file() or jobs.file_hash(point) != record['output_files'][point.name]:
        raise Error('结果文件缺失或已在计算后改变；不能作为本作业原始产物读取')
    return point
