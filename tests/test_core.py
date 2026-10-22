import httpx
import pytest
from conftest import body
from mcp.server.fastmcp.exceptions import ToolError

from slack_personal_mcp import slack as slack_api
from slack_personal_mcp.server import mcp

pytestmark = pytest.mark.anyio

WRITE = {
    "slack_send_message",
    "slack_add_reaction",
    "slack_remove_reaction",
    "slack_upload_file",
    "slack_download_file",
    "slack_edit_message",
    "slack_delete_message",
    "slack_schedule_message",
    "slack_cancel_scheduled",
    "slack_set_status",
    "slack_mark_read",
}


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    waits = []
    monkeypatch.setattr(slack_api.time, "sleep", waits.append)
    return waits


def read(**kw):
    return slack_api.call("conversations.history", workspace="acme", params={}, **kw)


def test_html_error_page(slack):
    slack.on("conversations.history", lambda r: httpx.Response(502, text="<html>bad gateway</html>"))
    with pytest.raises(slack_api.SlackError, match="HTTP 502, not a JSON response"):
        read()


def test_rate_limit_is_retried_after_retry_after(slack, no_sleep):
    replies = iter(
        [
            httpx.Response(429, headers={"Retry-After": "3"}, json={"ok": False, "error": "ratelimited"}),
            httpx.Response(200, json={"ok": True, "messages": []}),
        ]
    )
    slack.on("conversations.history", lambda r: next(replies))
    assert read() == {"ok": True, "messages": []}
    assert no_sleep == [3.0]


def test_rate_limit_beyond_budget_reports_retry_after(slack, no_sleep):
    slack.on("conversations.history", lambda r: httpx.Response(429, headers={"Retry-After": "120"}, json={}))
    with pytest.raises(slack_api.SlackError, match="retry after 120s") as exc:
        read()
    assert exc.value.retry_after == 120 and no_sleep == []


def test_read_retries_server_errors(slack):
    replies = iter([httpx.Response(503, text="busy"), httpx.Response(200, json={"ok": True})])
    slack.on("conversations.history", lambda r: next(replies))
    assert read() == {"ok": True}


def test_connect_error_is_retried_then_reported(slack):
    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    slack.on("chat.postMessage", refuse)
    with pytest.raises(slack_api.SlackError, match="cannot connect"):
        slack_api.call("chat.postMessage", workspace="acme", json_body={})
    assert len(slack.calls("chat.postMessage")) == slack_api.MAX_ATTEMPTS


@pytest.mark.parametrize(
    "reply",
    [
        lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("slow", request=r)),
        lambda r: httpx.Response(500, text="oops"),
    ],
)
def test_write_failure_after_sending_is_unknown_and_not_retried(slack, reply):
    slack.on("chat.postMessage", reply)
    with pytest.raises(slack_api.OutcomeUnknown, match="Check Slack before retrying"):
        slack_api.call("chat.postMessage", workspace="acme", json_body={})
    assert len(slack.calls("chat.postMessage")) == 1


def test_ambiguous_alias(monkeypatch):
    monkeypatch.setenv("SLACK_USER_TOKEN_A_B", "xoxp-1")
    monkeypatch.setenv("SLACK_USER_TOKEN_A__B", "xoxp-2")
    with pytest.raises(slack_api.SlackError, match="ambiguous"):
        slack_api.token("a-b")


async def test_list_workspaces_reports_each_workspace(slack, call_tool, monkeypatch):
    monkeypatch.setenv("SLACK_USER_TOKEN_OTHER", "xoxp-other")

    def auth(request):
        if request.headers["authorization"].endswith("other"):
            raise httpx.ConnectError("down", request=request)
        return httpx.Response(200, json={"ok": True, "team": "Acme", "user_id": "U1"})

    slack.on("auth.test", auth)
    out = await call_tool("slack_list_workspaces")
    assert '"team": "Acme"' in out and '"configured": false' in out and "cannot connect" in out


async def test_send_reports_slack_warnings(slack, call_tool):
    slack.on("chat.postMessage", {"ok": True, "ts": "1.2", "channel": "C1", "warning": "message_truncated"})
    out = await call_tool("slack_send_message", workspace="acme", channel_id="C1", text="hi")
    assert '"warnings": [\n    "message_truncated"' in out


async def test_send_refuses_text_slack_would_truncate(slack, call_tool):
    with pytest.raises(ToolError, match="up to 40000 as mrkdwn"):
        await call_tool("slack_send_message", workspace="acme", channel_id="C1", text="a" * 40_001, format="mrkdwn")
    with pytest.raises(ToolError, match="up to 12000 as markdown"):
        await call_tool("slack_send_message", workspace="acme", channel_id="C1", text="a" * 12_001)
    assert slack.requests == []


async def test_write_channels_allowlist(slack, call_tool, monkeypatch, tmp_path):
    monkeypatch.setenv("SLACK_WRITE_CHANNELS", "C1, D9")
    slack.on("chat.postMessage", {"ok": True, "ts": "1", "channel": "C1"})
    await call_tool("slack_send_message", workspace="acme", channel_id="C1", text="hi")
    with pytest.raises(ToolError, match="SLACK_WRITE_CHANNELS"):
        await call_tool("slack_add_reaction", workspace="acme", channel_id="C2", ts="1", name="eyes")

    path = tmp_path / "a.txt"
    path.write_text("x")
    with pytest.raises(ToolError, match="not allowed"):
        await call_tool("slack_upload_file", workspace="acme", channel_id="U1", file_path=str(path))
    assert slack.requests[1:] == []  # no DM opened, nothing uploaded


async def test_unreadable_reply_to_a_write_is_unknown(slack):
    slack.on("chat.postMessage", lambda r: httpx.Response(200, text="<html>"))
    with pytest.raises(slack_api.OutcomeUnknown, match="may have been applied"):
        slack_api.call("chat.postMessage", workspace="acme", json_body={})


async def test_annotations():
    tools = {t.name: t.annotations for t in await mcp.list_tools()}
    for name, ann in tools.items():
        assert ann.readOnlyHint is (name not in WRITE), name
    assert tools["slack_lint"].openWorldHint is False
    assert tools["slack_add_reaction"].idempotentHint is True
    assert tools["slack_send_message"].destructiveHint is False
    assert tools["slack_download_file"].destructiveHint is True


async def test_search_query_stays_out_of_logs(slack, call_tool, caplog):
    caplog.set_level("DEBUG")
    slack.on("search.messages", {"ok": True, "messages": {"matches": []}})
    await call_tool("slack_search_messages", workspace="acme", query="secret codename")
    assert "secret" not in caplog.text


def test_post_body_is_json(slack):
    slack.on("chat.postMessage", {"ok": True})
    slack_api.call("chat.postMessage", workspace="acme", json_body={"text": "hi"})
    assert body(slack.requests[0]) == {"text": "hi"}


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ({"error": "missing_scope", "needed": "reactions:write"}, "add user token scope reactions:write, reinstall"),
        ({"error": "not_in_channel"}, "not_in_channel; join the conversation"),
        ({"error": "token_revoked"}, "revoked: create a new user token"),
        ({"error": "token_expired"}, "token rotation is on"),
        ({"error": "something_new"}, r"something_new$"),
    ],
)
def test_errors_say_what_to_do(slack, reply, expected):
    slack.on("reactions.add", {"ok": False} | reply)
    with pytest.raises(slack_api.SlackError, match=expected) as exc:
        slack_api.call("reactions.add", workspace="acme", json_body={})
    assert exc.value.needed == reply.get("needed", "")


async def test_read_tools_prefer_the_scope_slack_names(slack, call_tool):
    slack.on("conversations.info", {"ok": False, "error": "missing_scope", "needed": "mpim:read"})
    with pytest.raises(ToolError, match="scope.*: mpim:read; reinstall"):
        await call_tool("slack_channel_info", workspace="acme", channel_id="G1")
