"""Check reachable manual links and correspondence to the actual exposed registry."""
from html.parser import HTMLParser
import importlib.util
from pathlib import Path
import re
import unittest
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]


class Document(HTMLParser):
    def __init__(self, source):
        super().__init__(); self.ids=set(); self.links=[]; self.text=[]
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs)
        if 'id' in attrs:self.ids.add(attrs['id'])
        for key in ('href','src'):
            if key in attrs:self.links.append(attrs[key])

    def handle_data(self, value):self.text.append(value)


def broken_links(root):
    problems=[]
    for source in sorted((root/'docs').glob('*.html')):
        for link in Document(source.read_text()).links:
            url=urlsplit(link)
            if url.scheme or url.netloc:continue
            target=(source.parent/unquote(url.path)).resolve() if url.path else source
            if not target.exists():problems.append((source.name,link,'missing file'));continue
            if url.fragment and target.suffix=='.html' and unquote(url.fragment) not in Document(target.read_text()).ids:
                problems.append((source.name,link,'missing anchor'))
    return problems


class Manual(unittest.TestCase):
    def test_offline_manual_links_reach_files_and_anchors(self):
        self.assertEqual(broken_links(ROOT),[])
        version=(ROOT/'VERSION').read_text().strip()
        for name in ['index.html','toolkit-usage.html','deployment.html','mcp.html','project-records.html']:
            visible=Document((ROOT/'docs'/name).read_text()).text
            self.assertTrue(any('工具包'+version in value.replace(' ', '') for value in visible), name+' manual edition must match VERSION')
        self.assertIn('源码版本'+version, (ROOT/'README.md').read_text())

    def test_reference_is_generated_from_current_registry(self):
        spec=importlib.util.spec_from_file_location('mc_reference',ROOT/'scripts/generate_tool_reference.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        current=module.generate()
        self.assertEqual((ROOT/'docs/reference.html').read_text(),current,'Regenerate actual tool reference')
        document=Document(current)
        for tool in module.TOOLS:self.assertIn(tool['name'],document.ids)

    def test_dsh_debug_table_lists_the_complete_server_keys_for_reported_tools(self):
        import sys
        sys.path.insert(0,str(ROOT/'mcp'))
        from tools import TOOLS
        text=(ROOT/'docs/deployment.html').read_text()
        section=re.search(r'<details id="schema-check">.*?</details>',text,re.S)
        self.assertIsNotNone(section)
        for name in ['openmc_list_official_examples','openmc_read_example_results']:
            rows=[row for row in re.findall(r'<tr>.*?</tr>',section.group(),re.S) if name in row]
            self.assertEqual(len(rows),1)
            cells=re.findall(r'<td[^>]*>(.*?)</td>',rows[0],re.S)
            actual=set(''.join(Document(cells[1]).text).strip().split('/'))
            tool=next(t for t in TOOLS if t['name']==name)
            self.assertEqual(actual,set(tool['inputSchema']['properties']))

    def test_quick_start_points_to_actual_tools_and_full_walkthrough(self):
        import sys
        sys.path.insert(0,str(ROOT/'mcp'))
        from tools import TOOLS
        source=(ROOT/'docs/index.html').read_text()
        names=re.findall(r'data-primary-tool="([^"]+)"',source)
        self.assertEqual(len(names),12)
        self.assertEqual(len(set(names)),12)
        self.assertTrue(set(names)<={t['name'] for t in TOOLS})
        self.assertIn('toolkit-usage.html#assembly',source)

    def test_user_schema_text_is_escaped_without_loss(self):
        spec=importlib.util.spec_from_file_location('mc_reference_escape',ROOT/'scripts/generate_tool_reference.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        description='温度 < 500 & 单位 K；禁止 <script>alert(1)</script>'
        tool={'name':'custom','description':'custom','inputSchema':{'type':'object','properties':{'temperature':{'type':'number','description':description}}},'outputSchema':{'type':'object'},'http':{'url':'https://example.invalid'}}
        result=module.generate([tool]);self.assertIn(description,''.join(Document(result).text))
        self.assertNotIn('<script>alert(1)</script>',result)


if __name__=='__main__':unittest.main()
