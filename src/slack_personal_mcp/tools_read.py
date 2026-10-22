import json

from .app import mcp
from .config import workspace_tokens
from .slack import call, file_summary, message


@mcp.tool()
def slack_list_workspaces() -> str:
    """List configured workspace aliases and verify which Slack account each token belongs to."""
    out = []
    for workspace in workspace_tokens():
        try:
            data = call("auth.test", workspace=workspace)
            out.append(
                {
                    "workspace": workspace,
                    "configured": True,
                    "team": data.get("team"),
                    "team_id": data.get("team_id"),
                    "url": data.get("url"),
                    "user": data.get("user"),
                    "user_id": data.get("user_id"),
                }
            )
        except RuntimeError as exc:
            out.append({"workspace": workspace, "configured": False, "error": str(exc)})
    return json.dumps(out, ensure_ascii=False, indent=2)


@mcp.tool()
def slack_search_messages(workspace: str, query: str, count: int = 15) -> str:
    """Search messages you can see (search:read). Slack search syntax supported."""
    data = call("search.messages", workspace=workspace, params={"query": query, "count": count})
    matches = data.get("messages", {}).get("matches", [])
    out = [
        {
            "channel": m.get("channel", {}).get("id"),
            "channel_name": m.get("channel", {}).get("name"),
            "user": m.get("username") or m.get("user"),
            "ts": m.get("ts"),
            "text": m.get("text"),
            "permalink": m.get("permalink"),
            **({"files": [file_summary(f) for f in m["files"]]} if m.get("files") else {}),
        }
        for m in matches
    ]
    return json.dumps(out, ensure_ascii=False, indent=2)


@mcp.tool()
def slack_read_channel(workspace: str, channel_id: str, limit: int = 20) -> str:
    """Read recent messages in a channel/DM (conversations.history)."""
    data = call(
        "conversations.history",
        workspace=workspace,
        params={"channel": channel_id, "limit": limit},
    )
    out = [message(m) for m in data.get("messages", [])]
    return json.dumps(out, ensure_ascii=False, indent=2)


@mcp.tool()
def slack_read_thread(workspace: str, channel_id: str, thread_ts: str, limit: int = 50) -> str:
    """Read a full thread (conversations.replies)."""
    data = call(
        "conversations.replies",
        workspace=workspace,
        params={"channel": channel_id, "ts": thread_ts, "limit": limit},
    )
    out = [message(m) for m in data.get("messages", [])]
    return json.dumps(out, ensure_ascii=False, indent=2)


@mcp.tool()
def slack_find_user(workspace: str, query: str, limit: int = 10) -> str:
    """Resolve a person to their user id by name/display name/email (scans users.list)."""
    q = query.lower()
    out = []
    cursor = ""
    while len(out) < limit:
        params = {"limit": 200}
        if cursor:
            params["cursor"] = cursor
        data = call("users.list", workspace=workspace, params=params)
        for u in data.get("members", []):
            if u.get("deleted") or u.get("is_bot"):
                continue
            p = u.get("profile", {})
            hay = " ".join(
                filter(None, [u.get("name"), p.get("real_name"), p.get("display_name"), p.get("email")])
            ).lower()
            if q in hay:
                out.append(
                    {
                        "id": u.get("id"),
                        "name": u.get("name"),
                        "real_name": p.get("real_name"),
                        "display_name": p.get("display_name"),
                    }
                )
                if len(out) >= limit:
                    break
        cursor = data.get("response_metadata", {}).get("next_cursor", "")
        if not cursor:
            break
    return json.dumps(out, ensure_ascii=False, indent=2)


@mcp.tool()
def slack_find_channel(workspace: str, query: str, limit: int = 15) -> str:
    """Find channel ids by name (conversations.list, public + private you are in)."""
    q = query.lower()
    out = []
    cursor = ""
    while len(out) < limit:
        params = {"types": "public_channel,private_channel", "limit": 200, "exclude_archived": "true"}
        if cursor:
            params["cursor"] = cursor
        data = call("conversations.list", workspace=workspace, params=params)
        for c in data.get("channels", []):
            if q in (c.get("name") or "").lower():
                out.append({"id": c.get("id"), "name": c.get("name"), "is_private": c.get("is_private")})
                if len(out) >= limit:
                    break
        cursor = data.get("response_metadata", {}).get("next_cursor", "")
        if not cursor:
            break
    return json.dumps(out, ensure_ascii=False, indent=2)
