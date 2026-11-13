"""Standard MCP handshake -> workflow tool -> on-demand references/resources; no model."""
import asyncio,hashlib,json,os
from pathlib import Path
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
ROOT=Path(__file__).resolve().parents[2]
async def run():
    async with streamablehttp_client(os.environ['MC_TEST_MCP_URL']) as (read,write,_):
        async with ClientSession(read,write) as session:
            hello=await session.initialize()
            assert hello.serverInfo.version==(ROOT/'VERSION').read_text().strip()
            assert 'openmc_get_workflow' in hello.instructions
            assert 'chapter="start"' in hello.instructions
            discovered=await session.list_tools()
            tool=next(t for t in discovered.tools if t.name=='openmc_get_workflow')
            assert tool.annotations.readOnlyHint
            values={}
            for chapter in ['start','files','advanced']:
                result=await session.call_tool(tool.name,{'chapter':chapter})
                assert not result.isError
                v=result.structuredContent or json.loads(result.content[0].text)
                raw=v['content'].encode();assert hashlib.sha256(raw).hexdigest()==v['sha256']
                assert len(raw)==v['bytes']
                resource=await session.read_resource(v['uri'])
                assert resource.contents[0].text==v['content']
                assert resource.contents[0].mimeType=='text/markdown'
                values[chapter]=v
            assert 'openmc_import_model' in values['start']['content']
            assert 'openmc_upload_status' in values['files']['content']
            listing=await session.list_resources()
            assert {v['uri'] for v in values.values()} <= {str(r.uri) for r in listing.resources}
            bad=await session.call_tool(tool.name,{'chapter':'../../config/openmc.local.json'})
            assert bad.isError
            report={'version':hello.serverInfo.version,'instructions':hello.instructions,
                'chapters':{k:{a:v[a] for a in ['uri','sha256','bytes']} for k,v in values.items()},
                'tool_resource_bytes_identical':True,'invalid_chapter_rejected':True}
            Path(os.environ['MC_TEST_SESSION_DIR'],'workflow.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
asyncio.run(run())
