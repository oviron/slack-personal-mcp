import json

import pytest
from conftest import body
from mcp.server.fastmcp.exceptions import ToolError

from slack_personal_mcp.server import mcp

pytestmark = pytest.mark.anyio

EXTRA_TOOLS = {
    "slack_list_emoji",
    "slack_list_usergroups",
    "slack_get_reactions",
    "slack_read_canvas",
    "slack_search",
}


async def result(name, **arguments):
    content, structured = await mcp.call_tool(name, arguments)
    assert json.loads(content[0].text) == structured
    return structured


async def test_extra_tools_are_structured_and_read_only():
    tools = {tool.name: tool for tool in await mcp.list_tools()}
    assert EXTRA_TOOLS <= tools.keys()
    for name in EXTRA_TOOLS:
        assert tools[name].annotations.readOnlyHint is True
        assert tools[name].outputSchema["type"] == "object"


async def test_emoji_substring_filter_and_alias(slack):
    slack.on(
        "emoji.list",
        {
            "ok": True,
            "emoji": {"shipit": "alias:squirrel", "ship": "https://emoji.slack.com/ship.png", "other": "alias:ship"},
            "categories": ["ignored"],
        },
    )
    assert await result("slack_list_emoji", workspace="acme", query="SHIP") == {
        "emoji": [{"name": "shipit", "alias": "squirrel"}, {"name": "ship", "url": "https://emoji.slack.com/ship.png"}]
    }
    assert (await result("slack_list_emoji", workspace="acme"))["emoji"][-1] == {"name": "other", "alias": "ship"}
    assert await result("slack_list_emoji", workspace="acme", query="absent") == {"emoji": []}
    assert all(request.method == "GET" and not request.url.params for request in slack.requests)


@pytest.mark.parametrize("include_users", [False, True])
async def test_usergroups_projection_and_optional_members(slack, include_users):
    slack.on(
        "usergroups.list",
        {
            "ok": True,
            "usergroups": [
                {
                    "id": "S1",
                    "name": "Developers",
                    "handle": "devs",
                    "description": "Engineering",
                    "user_count": "2",
                    "users": ["U1", "U2"],
                    "prefs": {"channels": ["C1"]},
                    "created_by": "U9",
                },
                {"id": "S2", "name": "Empty", "users": [], "user_count": 0},
            ],
        },
    )
    expected = [
        {"id": "S1", "name": "Developers", "handle": "devs", "description": "Engineering", "user_count": "2"},
        {"id": "S2", "name": "Empty", "user_count": 0},
    ]
    if include_users:
        expected[0]["users"] = ["U1", "U2"]
        expected[1]["users"] = []
    arguments = {"include_users": True} if include_users else {}
    assert await result("slack_list_usergroups", workspace="acme", **arguments) == {"usergroups": expected}
    (request,) = slack.calls("usergroups.list")
    assert request.method == "GET"
    assert dict(request.url.params) == {"include_users": str(include_users).lower(), "include_count": "true"}


@pytest.mark.parametrize(("count", "complete"), [(2, True), (3, False)])
async def test_reactions_use_cached_names_and_preserve_total(slack, count, complete):
    slack.on("auth.test", {"ok": True, "team_id": "T1"})
    slack.on(
        "users.list", {"ok": True, "members": [{"id": "U1", "name": "alice", "profile": {"display_name": "Alice"}}]}
    )
    slack.on(
        "reactions.get",
        {
            "ok": True,
            "type": "message",
            "channel": "C1",
            "message": {
                "text": "not needed",
                "reactions": [{"name": "eyes", "count": count, "users": ["U1", "U2"]}, {"name": "wave", "count": 0}],
            },
        },
    )
    expected = {
        "channel_id": "C1",
        "ts": "1.000001",
        "reactions": [
            {
                "name": "eyes",
                "count": count,
                "users": [{"user_id": "U1", "user_name": "Alice"}, {"user_id": "U2", "user_name": None}],
                "users_complete": complete,
            },
            {"name": "wave", "count": 0, "users": [], "users_complete": True},
        ],
    }
    for _ in range(2):
        assert await result("slack_get_reactions", workspace="acme", channel_id="C1", ts="1.000001") == expected
    assert len(slack.calls("users.list")) == 1
    assert len(slack.calls("auth.test")) == 1
    assert dict(slack.calls("reactions.get")[0].url.params) == {
        "channel": "C1",
        "timestamp": "1.000001",
        "full": "true",
    }


async def test_empty_reactions_do_not_load_directory(slack):
    slack.on("reactions.get", {"ok": True, "message": {}})
    assert await result("slack_get_reactions", workspace="acme", channel_id="D1", ts="1.0") == {
        "channel_id": "D1",
        "ts": "1.0",
        "reactions": [],
    }
    assert len(slack.requests) == 1


async def test_reactions_name_cache_scope_error_is_actionable(slack):
    slack.on("reactions.get", {"ok": True, "message": {"reactions": [{"name": "eyes", "count": 1, "users": ["U1"]}]}})
    slack.on("auth.test", {"ok": True, "team_id": "T1"})
    slack.on("users.list", {"ok": False, "error": "missing_scope"})
    with pytest.raises(ToolError, match="missing_scope.*users:read.*reinstall"):
        await result("slack_get_reactions", workspace="acme", channel_id="C1", ts="1.0")


@pytest.mark.parametrize(
    ("content_type", "content"), [("markdown", "# Plan\n\nFull content"), ("html", "<h1>Plan</h1>"), ("markdown", "")]
)
async def test_canvas_reads_full_content_directly(slack, content_type, content):
    slack.on("canvases.getContent", {"ok": True, "content": content, "ignored": {"large": "payload"}})
    arguments = {"content_type": content_type} if content_type != "markdown" else {}
    assert await result("slack_read_canvas", workspace="acme", canvas_id="F1", **arguments) == {
        "canvas_id": "F1",
        "content_type": content_type,
        "content": content,
    }
    (request,) = slack.requests
    assert request.method == "POST"
    assert request.headers["authorization"] == "Bearer xoxp-test"
    assert body(request) == {"canvas_id": "F1", "content_type": content_type}


async def test_search_documented_results_and_cursor(slack):
    slack.on(
        "assistant.search.context",
        {
            "ok": True,
            "results": {
                "messages": [
                    {
                        "author_user_id": "U1",
                        "author_name": "Alice",
                        "channel_id": "C1",
                        "channel_name": "dev",
                        "message_ts": "1.000001",
                        "content": "Release plan",
                        "is_author_bot": False,
                        "permalink": "https://acme.slack.com/archives/C1/p1000001",
                        "blocks": [{"large": "ignored"}],
                        "context_messages": {"before": [{"text": "ignored"}]},
                    }
                ],
                "files": [
                    {
                        "file_id": "F1",
                        "title": "Plan",
                        "file_type": "application/pdf",
                        "content": "Summary",
                        "ignored": 1,
                    }
                ],
                "channels": [{"name": "dev", "topic": "Releases", "purpose": "Ship", "ignored": 1}],
                "users": [
                    {
                        "user_id": "U1",
                        "full_name": "Alice",
                        "title": "Engineer",
                        "timezone": "UTC",
                        "profile_pic_permalink": "ignored",
                    }
                ],
                "unknown": [{"large": "ignored"}],
            },
            "response_metadata": {"next_cursor": " next== "},
        },
    )
    content_types = ["messages", "files", "channels", "users"]
    channel_types = ["public_channel", "private_channel", "im", "mpim"]
    assert await result(
        "slack_search",
        workspace="acme",
        query="release in:<#C1>",
        content_types=content_types,
        channel_types=channel_types,
        cursor="previous==",
        limit=7,
    ) == {
        "results": {
            "messages": [
                {
                    "author_user_id": "U1",
                    "author_name": "Alice",
                    "channel_id": "C1",
                    "channel_name": "dev",
                    "message_ts": "1.000001",
                    "content": "Release plan",
                    "is_author_bot": False,
                    "permalink": "https://acme.slack.com/archives/C1/p1000001",
                }
            ],
            "files": [{"file_id": "F1", "title": "Plan", "file_type": "application/pdf", "content": "Summary"}],
            "channels": [{"name": "dev", "topic": "Releases", "purpose": "Ship"}],
            "users": [{"user_id": "U1", "full_name": "Alice", "title": "Engineer", "timezone": "UTC"}],
        },
        "next_cursor": "next==",
        "has_more": True,
    }
    (request,) = slack.requests
    assert request.method == "POST"
    assert request.headers["authorization"] == "Bearer xoxp-test"
    assert not request.url.params
    assert body(request) == {
        "query": "release in:<#C1>",
        "content_types": content_types,
        "channel_types": channel_types,
        "cursor": "previous==",
        "limit": 7,
    }


async def test_search_defaults_and_empty_page(slack):
    slack.on("assistant.search.context", {"ok": True, "results": {"messages": []}})
    assert await result("slack_search", workspace="acme", query="plan") == {
        "results": {"messages": []},
        "next_cursor": "",
        "has_more": False,
    }
    assert body(slack.requests[0]) == {
        "query": "plan",
        "content_types": ["messages"],
        "channel_types": ["public_channel"],
        "limit": 20,
    }


@pytest.mark.parametrize(
    ("name", "method", "arguments", "scope"),
    [
        ("slack_list_emoji", "emoji.list", {}, "emoji:read"),
        ("slack_list_usergroups", "usergroups.list", {}, "usergroups:read"),
        ("slack_get_reactions", "reactions.get", {"channel_id": "C1", "ts": "1.0"}, "reactions:read"),
        ("slack_read_canvas", "canvases.getContent", {"canvas_id": "F1"}, "canvases:read"),
        ("slack_search", "assistant.search.context", {"query": "plan"}, "search:read.public"),
    ],
)
async def test_missing_scopes_are_actionable(slack, name, method, arguments, scope):
    slack.on(method, {"ok": False, "error": "missing_scope"})
    with pytest.raises(ToolError, match=rf"missing_scope.*{scope}.*reinstall.*replace the token") as exc:
        await result(name, workspace="acme", **arguments)
    if name == "slack_search":
        for scope in (
            "search:read.private",
            "search:read.im",
            "search:read.mpim",
            "search:read.files",
            "search:read.users",
        ):
            assert scope in str(exc.value)
    assert len(slack.requests) == 1


@pytest.mark.parametrize("error", ["not_allowed", "feature_not_enabled"])
async def test_search_unavailable_is_actionable_without_legacy_fallback(slack, error):
    slack.on("assistant.search.context", {"ok": False, "error": error})
    with pytest.raises(ToolError, match=rf"{error}.*Real-time Search.*app/workspace"):
        await result("slack_search", workspace="acme", query="plan")
    assert len(slack.requests) == 1


@pytest.mark.parametrize(
    ("name", "method", "arguments", "error"),
    [
        ("slack_read_canvas", "canvases.getContent", {"canvas_id": "F1"}, "canvas_not_found"),
        ("slack_search", "assistant.search.context", {"query": "plan"}, "invalid_cursor"),
        ("slack_get_reactions", "reactions.get", {"channel_id": "C1", "ts": "1.0"}, "message_not_found"),
    ],
)
async def test_other_slack_errors_are_preserved(slack, name, method, arguments, error):
    slack.on(method, {"ok": False, "error": error})
    with pytest.raises(ToolError, match=error):
        await result(name, workspace="acme", **arguments)


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("slack_search", {"query": "plan", "limit": 0}),
        ("slack_search", {"query": "plan", "limit": 21}),
        ("slack_search", {"query": "plan", "content_types": ["invalid"]}),
        ("slack_search", {"query": "plan", "content_types": []}),
        ("slack_search", {"query": "plan", "channel_types": ["invalid"]}),
        ("slack_search", {"query": "plan", "channel_types": []}),
        ("slack_read_canvas", {"canvas_id": "F1", "content_type": "invalid"}),
    ],
)
async def test_invalid_arguments_make_no_requests(slack, name, arguments):
    with pytest.raises(ToolError):
        await result(name, workspace="acme", **arguments)
    assert not slack.requests
