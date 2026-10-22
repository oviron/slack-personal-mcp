import datetime
import json
import time

import pytest
from conftest import body
from mcp.server.fastmcp.exceptions import ToolError

pytestmark = pytest.mark.anyio


@pytest.fixture
def me(slack):
    slack.on("auth.test", {"ok": True, "user_id": "U1"})
    return slack


def message_by(slack, user):
    slack.on("conversations.replies", {"ok": True, "messages": [{"ts": "0.5"}, {"ts": "1.0", "user": user}]})


async def test_edit_own_message(me, call_tool):
    message_by(me, "U1")
    me.on("chat.update", {"ok": True, "ts": "1.0", "channel": "C1"})
    out = json.loads(await call_tool("slack_edit_message", workspace="acme", channel_id="C1", ts="1.0", text="fixed"))
    assert out["edited"] is True
    assert body(me.calls("chat.update")[0]) == {"channel": "C1", "ts": "1.0", "markdown_text": "fixed"}
    assert me.calls("conversations.replies")[0].url.params["oldest"] == "1.0"


async def test_edit_and_delete_refuse_messages_by_others(me, call_tool):
    message_by(me, "U2")
    with pytest.raises(ToolError, match="posted by U2, not by you"):
        await call_tool("slack_edit_message", workspace="acme", channel_id="C1", ts="1.0", text="x")
    with pytest.raises(ToolError, match="posted by U2"):
        await call_tool("slack_delete_message", workspace="acme", channel_id="C1", ts="1.0")
    assert me.calls("chat.update") == me.calls("chat.delete") == []


async def test_edit_goes_through_style_gate(me, call_tool):
    with pytest.raises(ToolError, match="REFUSED"):
        await call_tool("slack_edit_message", workspace="acme", channel_id="C1", ts="1.0", text="a — b")
    assert me.requests == []


async def test_delete_missing_message(me, call_tool):
    me.on("conversations.replies", {"ok": False, "error": "thread_not_found"})
    with pytest.raises(ToolError, match="No message 1.0 in C1"):
        await call_tool("slack_delete_message", workspace="acme", channel_id="C1", ts="1.0")


async def test_delete_own_message(me, call_tool):
    message_by(me, "U1")
    me.on("chat.delete", {"ok": True})
    out = json.loads(await call_tool("slack_delete_message", workspace="acme", channel_id="C1", ts="1.0"))
    assert out["deleted"] is True


async def test_schedule_message(slack, call_tool):
    post_at = int(time.time()) + 3600
    slack.on(
        "chat.scheduleMessage",
        {"ok": True, "channel": "C1", "scheduled_message_id": "Q1", "post_at": post_at},
    )
    out = json.loads(
        await call_tool("slack_schedule_message", workspace="acme", channel_id="C1", text="hi", post_at=str(post_at))
    )
    assert out["scheduled_message_id"] == "Q1" and out["post_at"].endswith("+00:00")
    assert body(slack.requests[0]) == {"channel": "C1", "post_at": post_at, "markdown_text": "hi"}


async def test_schedule_takes_iso_with_timezone(slack, call_tool):
    when = int(time.time()) + 86400
    post_at = datetime.datetime.fromtimestamp(when, datetime.timezone(datetime.timedelta(hours=3))).isoformat()
    slack.on("chat.scheduleMessage", {"ok": True, "scheduled_message_id": "Q1"})
    await call_tool("slack_schedule_message", workspace="acme", channel_id="C1", text="hi", post_at=post_at)
    assert body(slack.requests[0])["post_at"] == when


@pytest.mark.parametrize(
    ("post_at", "error"),
    [
        ("2026-01-01T09:00:00", "needs a timezone"),
        ("1", "in the past"),
        ("next week", "neither"),
        ("9999999999", "120"),
    ],
)
async def test_schedule_refuses_bad_times(slack, call_tool, post_at, error):
    with pytest.raises(ToolError, match=error):
        await call_tool("slack_schedule_message", workspace="acme", channel_id="C1", text="hi", post_at=post_at)
    assert slack.requests == []


async def test_list_and_cancel_scheduled(slack, call_tool):
    slack.on(
        "chat.scheduledMessages.list",
        {"ok": True, "scheduled_messages": [{"id": "Q2", "channel_id": "C1", "post_at": 20, "text": "b"},
                                            {"id": "Q1", "channel_id": "C1", "post_at": 10, "text": "a"}]},
    )  # fmt: skip
    out = json.loads(await call_tool("slack_list_scheduled", workspace="acme"))
    assert [m["id"] for m in out["scheduled"]] == ["Q1", "Q2"] and out["next_cursor"] is None
    slack.on("chat.deleteScheduledMessage", {"ok": True})
    await call_tool("slack_cancel_scheduled", workspace="acme", channel_id="C1", scheduled_message_id="Q1")
    assert body(slack.calls("chat.deleteScheduledMessage")[0]) == {"channel": "C1", "scheduled_message_id": "Q1"}


async def test_permalink(slack, call_tool):
    slack.on("chat.getPermalink", {"ok": True, "permalink": "https://acme.slack.com/archives/C1/p1"})
    out = json.loads(await call_tool("slack_get_permalink", workspace="acme", channel_id="C1", ts="1.0"))
    assert out["permalink"].endswith("/p1")
    assert slack.requests[0].url.params["message_ts"] == "1.0"


async def test_set_and_clear_status(slack, call_tool):
    slack.on("users.profile.set", {"ok": True})
    out = json.loads(
        await call_tool("slack_set_status", workspace="acme", text="Off", emoji="palm_tree", expires_in_minutes=60)
    )
    profile = body(slack.requests[0])["profile"]
    assert profile["status_emoji"] == ":palm_tree:" and profile["status_expiration"] > time.time()
    assert "expires" in out
    await call_tool("slack_set_status", workspace="acme")
    assert body(slack.requests[1])["profile"] == {"status_text": "", "status_emoji": "", "status_expiration": 0}
    with pytest.raises(ToolError, match="rewrite the status"):
        await call_tool("slack_set_status", workspace="acme", text="Back soon…")


async def test_unread_counts_others_messages_after_marker(me, call_tool):
    me.on("conversations.info", {"ok": True, "channel": {"id": "C1", "name": "general", "last_read": "100.0"}})
    me.on(
        "conversations.history",
        {"ok": True, "has_more": False, "messages": [{"ts": "102", "user": "U2"}, {"ts": "101", "user": "U1"}]},
    )
    out = json.loads(await call_tool("slack_unread", workspace="acme", channel_ids=["C1"]))
    assert out["channels"] == [
        {"channel": "C1", "name": "general", "unread": 1, "has_more": False, "last_read": "1970-01-01T00:01:40+00:00"}
    ]
    assert me.calls("conversations.history")[0].url.params["oldest"] == "100.0"
    with pytest.raises(ToolError, match="1 to 20"):
        await call_tool("slack_unread", workspace="acme", channel_ids=[])


async def test_mark_read(slack, call_tool):
    slack.on("conversations.mark", {"ok": True})
    await call_tool("slack_mark_read", workspace="acme", channel_id="C1", ts="1.0")
    assert body(slack.requests[0]) == {"channel": "C1", "ts": "1.0"}
