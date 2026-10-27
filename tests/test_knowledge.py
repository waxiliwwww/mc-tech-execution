"""Offline sources and citations are checked against actual files, not generated answers."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'mcp'))
import openmc_knowledge as knowledge


class OfficialKnowledge(unittest.TestCase):
    def test_sources_are_official_pinned_and_complete(self):
        data = knowledge.list_sources()
        self.assertEqual(len(data['documents']), 8)
        for item in data['documents']:
            self.assertEqual(item['version'], '0.16.0')
            self.assertEqual(item['license'], 'MIT')
            self.assertEqual(item['content_kind'], 'official_original')
            self.assertIn('github.com/openmc-dev/openmc/blob/617d35', item['source_url'])
            self.assertEqual(hashlib.sha256((ROOT / 'knowledge' / item['source']).read_bytes()).hexdigest(), item['sha256'])
        license = (ROOT / 'knowledge/openmc-0.16.0/LICENSE').read_text()
        self.assertIn('Permission is hereby granted', license)

    def test_chinese_query_gets_original_parameter_definition_and_context(self):
        matches = knowledge.search('粒子数')['matches']
        self.assertTrue(matches)
        first = matches[0]
        self.assertEqual(first['content_kind'], 'official_original')
        self.assertIn('particles', first['text'])
        self.assertIn('<particles>', first['text'])
        context = knowledge.read(first['source'], first['start_line'], first['end_line']-first['start_line']+1)
        self.assertEqual(context['text'], first['text'])
        self.assertIn('#L', context['citation_url'])
        self.assertEqual(knowledge.search('unfindable_76044865')['matches'], [])

    def test_original_run_mode_definition_is_returned_not_an_invented_answer(self):
        hits = knowledge.search('运行模式')['matches']
        hit = next(h for h in hits if '<run_mode>' in h['text'])
        text = knowledge.read(hit['source'], hit['start_line'], 12)['text']
        self.assertIn('"eigenvalue", "fixed source", "plot", "volume", or "particle restart"', text)

    def test_bad_paths_and_tampered_source_do_not_get_official_citations(self):
        for source in ['../README.md', '/etc/passwd', 'missing.rst']:
            with self.assertRaises(ValueError):knowledge.read(source)
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / 'knowledge'
            shutil.copytree(ROOT / 'knowledge', base)
            original = knowledge.BASE
            knowledge.BASE = base
            try:
                path = base / 'openmc-0.16.0/io_formats/settings.rst'
                path.write_text(path.read_text() + '\nTampered definition\n')
                with self.assertRaisesRegex(ValueError, '哈希不符'):knowledge.read(path.relative_to(base).as_posix())
            finally:knowledge.BASE = original


if __name__ == '__main__':unittest.main()
