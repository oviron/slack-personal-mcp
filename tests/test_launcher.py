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


@pytest.mark.parametrize(("env_file", "expected"), [(None, "cannot reach"), ("", "Unknown Slack workspace")])
async def test_launcher_reads_env_next_to_it(tmp_path, env_file, expected):
    """The clone launcher loads .env beside server.py; SLACK_ENV_FILE='' turns that off."""
    (tmp_path / "server.py").write_text((ROOT / "server.py").read_text())
    (tmp_path / "src").symlink_to(ROOT / "src")
    (tmp_path / ".env").write_text("SLACK_USER_TOKEN_FROMFILE=xoxp-file\n")
    env = {"PATH": os.environ["PATH"], "HTTPS_PROXY": "http://127.0.0.1:9"}
    if env_file is not None:
        env["SLACK_ENV_FILE"] = env_file
    params = StdioServerParameters(command=sys.executable, args=[str(tmp_path / "server.py")], env=env, cwd=tmp_path)
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        result = await session.call_tool("slack_read_channel", {"workspace": "fromfile", "channel_id": "C1"})
    text = result.content[0].text
    assert result.isError
    if expected == "cannot reach":
        assert "Unknown Slack workspace" not in text, text
    else:
        assert expected in text
