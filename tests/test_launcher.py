import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("args", [[str(ROOT / "server.py")], ["-m", "slack_personal_mcp"]])
async def test_stdio_launch(args):
    env = {"PATH": os.environ["PATH"], "SLACK_ENV_FILE": os.devnull, "SLACK_USER_TOKEN_ACME": "xoxp-test"}
    params = StdioServerParameters(command=sys.executable, args=args, env=env)
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        tools = await session.list_tools()
    assert len(tools.tools) == 14
