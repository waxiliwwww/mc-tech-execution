"""Read the shipped Skill through ordinary MCP tools/resources, without host writes."""
from hashlib import sha256
import sysconfig
from pathlib import Path
from adapters import ToolExecutionError

FILES = {'start': 'SKILL.md', 'files': 'references/file-transfer.md',
         'advanced': 'references/extended-tasks.md'}
PREFIX = 'skill://openmc-workflows/'


def skill_root():
    root = Path(__file__).resolve().parent.parent
    # Source archive, pip --target, and normal prefix installations respectively.
    relative = Path('share/mc-agent-tools/skills/openmc-workflows')
    for candidate in (root/'skills/openmc-workflows', root/relative,
                      Path(sysconfig.get_path('data'))/relative):
        if (candidate/'SKILL.md').is_file():
            return candidate
    raise ToolExecutionError('交付包缺少openmc-workflows技能文件；请补齐同版源码包或重新安装，不影响已有作业记录。')


def get_workflow(chapter='start'):
    if chapter not in FILES:
        raise ToolExecutionError('未知指引章节；请选择start、files或advanced。')
    relative = FILES[chapter]
    try:
        data = (skill_root()/relative).read_bytes()
    except OSError as error:
        raise ToolExecutionError('无法读取随包任务指引：'+relative+'；请检查安装文件完整性。') from error
    return {'chapter': chapter, 'uri': PREFIX+relative, 'mime_type': 'text/markdown',
            'content': data.decode('utf-8'), 'sha256': sha256(data).hexdigest(),
            'bytes': len(data),
            'chapters': [{'chapter': key, 'file': value, 'uri': PREFIX+value} for key,value in FILES.items()],
            'note': '这是任务指引，不是计算或专业验收结果；相对参考用同一工具的files/advanced章节读取。'}


def server_instructions():
    """Put the canonical minimal workflow in the first model input, not just a hint to fetch it."""
    skill = get_workflow('start')['content']
    _, marker, rest = skill.partition('## 连接时速查\n')
    core = rest.split('\n## ', 1)[0].strip()
    if not marker or not core:
        raise ToolExecutionError('技能缺少连接时速查章节；请恢复同版Skill，未发布不完整连接指引。')
    return ('OpenMC Forge任务指引（核心内容已随连接提供，无需先安装Skill）：\n'+core+
            '\n完整说明按任务调用openmc_get_workflow(chapter="start")，文件交接读files，扩展任务读advanced。'
            '工具名前缀以宿主实际发现为准；阅读不授权扩大计算或修改配置。')


def read_resource(uri):
    for key, value in FILES.items():
        if uri == PREFIX+value:
            return get_workflow(key)['content']
    raise ValueError('未知技能资源；先读取resources/list，不接受任意文件路径。')


RESOURCES = [{'uri': PREFIX+value, 'name': 'OpenMC工作流：'+key,
              'description': '随服务发布的任务指引；普通MCP资源，不声明官方Skills扩展。',
              'mimeType': 'text/markdown'} for key,value in FILES.items()]

WORKFLOW_TOOLS = [{'name': 'openmc_get_workflow',
    'description': '连接已提供核心工作流；需要完整任务说明时读取start，异机传文件读files，扫描/CAD/燃耗/Slurm/修复读advanced。与随包Skill同源，无需客户端安装；不执行计算或修改配置。',
    'inputSchema': {'type':'object','properties':{'chapter':{'type':'string','enum':list(FILES),
        'default':'start','description':'start：任务选择与主流程；files：宿主/服务端文件交接；advanced：扩展任务与修复。'}},'additionalProperties':False},
    'outputSchema': {'type':'object'}, 'function':get_workflow,
    'annotations':{'readOnlyHint':True,'idempotentHint':True,'openWorldHint':False}}]
