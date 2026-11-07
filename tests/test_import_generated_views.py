"""D5 回归：导入模型时不得把生成的 views/ 预览图当输入搬运。

功能：`openmc_import_model` 的导入文件清单与目标目录。
类别：回归（旧行为：source/views/*.png 被 copytree 一并复制并列入 files）。
前置：无。构造原生三件套与假视图图；不调用OpenMC API，不执行脚本。
输入：source 目录含 geometry/materials/settings.xml 和 views/geometry-xy.png。
预期：导入成功，目标不含 views/，files 不含图片；源目录视图保留。
不证明：不证明真实模型可计算、外部依赖可用或专业验收。
"""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'mcp'))
import business
from openmc_import import import_model


class ImportExcludesGeneratedViews(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mc import views ')
        self.root = Path(self.temp.name)
        self.ws = self.root / 'workspace'
        self.ws.mkdir()
        self.previous = business.WORKSPACE
        business.WORKSPACE = self.ws
        self.addCleanup(setattr, business, 'WORKSPACE', self.previous)
        self.addCleanup(self.temp.cleanup)
        self.config = self.root / 'config.json'
        self.config.write_text((ROOT / 'config/openmc.json').read_text())
        self.env = patch.dict(os.environ, {
            'MC_WORKSPACE': str(self.ws),
            'MC_OPENMC_CONFIG': str(self.config),
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.source = self.ws / 'incoming'
        self.source.mkdir()
        for name in ('geometry', 'materials', 'settings'):
            (self.source / (name + '.xml')).write_text('<%s/>' % name)
        (self.source / 'views').mkdir()
        (self.source / 'views/geometry-xy.png').write_bytes(b'not an input')

    def test_generated_views_are_not_copied_or_listed(self):
        value = import_model('incoming', 'cases/imported', api_check=False)
        target = self.ws / 'cases/imported'
        self.assertFalse((target / 'views').exists())
        self.assertEqual(sorted(item['name'] for item in value['files']), sorted([
            'geometry.xml', 'materials.xml', 'settings.xml', 'mc-case.json']))
        self.assertTrue((self.source / 'views/geometry-xy.png').is_file())


if __name__ == '__main__':
    unittest.main()