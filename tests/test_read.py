import importlib
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from slack_personal_mcp import tools_read
from slack_personal_mcp.server import mcp

pytestmark = pytest.mark.anyio


def catalogs(slack, *, team="T1", users=None, channels=None):
    slack.on("auth.test", {"ok": True, "team_id": team})
    slack.on("users.list", {"ok": True, "members": users or []})
    slack.on("conversations.list", {"ok": True, "channels": channels or []})


async def result(name, **arguments):
    content, structured = await mcp.call_tool(name, arguments)
    assert json.loads(content[0].text) == structured
    return structured


async def test_history_cursor_time_bounds_and_projection(slack):
    catalogs(slack, users=[{"id": "U1", "name": "alice", "profile": {"display_name": "Alice"}}])
    slack.on(
        "conversations.history",
        {
            "ok": True,
            "messages": [
                {
                    "ts": "1729612345.123456",
                    "user": "U1",
                    "username": "stale name",
                    "text": "hello",
                    "thread_ts": "1729612300.000001",
                    "reply_count": 3,
                    "subtype": "thread_broadcast",
                    "bot_id": "B1",
                    "bot_profile": {"name": "Build bot"},
                    "edited": {"user": "U1", "ts": "1729612346.000001"},
                    "reactions": [{"name": "eyes", "count": 2, "users": ["U1"]}],
                    "files": [{"id": "F1", "name": "build.txt"}],
                    "attachments": [
                        {
                            "pretext": "Build",
                            "title": "Failed",
                            "text": "Retry",
                            "fields": [{"title": "Job", "value": "42"}],
                        }
                    ],
                    "blocks": [
                        {
                            "type": "section",
                            "text": {"type": "mrkdwn", "text": "Section"},
                            "fields": [{"text": "Field"}],
                        },
                        {
                            "type": "rich_text",
                            "elements": [
                                {
                                    "type": "rich_text_section",
                                    "elements": [
                                        {"type": "text", "text": "Rich "},
                                        {"type": "link", "url": "https://example.test", "text": "link"},
                                    ],
                                },
                                {
                                    "type": "rich_text_list",
                                    "elements": [
                                        {"type": "rich_text_section", "elements": [{"type": "text", "text": "Item"}]}
                                    ],
                                },
                            ],
                        },
                        {"type": "context", "elements": [{"type": "plain_text", "text": "Context"}]},
                    ],
                }
            ],
            "response_metadata": {"next_cursor": "next"},
        },
    )
    out = await result(
        "slack_read_channel",
        workspace="acme",
        channel_id="C1",
        cursor="start",
        oldest="10.0",
        latest="20.0",
        inclusive=True,
        limit=5,
    )
    assert out["next_cursor"] == "next" and out["has_more"] is True
    assert dict(slack.calls("conversations.history")[0].url.params) == {
        "channel": "C1",
        "cursor": "start",
        "oldest": "10.0",
        "latest": "20.0",
        "inclusive": "true",
        "limit": "5",
    }
    assert out["messages"] == [
        {
            "ts": "1729612345.123456",
            "time": "2024-10-22T15:52:25.123456+00:00",
            "user_id": "U1",
            "user_name": "Alice",
            "text": "hello",
            "thread_ts": "1729612300.000001",
            "reply_count": 3,
            "subtype": "thread_broadcast",
            "bot_id": "B1",
            "bot_name": "Build bot",
            "edited": {"user": "U1", "ts": "1729612346.000001"},
            "reactions": [{"name": "eyes", "count": 2}],
            "files": [{"id": "F1", "name": "build.txt"}],
            "attachments_text": "Build\nFailed\nRetry\nJob: 42",
            "blocks_text": "Section\nField\nRich link\nItem\nContext",
        }
    ]


async def test_empty_fields_and_blocks_only_bot(slack):
    slack.on(
        "conversations.history",
        {
            "ok": True,
            "messages": [
                {"ts": "1.000001", "text": "", "reply_count": 0, "attachments": [], "blocks": [], "edited": {}},
                {
                    "ts": "2.0",
                    "bot_id": "B2",
                    "username": "bot",
                    "blocks": [{"type": "section", "text": {"text": "Alert"}}],
                },
            ],
        },
    )
    out = await result("slack_read_channel", workspace="acme", channel_id="C1")
    assert out == {
        "messages": [
            {"ts": "1.000001", "time": "1970-01-01T00:00:01.000001+00:00", "reply_count": 0},
            {
                "ts": "2.0",
                "time": "1970-01-01T00:00:02+00:00",
                "bot_id": "B2",
                "bot_name": "bot",
                "user_name": "bot",
                "blocks_text": "Alert",
            },
        ],
        "next_cursor": "",
        "has_more": False,
    }
    assert slack.calls("users.list") == []


@pytest.mark.parametrize(
    ("all_pages", "budget", "pages", "complete"), [(False, 10, 1, False), (True, 1, 1, False), (True, 3, 2, True)]
)
async def test_thread_page_budget_and_continuation(slack, all_pages, budget, pages, complete):
    def replies(request):
        second = request.url.params.get("cursor") == "next"
        return httpx.Response(
            200,
            json={
                "ok": True,
                "messages": [{"ts": "2.0" if second else "1.0", "text": "reply"}],
                "has_more": not second,
                "response_metadata": {"next_cursor": "" if second else "next"},
            },
        )

    slack.on("conversations.replies", replies)
    out = await result(
        "slack_read_thread",
        workspace="acme",
        channel_id="C1",
        thread_ts="1.0",
        cursor="start",
        oldest="0.0",
        latest="3.0",
        inclusive=True,
        limit=1,
        all_pages=all_pages,
        max_pages=budget,
    )
    assert len(slack.calls("conversations.replies")) == pages
    assert len(out["messages"]) == pages and out["complete"] is complete
    assert out["has_more"] is (not complete) and out["next_cursor"] == ("" if complete else "next")
    for request in slack.calls("conversations.replies"):
        assert request.url.params["ts"] == "1.0" and request.url.params["oldest"] == "0.0"
        assert request.url.params["latest"] == "3.0" and request.url.params["inclusive"] == "true"


@pytest.mark.parametrize("metadata", [{}, {"next_cursor": "same"}])
async def test_thread_does_not_loop_without_progress(slack, metadata):
    slack.on("conversations.replies", {"ok": True, "messages": [], "has_more": True, "response_metadata": metadata})
    out = await result(
        "slack_read_thread", workspace="acme", channel_id="C1", thread_ts="1", cursor="same", all_pages=True
    )
    assert out["complete"] is False and out["has_more"] is True
    assert len(slack.calls("conversations.replies")) == 1


async def test_search_filters_paging_and_cached_user_name(slack):
    catalogs(slack, users=[{"id": "U1", "name": "alice"}])
    slack.on(
        "search.messages",
        {
            "ok": True,
            "messages": {
                "matches": [
                    {
                        "ts": "1.0",
                        "user": "U1",
                        "username": "old",
                        "text": "hit",
                        "channel": {"id": "C1", "name": "general"},
                        "permalink": "https://example.test/message",
                    }
                ],
                "total": 201,
                "paging": {"page": 2, "pages": 3, "total": 201},
            },
        },
    )
    out = await result(
        "slack_search_messages",
        workspace="acme",
        query='"raw phrase" -is:starred',
        count=100,
        page=2,
        sort="timestamp",
        sort_dir="asc",
        in_channel="C1",
        from_user="U1",
        before="2026-10-22",
        after="2026-10-01",
        has="file",
    )
    assert dict(slack.calls("search.messages")[0].url.params) == {
        "query": '"raw phrase" -is:starred in:C1 from:U1 before:2026-10-22 after:2026-10-01 has:file',
        "count": "100",
        "page": "2",
        "sort": "timestamp",
        "sort_dir": "asc",
    }
    assert (out["total"], out["page"], out["pages"]) == (201, 2, 3)
    assert out["messages"][0] == {
        "ts": "1.0",
        "time": "1970-01-01T00:00:01+00:00",
        "user_id": "U1",
        "user_name": "alice",
        "text": "hit",
        "channel": "C1",
        "channel_name": "general",
        "permalink": "https://example.test/message",
    }


async def test_search_new_pagination_envelope(slack):
    slack.on(
        "search.messages",
        {"ok": True, "messages": {"matches": [], "pagination": {"total_count": 9, "page": 2, "page_count": 3}}},
    )
    assert await result("slack_search_messages", workspace="acme", query="x", page=2) == {
        "messages": [],
        "total": 9,
        "page": 2,
        "pages": 3,
    }


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("slack_search_messages", {"query": "x", "count": 101}),
        ("slack_search_messages", {"query": "x", "count": 0}),
        ("slack_search_messages", {"query": "x", "page": 0}),
        ("slack_search_messages", {"query": "x", "sort": "bad"}),
        ("slack_search_messages", {"query": "x", "sort_dir": "bad"}),
        ("slack_read_channel", {"channel_id": "C1", "limit": 0}),
        ("slack_read_thread", {"channel_id": "C1", "thread_ts": "1", "max_pages": 0}),
        ("slack_find_user", {"query": "a", "limit": 0}),
        ("slack_find_channel", {"query": "a", "types": "bogus"}),
    ],
)
async def test_invalid_arguments_do_not_reach_transport(slack, name, args):
    with pytest.raises(ToolError):
        await result(name, workspace="acme", **args)
    assert slack.requests == []


async def test_exact_email_and_user_id_use_direct_methods(slack):
    user = {"id": "UABC123", "name": "alice", "profile": {"display_name": "Alice"}}
    slack.on("users.lookupByEmail", {"ok": True, "user": user})
    slack.on("users.info", {"ok": True, "user": user})
    expected = {"users": [{"id": "UABC123", "name": "alice", "display_name": "Alice"}]}
    assert await result("slack_find_user", workspace="acme", query="alice@example.test") == expected
    assert await result("slack_find_user", workspace="acme", query="UABC123") == expected
    assert slack.calls("users.lookupByEmail")[0].url.params["email"] == "alice@example.test"
    assert slack.calls("users.info")[0].url.params["user"] == "UABC123"
    assert slack.calls("users.list") == [] and slack.calls("auth.test") == []


async def test_info_tools(slack):
    user = {"id": "U1", "name": "alice", "profile": {"title": "Engineer"}, "deleted": False}
    channel = {"id": "C1", "name": "general", "topic": {"value": "News"}, "num_members": 12}
    slack.on("users.info", {"ok": True, "user": user})
    slack.on("conversations.info", {"ok": True, "channel": channel})
    assert await result("slack_user_info", workspace="acme", user_id="U1") == {
        "user": {"id": "U1", "name": "alice", "title": "Engineer"}
    }
    assert await result("slack_channel_info", workspace="acme", channel_id="C1") == {
        "channel": {"id": "C1", "name": "general", "topic": "News", "num_members": 12}
    }


async def test_rich_text_is_kept_only_without_text(slack):
    rich = [
        {"type": "rich_text", "elements": [{"type": "rich_text_section", "elements": [{"type": "text", "text": "hi"}]}]}
    ]
    slack.on("conversations.history", {"ok": True, "messages": [{"ts": "1.0", "text": "hi", "blocks": rich}]})
    assert "blocks_text" not in (await result("slack_read_channel", workspace="acme", channel_id="C1"))["messages"][0]


async def test_rich_text_with_more_than_the_fallback_text_is_kept(slack):
    rich = [
        {
            "type": "rich_text",
            "elements": [
                {"type": "rich_text_section", "elements": [{"type": "text", "text": "Deploy failed: step 3"}]}
            ],
        }
    ]
    slack.on(
        "conversations.history", {"ok": True, "messages": [{"ts": "1.0", "text": "Deploy failed", "blocks": rich}]}
    )
    message = (await result("slack_read_channel", workspace="acme", channel_id="C1"))["messages"][0]
    assert message["blocks_text"] == "Deploy failed: step 3"


async def test_id_shaped_name_falls_back_to_name_search(slack):
    catalogs(slack, users=[{"id": "U1", "name": "william"}])
    slack.on("users.info", {"ok": False, "error": "user_not_found"})
    assert await result("slack_find_user", workspace="acme", query="WILLIAM") == {
        "users": [{"id": "U1", "name": "william"}]
    }


async def test_channel_info_asks_for_member_count(slack):
    slack.on("conversations.info", {"ok": True, "channel": {"id": "C1", "num_members": 3}})
    await result("slack_channel_info", workspace="acme", channel_id="C1")
    assert slack.calls("conversations.info")[0].url.params["include_num_members"] == "true"


async def test_unknown_email_finds_nobody(slack):
    slack.on("users.lookupByEmail", {"ok": False, "error": "users_not_found"})
    assert await result("slack_find_user", workspace="acme", query="nobody@example.test") == {"users": []}


@pytest.mark.parametrize(
    ("name", "method", "args", "scope"),
    [
        ("slack_find_user", "users.lookupByEmail", {"query": "alice@example.test"}, "users:read.email"),
        ("slack_user_info", "users.info", {"user_id": "U1"}, "users:read"),
        ("slack_channel_info", "conversations.info", {"channel_id": "D1"}, "im:read"),
        ("slack_find_channel", "conversations.list", {"query": "", "types": "im,mpim"}, "mpim:read"),
        ("slack_read_channel", "conversations.history", {"channel_id": "C1"}, "channels:history"),
    ],
)
async def test_missing_scope_names_scope_and_reinstall(slack, name, method, args, scope):
    catalogs(slack)
    slack.on(method, {"ok": False, "error": "missing_scope"})
    with pytest.raises(ToolError, match=scope) as exc:
        await result(name, workspace="acme", **args)
    assert "reinstall" in str(exc.value).lower()


async def test_catalogs_load_all_pages_once_refresh_and_expire(slack, monkeypatch):
    from slack_personal_mcp import cache

    now = [100.0]
    monkeypatch.setattr(cache.time, "monotonic", lambda: now[0])
    catalogs(slack)

    def users(request):
        second = request.url.params.get("cursor") == "next"
        return httpx.Response(
            200,
            json={
                "ok": True,
                "members": [
                    {"id": "U2" if second else "U1", "name": "alice2" if second else "alice1"},
                    {"id": "UB", "name": "alicebot", "is_bot": True},
                    {"id": "UD", "name": "alicedeleted", "deleted": True},
                ],
                "response_metadata": {"next_cursor": "" if second else "next"},
            },
        )

    slack.on("users.list", users)
    assert len((await result("slack_find_user", workspace="acme", query="alice", limit=1))["users"]) == 1
    assert len((await result("slack_find_user", workspace="ACME", query="alice"))["users"]) == 2
    assert len(slack.calls("users.list")) == 2
    await result("slack_find_user", workspace="acme", query="alice", refresh=True)
    assert len(slack.calls("users.list")) == 4
    now[0] += 3601
    await result("slack_find_user", workspace="acme", query="alice")
    assert len(slack.calls("users.list")) == 6


async def test_channel_types_cached_separately_and_dm_names(slack):
    catalogs(slack, users=[{"id": "U1", "name": "alice"}])

    def channels(request):
        if request.url.params["types"] == "im,mpim":
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "channels": [
                        {"id": "D1", "is_im": True, "user": "U1"},
                        {"id": "G1", "name": "mpdm-alice-bob", "is_mpim": True, "is_private": True},
                    ],
                },
            )
        return httpx.Response(
            200, json={"ok": True, "channels": [{"id": "C1", "name": "general", "is_private": False}]}
        )

    slack.on("conversations.list", channels)
    assert (await result("slack_find_channel", workspace="acme", query="gen"))["channels"] == [
        {"id": "C1", "name": "general", "is_private": False}
    ]
    out = await result("slack_find_channel", workspace="acme", query="alice", types="mpim, im")
    assert out["channels"] == [
        {"id": "D1", "name": "alice", "is_im": True, "user_id": "U1"},
        {"id": "G1", "name": "mpdm-alice-bob", "is_private": True, "is_mpim": True},
    ]
    await result("slack_find_channel", workspace="acme", query="", types="im,mpim")
    assert len(slack.calls("conversations.list")) == 2
    await result("slack_find_channel", workspace="acme", query="", types="im,mpim", refresh=True)
    assert len(slack.calls("conversations.list")) == 3


async def test_catalog_isolation_by_workspace_team_and_token(slack, monkeypatch):
    monkeypatch.setenv("SLACK_USER_TOKEN_OTHER", "xoxp-other")
    team = ["T1"]
    slack.on("auth.test", lambda r: httpx.Response(200, json={"ok": True, "team_id": team[0]}))
    slack.on(
        "users.list",
        lambda r: httpx.Response(
            200,
            json={
                "ok": True,
                "members": [{"id": "U1", "name": "other" if r.headers["authorization"].endswith("other") else team[0]}],
            },
        ),
    )
    assert (await result("slack_find_user", workspace="acme", query=""))["users"][0]["name"] == "T1"
    assert (await result("slack_find_user", workspace="other", query=""))["users"][0]["name"] == "other"
    team[0] = "T2"
    monkeypatch.setenv("SLACK_USER_TOKEN_ACME", "xoxp-new")
    assert (await result("slack_find_user", workspace="acme", query=""))["users"][0]["name"] == "T2"
    assert len(slack.calls("users.list")) == 3


async def test_channel_catalog_pages_ttl_and_team_change(slack, monkeypatch):
    from slack_personal_mcp import cache

    now, team = [100.0], ["T1"]
    monkeypatch.setattr(cache.time, "monotonic", lambda: now[0])
    slack.on("auth.test", lambda r: httpx.Response(200, json={"ok": True, "team_id": team[0]}))

    def channels(request):
        second = request.url.params.get("cursor") == "next"
        assert request.url.params["types"] == "public_channel,private_channel"
        assert request.url.params["exclude_archived"] == "true"
        return httpx.Response(
            200,
            json={
                "ok": True,
                "channels": [{"id": "C2" if second else "C1", "name": f"{team[0]}-second" if second else "first"}],
                "response_metadata": {"next_cursor": "" if second else "next"},
            },
        )

    slack.on("conversations.list", channels)
    assert (await result("slack_find_channel", workspace="acme", query="second", limit=1))["channels"] == [
        {"id": "C2", "name": "T1-second"}
    ]
    await result("slack_find_channel", workspace="acme", query="first")
    assert len(slack.calls("conversations.list")) == 2
    now[0] += 3601
    team[0] = "T2"
    assert (await result("slack_find_channel", workspace="acme", query="second"))["channels"][0]["name"] == "T2-second"
    assert len(slack.calls("conversations.list")) == 4


@pytest.mark.parametrize("failure", ["missing_scope", "repeated_cursor"])
async def test_incomplete_catalog_is_not_cached(slack, failure):
    catalogs(slack)
    calls = [0]

    def users(request):
        calls[0] += 1
        if failure == "missing_scope" and calls[0] == 2:
            return httpx.Response(200, json={"ok": False, "error": "missing_scope"})
        return httpx.Response(
            200,
            json={
                "ok": True,
                "members": [{"id": "U1", "name": "partial"}],
                "response_metadata": {"next_cursor": "again"},
            },
        )

    slack.on("users.list", users)
    with pytest.raises(ToolError, match="users:read" if failure == "missing_scope" else "incomplete"):
        await result("slack_find_user", workspace="acme", query="")
    slack.on("users.list", {"ok": True, "members": [{"id": "U2", "name": "complete"}]})
    assert await result("slack_find_user", workspace="acme", query="") == {"users": [{"id": "U2", "name": "complete"}]}
    assert len(slack.calls("users.list")) == 3


async def test_search_unknown_author_and_rich_text_entities(slack):
    catalogs(slack)
    slack.on(
        "search.messages",
        {
            "ok": True,
            "messages": {
                "matches": [
                    {
                        "ts": "1.0",
                        "user": "UOUTSIDE",
                        "username": "External",
                        "blocks": [
                            {
                                "type": "rich_text",
                                "elements": [
                                    {
                                        "type": "rich_text_quote",
                                        "elements": [
                                            {
                                                "type": "rich_text_section",
                                                "elements": [
                                                    {"type": "user", "user_id": "U1"},
                                                    {"type": "channel", "channel_id": "C1"},
                                                    {"type": "emoji", "name": "eyes"},
                                                    {"type": "broadcast", "range": "here"},
                                                    {"type": "usergroup", "usergroup_id": "S1"},
                                                ],
                                            }
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                ]
            },
        },
    )
    message = (await result("slack_search_messages", workspace="acme", query="x"))["messages"][0]
    assert message["user_id"] == "UOUTSIDE" and message["user_name"] == "External"
    assert message["blocks_text"] == "<@U1><#C1>:eyes:<!here><!subteam^S1>"


def test_concurrent_catalog_load_is_shared(slack):
    catalogs(slack)
    started, release = Event(), Event()

    def users(request):
        started.set()
        assert release.wait(5)
        return httpx.Response(200, json={"ok": True, "members": [{"id": "U1", "name": "alice"}]})

    slack.on("users.list", users)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(tools_read.slack_find_user, workspace="acme", query="alice") for _ in range(4)]
        assert started.wait(5)
        release.set()
        outputs = [future.result() for future in futures]
    assert outputs == [{"users": [{"id": "U1", "name": "alice"}]}] * 4
    assert len(slack.calls("users.list")) == len(slack.calls("auth.test")) == 1


def test_import_does_not_load_catalogs(slack):
    from slack_personal_mcp import cache

    importlib.reload(cache)
    assert slack.requests == []


async def test_read_output_schemas():
    names = {
        "slack_list_workspaces",
        "slack_search_messages",
        "slack_read_channel",
        "slack_read_thread",
        "slack_find_user",
        "slack_find_channel",
        "slack_user_info",
        "slack_channel_info",
    }
    tools = {tool.name: tool for tool in await mcp.list_tools()}
    for name in names:
        assert tools[name].outputSchema["type"] == "object", name
