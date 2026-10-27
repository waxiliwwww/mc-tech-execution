#!/usr/bin/env python3
"""Create an independent service without installing dependencies or starting it."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = 'mcp-toolkit-source-0.2.0.tar.gz'


def create(destination, language):
    archive = ROOT / 'vendor' / ARCHIVE
    manifest = json.loads((ROOT / 'vendor/mcp-toolkit-MANIFEST.json').read_text())
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if digest != manifest['artifacts'][ARCHIVE]:raise ValueError('模板快照哈希不符，未创建；请重新取得完整工具包')
    if destination.exists() or destination.is_symlink():raise ValueError('目标已存在，拒绝覆盖；请选择一个新目录')
    if not destination.parent.is_dir():raise ValueError('目标父目录不存在；先创建父目录，再选择新服务目录')
    files = {}
    prefix = ('mcp-toolkit', language)
    with tarfile.open(archive) as bundle:
        for member in bundle.getmembers():
            path = PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts or member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
                raise ValueError('模板包含不安全归档项，未创建：' + member.name)
            if path.parts[:2] != prefix or not member.isfile():continue
            relative = Path(*path.parts[2:])
            if relative in files:raise ValueError('模板包含重复文件，未创建')
            files[relative] = (bundle.extractfile(member).read(), member.mode)
    required = {'start.sh', 'service.config.json', 'tools.py' if language == 'python' else 'src/tools.ts'}
    if not required <= {str(path) for path in files}:raise ValueError('模板缺少可编辑源码，未创建')
    if language == 'typescript':
        source, mode = files[Path('src/tools.ts')]
        replacements = {
            b'const inputSchema = { a: z.number(), b: z.number() };':
                'const inputSchema = { a: z.number().describe("第一个有限加数"), b: z.number().describe("第二个有限加数") };'.encode(),
            b'const outputSchema = { sum: z.number() };':
                'const outputSchema = { sum: z.number().describe("两个加数的和") };'.encode(),
        }
        for before, after in replacements.items():
            if source.count(before) != 1:raise ValueError('固定模板结构不符，未创建；请核对模板版本')
            source = source.replace(before, after)
        files[Path('src/tools.ts')] = (source, mode)
    # Atomic mkdir prevents an existing empty directory from being replaced too.
    # A filesystem failure leaves the new partial folder for inspection; no retry
    # or cleanup can delete someone else's files in that folder.
    destination.mkdir()
    for relative, (data, mode) in files.items():
        target = destination / relative;target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as output:output.write(data)
        target.chmod(0o755 if mode & 0o111 else 0o644)
    config_path = destination / 'service.config.json'
    config = json.loads(config_path.read_text())
    alias = re.sub(r'[^a-z0-9_-]+', '-', destination.name.lower()).strip('-_')[:48] or 'my-tools'
    config.update(name=alias, clientName=alias)
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n')
    guide = destination / 'README.md'
    text = re.sub(r'^cd /home/[^\n]+$', '# 先进入本服务根目录', guide.read_text(), flags=re.MULTILINE)
    if language == 'python':
        text = text.replace('"text": {"type": "string"}', '"text": {"type": "string", "description": "交给业务函数的输入文本"}')
        text = text.replace('"result": {"type": "string"}', '"result": {"type": "string", "description": "业务函数处理后的结果文本"}')
    else:
        text = text.replace('a: z.number()', 'a: z.number().describe("第一个乘数")')
        text = text.replace('b: z.number()', 'b: z.number().describe("第二个乘数")')
        text = text.replace('product: z.number()', 'product: z.number().describe("两个乘数的乘积")')
    entry = 'tools.py / business.py' if language == 'python' else 'src/tools.ts / src/business.ts'
    intro = (f'> 这是可编辑的独立{language}服务，由固定模板复制；运行不依赖原MC工具包或Harness。\n'
             f'> 先在本目录执行`./start.sh`，默认提供函数`add`和独立程序`add_program`两个工具。\n'
             f'> 替换业务时改`{entry}`，多个工具加入同一注册表；监听/访问地址与鉴权按`service.config.json`和环境配置。\n'
             '> 首次启动安装语言依赖，可能需要网络；这里不安装OpenMC。启动后把实际URL、传输方式及认证信息交给兼容MCP客户端，工具定义由tools/list发现。\n'
             '> connection.json和/info用于读取连接信息；0.0.0.0是监听地址，不能作为客户端地址。修改/新增源码后按下文打包，并检查新增模块与程序是否进包。\n\n')
    guide.write_text(intro + text)
    (destination / 'TEMPLATE.json').write_text(json.dumps({'source':ARCHIVE,'sha256':digest,
        'template_version':manifest['version'],'language':language,
        'changes':['service name/client alias','portable README entry and described parameter examples'] +
                  (['TypeScript example parameter/result descriptions'] if language == 'typescript' else [])},indent=2)+'\n')
    return config


def main():
    parser = argparse.ArgumentParser(prog='create-mcp.sh', description='离线创建可编辑独立MCP服务；默认Python，已有目录不覆盖')
    parser.add_argument('directory', help='新的服务目录；父目录须存在')
    parser.add_argument('--language', choices=['python', 'typescript'], default='python')
    args = parser.parse_args();destination = Path(args.directory).absolute()
    try:
        config = create(destination, args.language)
        print('MCP服务源码已创建：' + str(destination))
        print('运行依赖：' + ('Python3.10+与venv/pip' if args.language == 'python' else 'Node22与Corepack/pnpm'))
        print('下一步：\n  cd ' + shlex.quote(str(destination)) + '\n  ./start.sh')
        print('同一服务默认提供add、add_program；配置文件：' + str(destination / 'service.config.json'))
        print(f"默认本机地址：http://{config['host']}:{config['port']}{config['path']}；远程部署需实际可达URL及鉴权。")
        print('创建未安装依赖或启动服务。详细步骤见本服务README；首次启动可能访问包源。')
        return 0
    except (OSError, ValueError, KeyError, tarfile.TarError) as error:
        print('创建未完成：' + str(error) + '。若已出现新目录，请核对其中内容后改用新目录，不会覆盖重试。', file=sys.stderr)
        return 1


if __name__ == '__main__':raise SystemExit(main())
