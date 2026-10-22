import httpx
import pytest
from conftest import body
from mcp.server.fastmcp.exceptions import ToolError

from slack_personal_mcp.config import workspace_tokens
from slack_personal_mcp.server import mcp

pytestmark = pytest.mark.anyio

TOOLS = {
    "slack_list_workspaces",
    "slack_lint",
    "slack_send_message",
    "slack_add_reaction",
    "slack_remove_reaction",
    "slack_upload_file",
    "slack_file_info",
    "slack_list_files",
    "slack_download_file",
    "slack_search_messages",
    "slack_read_channel",
    "slack_read_thread",
    "slack_find_user",
    "slack_find_channel",
}


async def test_tools_are_registered():
    assert {t.name for t in await mcp.list_tools()} == TOOLS


def test_workspace_aliases(monkeypatch):
    monkeypatch.setenv("SLACK_USER_TOKEN_SIDE_PROJECT", "xoxp-2")
    monkeypatch.setenv("SLACK_USER_TOKEN_A__B", "xoxp-3")
    monkeypatch.setenv("SLACK_USER_TOKEN_", "xoxp-4")
    assert set(workspace_tokens()) == {"acme", "side-project", "a-b"}


async def test_send_message_posts_as_user(slack, call_tool):
    slack.on("chat.postMessage", {"ok": True, "ts": "1.2", "channel": "C1"})
    out = await call_tool("slack_send_message", workspace="ACME", channel_id="C1", text="hi", thread_ts="1.0")
    assert out == "sent workspace=acme ts=1.2 channel=C1"
    (req,) = slack.calls("chat.postMessage")
    assert req.headers["authorization"] == "Bearer xoxp-test"
    assert body(req) == {"channel": "C1", "text": "hi", "thread_ts": "1.0"}


async def test_style_gate_refuses_before_sending(slack, call_tool):
    with pytest.raises(ToolError, match="REFUSED"):
        await call_tool("slack_send_message", workspace="acme", channel_id="C1", text="Great question — sure")
    assert slack.requests == []


async def test_style_gate_off(slack, call_tool, monkeypatch):
    monkeypatch.setenv("SLACK_STYLE_GATE", "off")
    slack.on("chat.postMessage", {"ok": True, "ts": "1.2", "channel": "C1"})
    out = await call_tool("slack_send_message", workspace="acme", channel_id="C1", text="Great question — sure")
    assert out.startswith("sent")


async def test_unknown_workspace(slack, call_tool):
    with pytest.raises(ToolError, match="Unknown Slack workspace"):
        await call_tool("slack_read_channel", workspace="nope", channel_id="C1")


async def test_slack_error_is_raised(slack, call_tool):
    slack.on("conversations.history", {"ok": False, "error": "channel_not_found"})
    with pytest.raises(ToolError, match="channel_not_found"):
        await call_tool("slack_read_channel", workspace="acme", channel_id="C1")


async def test_read_channel(slack, call_tool):
    slack.on(
        "conversations.history",
        {
            "ok": True,
            "messages": [{"ts": "1.1", "user": "U1", "text": "hey", "files": [{"id": "F1", "name": "a.txt"}]}],
        },
    )
    out = await call_tool("slack_read_channel", workspace="acme", channel_id="C1", limit=5)
    assert '"F1"' in out and '"hey"' in out
    assert slack.requests[0].url.params["limit"] == "5"


async def test_upload_into_user_dm(slack, call_tool, tmp_path):
    path = tmp_path / "report.txt"
    path.write_text("data")
    slack.on("conversations.open", {"ok": True, "channel": {"id": "D1"}})
    slack.on(
        "files.getUploadURLExternal", {"ok": True, "upload_url": "https://files.slack.com/upload/v1/x", "file_id": "F1"}
    )
    slack.on("https://files.slack.com/upload/v1/x", lambda r: httpx.Response(200, text="OK"))
    slack.on("files.completeUploadExternal", {"ok": True, "files": [{"id": "F1"}]})
    out = await call_tool("slack_upload_file", workspace="acme", channel_id="U1", file_path=str(path))
    assert out == "uploaded workspace=acme file_id=F1 name=report.txt channel=D1"
    assert b"channel_id=D1" in slack.calls("files.completeUploadExternal")[0].content


async def test_download(slack, call_tool, tmp_path):
    slack.on(
        "files.info", {"ok": True, "file": {"name": "a.txt", "url_private_download": "https://files.slack.com/d/a.txt"}}
    )
    slack.on("https://files.slack.com/d/a.txt", lambda r: httpx.Response(200, content=b"abc"))
    out = await call_tool("slack_download_file", workspace="acme", file_id="F1", dest_dir=str(tmp_path))
    assert (tmp_path / "F1-a.txt").read_bytes() == b"abc"
    assert out.endswith(f"path={tmp_path / 'F1-a.txt'}")
