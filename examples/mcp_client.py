#!/usr/bin/env python3
"""Standard MCP discovery/call example. JSON stdout, no Harness or model required."""
import argparse
import asyncio
import json
import os
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def run(args):
    payload = json.loads(args.arguments)
    if not isinstance(payload, dict):raise ValueError('arguments须为JSON对象')
    token = os.environ.get('MCP_AUTH_TOKEN')
    headers = {'Authorization': 'Bearer ' + token} if token else None
    async with streamablehttp_client(args.url, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            if not args.tool:return (await session.list_tools()).model_dump(mode='json'), False
            result = await session.call_tool(args.tool, payload)
            return result.model_dump(mode='json'), result.isError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--tool', help='省略时执行tools/list')
    parser.add_argument('--arguments', default='{}')
    args = parser.parse_args()
    try:
        value, failed = asyncio.run(run(args))
        print(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))
        return 1 if failed else 0
    except Exception as error:
        from openmc_demo import error_text
        print('MCP调用失败：' + error_text(error), file=sys.stderr)
        return 1


if __name__ == '__main__':raise SystemExit(main())
