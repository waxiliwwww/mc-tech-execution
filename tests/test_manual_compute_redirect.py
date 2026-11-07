"""旧 compute.html 入口应继续把用户带到当前手册，而不是留下失效教学。

功能：`docs/compute.html` 的章节兼容入口与当前 `toolkit-usage.html` 的落点。
类别：回归/可用性（旧问题：手册章节已整合，但真实教学测试仍读取已删除的
      `data-run` 块，发布时直接 KeyError；旧链接也没有被单独验证）。
前置：无。只读 HTML，不执行 OpenMC。
输入：docs/compute.html 中的旧章节锚点和指向。
预期：每个旧锚点都指向存在的 HTML 文件与存在的片段；页面不再声称可执行旧
      内嵌课程；当前手册仍列出 build/submit/results 三步。
不证明：不证明手册命令能实际跑通、不证明物理结果或专业验收。
"""
from html.parser import HTMLParser
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
OLD_ANCHORS = {'setup', 'config', 'prepare', 'change', 'run', 'states', 'results',
               'report-selection', 'scan', 'repair', 'depletion', 'view',
               'maintenance', 'content', 'more'}


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = {}

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == 'a' and values.get('id'):
            self.links[values['id']] = values.get('href', '')


class ManualComputeRedirect(unittest.TestCase):
    def test_every_old_lesson_anchor_reaches_current_manual(self):
        page = (ROOT / 'docs/compute.html').read_text(encoding='utf-8')
        self.assertNotIn('data-run=', page)
        parser = Links()
        parser.feed(page)
        self.assertEqual(set(parser.links), OLD_ANCHORS)
        for anchor, href in parser.links.items():
            with self.subTest(anchor=anchor):
                path, _, fragment = href.partition('#')
                target = ROOT / 'docs' / path
                self.assertTrue(target.is_file(), href)
                self.assertTrue(fragment, href)
                self.assertIn('id="%s"' % fragment, target.read_text(encoding='utf-8'))

    def test_current_manual_keeps_the_three_core_calls(self):
        page = (ROOT / 'docs/toolkit-usage.html').read_text(encoding='utf-8')
        for name in ('openmc_build_model', 'openmc_submit_job', 'openmc_read_results'):
            self.assertIn(name, page)


if __name__ == '__main__':
    unittest.main()