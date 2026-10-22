import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    ("args", "extra", "count"),
    [
        ([str(ROOT / "server.py")], {}, 30),
        (["-m", "slack_personal_mcp"], {}, 30),
        (["-m", "slack_personal_mcp"], {"SLACK_READ_ONLY": "1"}, 19),
    ],
)
async def test_stdio_launch(args, extra, count, tmp_path):
    env = {"PATH": os.environ["PATH"], "SLACK_ENV_FILE": os.devnull, "SLACK_USER_TOKEN_ACME": "xoxp-test", **extra}
    params = StdioServerParameters(command=sys.executable, args=args, env=env, cwd=tmp_path)
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        tools = await session.list_tools()
    assert len(tools.tools) == count


@pytest.mark.parametrize(("env_file", "expected"), [(None, "cannot connect"), ("", "Unknown Slack workspace")])
async def test_launcher_reads_env_next_to_it(tmp_path, env_file, expected):
    """The clone launcher loads .env beside server.py; SLACK_ENV_FILE='' turns that off."""
    mock_transport = """
import httpx

def offline(self, request):
    raise httpx.ConnectError("offline test", request=request)

httpx.HTTPTransport.handle_request = offline
"""
    (tmp_path / "server.py").write_text(mock_transport + (ROOT / "server.py").read_text())
    (tmp_path / "src").symlink_to(ROOT / "src")
    (tmp_path / ".env").write_text("SLACK_USER_TOKEN_FROMFILE=xoxp-file\n")
    env = {"PATH": os.environ["PATH"]}
    if env_file is not None:
        env["SLACK_ENV_FILE"] = env_file
    params = StdioServerParameters(command=sys.executable, args=[str(tmp_path / "server.py")], env=env, cwd=tmp_path)
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        result = await session.call_tool("slack_read_channel", {"workspace": "fromfile", "channel_id": "C1"})
    text = result.content[0].text
    assert result.isError
    assert expected in text


async def test_stdio_read_returns_structured_content(tmp_path):
    launcher = tmp_path / "mock_server.py"
    launcher.write_text("""
import httpx

def handle(self, request):
    replies = {
        "conversations.history": {"ok": True, "messages": [{"ts": "1.0", "user": "U1", "text": "hello"}]},
        "auth.test": {"ok": True, "team_id": "T1"},
        "users.list": {"ok": True, "members": [{"id": "U1", "name": "alice"}]},
    }
    return httpx.Response(200, json=replies[request.url.path.rsplit("/", 1)[-1]])

httpx.HTTPTransport.handle_request = handle
from slack_personal_mcp.server import main
main()
""")
    env = {"PATH": os.environ["PATH"], "SLACK_ENV_FILE": os.devnull, "SLACK_USER_TOKEN_ACME": "xoxp-test"}
    params = StdioServerParameters(command=sys.executable, args=[str(launcher)], env=env, cwd=tmp_path)
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        tools = {tool.name: tool for tool in (await session.list_tools()).tools}
        assert tools["slack_read_channel"].outputSchema["type"] == "object"
        result = await session.call_tool("slack_read_channel", {"workspace": "acme", "channel_id": "C1"})
    assert not result.isError
    assert result.structuredContent == {
        "messages": [
            {"ts": "1.0", "time": "1970-01-01T00:00:01+00:00", "user_id": "U1", "user_name": "alice", "text": "hello"}
        ],
        "next_cursor": "",
        "has_more": False,
    }
