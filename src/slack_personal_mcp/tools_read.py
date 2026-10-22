import re
from typing import Annotated, Any, Literal

from pydantic import Field

from . import cache
from .app import tool
from .config import workspace_tokens
from .read_api import CONVERSATION_SCOPES, call
from .slack import SlackError, message


def _messages(workspace: str, messages: list[dict]) -> list[dict]:
    names = cache.user_names(workspace) if any(m.get("user") for m in messages) else {}
    return [message(m, names) for m in messages]


def _page(data: dict) -> dict[str, Any]:
    cursor = (data.get("response_metadata", {}).get("next_cursor") or "").strip()
    return {
        "messages": data.get("messages", []),
        "next_cursor": cursor,
        "has_more": bool(data.get("has_more") or cursor),
    }


def _read_params(channel_id: str, limit: int, cursor: str, oldest: str, latest: str, inclusive: bool) -> dict[str, Any]:
    return {"channel": channel_id, "limit": limit, "inclusive": inclusive} | {
        key: value for key, value in (("cursor", cursor), ("oldest", oldest), ("latest", latest)) if value
    }


def _compact(fields: dict) -> dict[str, Any]:
    return {key: value for key, value in fields.items() if value not in (None, "", [], {})}


def _user(user: dict, details: bool = False) -> dict[str, Any]:
    profile = user.get("profile", {})
    out = {
        "id": user.get("id"),
        "name": user.get("name"),
        "real_name": profile.get("real_name") or user.get("real_name"),
        "display_name": profile.get("display_name"),
    }
    if details:
        out |= {
            "title": profile.get("title"),
            "email": profile.get("email"),
            "tz": user.get("tz"),
            "status_text": profile.get("status_text"),
            "status_emoji": profile.get("status_emoji"),
            "is_admin": user.get("is_admin") or None,
            "is_bot": user.get("is_bot") or None,
            "deleted": user.get("deleted") or None,
        }
    return _compact(out)


@tool(write=False)
def slack_list_workspaces() -> dict[str, Any]:
    """List configured workspace aliases and verify token identities (auth.test; no extra scopes)."""
    out = []
    for workspace in workspace_tokens():
        try:
            data = call("auth.test", workspace=workspace)
            out.append(
                {"workspace": workspace, "configured": True}
                | {key: data[key] for key in ("team", "team_id", "url", "user", "user_id") if data.get(key)}
            )
        except RuntimeError as exc:
            out.append({"workspace": workspace, "configured": False, "error": str(exc)})
    return {"workspaces": out}


@tool(write=False)
def slack_search_messages(
    workspace: str,
    query: str,
    count: Annotated[int, Field(ge=1, le=100)] = 15,
    page: Annotated[int, Field(ge=1, le=100)] = 1,
    sort: Literal["score", "timestamp"] = "score",
    sort_dir: Literal["asc", "desc"] = "desc",
    in_channel: str = "",
    from_user: str = "",
    before: str = "",
    after: str = "",
    has: str = "",
) -> dict[str, Any]:
    """Search visible messages with raw Slack query and optional filters; returns total/page/pages.
    Needs search:read and users:read for cached author names; count is 1-100.
    """
    filters = (("in", in_channel), ("from", from_user), ("before", before), ("after", after), ("has", has))
    query = " ".join([query] + [f"{key}:{value}" for key, value in filters if value]).strip()
    data = call(
        "search.messages",
        workspace=workspace,
        params={
            "query": query,
            "count": count,
            "page": page,
            "sort": sort,
            "sort_dir": sort_dir,
        },
    ).get("messages", {})
    matches = data.get("matches", [])
    messages = _messages(workspace, matches)
    for projected, match in zip(messages, matches, strict=True):
        channel = match.get("channel") or {}
        projected.update(
            {
                key: value
                for key, value in {
                    "channel": channel.get("id"),
                    "channel_name": channel.get("name"),
                    "permalink": match.get("permalink"),
                }.items()
                if value
            }
        )
    paging = data.get("paging") or {}
    pagination = data.get("pagination") or {}
    total = data.get("total", paging.get("total", pagination.get("total_count", 0)))
    return {
        "messages": messages,
        "total": total,
        "page": paging.get("page", pagination.get("page", page)),
        "pages": paging.get("pages", pagination.get("page_count", (total + count - 1) // count)),
    }


@tool(write=False)
def slack_read_channel(
    workspace: str,
    channel_id: str,
    limit: Annotated[int, Field(ge=1, le=1000)] = 20,
    cursor: str = "",
    oldest: str = "",
    latest: str = "",
    inclusive: bool = False,
) -> dict[str, Any]:
    """Read one history page with cursor and Slack timestamp bounds; returns messages/next_cursor/has_more.
    Needs the conversation's channels:history, groups:history, im:history or mpim:history; users:read for names.
    """
    out = _page(
        call(
            "conversations.history",
            workspace=workspace,
            params=_read_params(channel_id, limit, cursor, oldest, latest, inclusive),
        )
    )
    out["messages"] = _messages(workspace, out["messages"])
    return out


@tool(write=False)
def slack_read_thread(
    workspace: str,
    channel_id: str,
    thread_ts: str,
    limit: Annotated[int, Field(ge=1, le=1000)] = 50,
    cursor: str = "",
    oldest: str = "",
    latest: str = "",
    inclusive: bool = False,
    all_pages: bool = False,
    max_pages: Annotated[int, Field(ge=1, le=100)] = 10,
) -> dict[str, Any]:
    """Read thread pages from cursor within timestamp bounds; all_pages stops at max_pages (default 10).
    Returns complete=false while more remain. Needs channels:history/groups:history/im:history/mpim:history; users:read.
    """
    params = _read_params(channel_id, limit, cursor, oldest, latest, inclusive) | {"ts": thread_ts}
    messages = []
    seen = {cursor}
    for _ in range(max_pages if all_pages else 1):
        out = _page(call("conversations.replies", workspace=workspace, params=params))
        messages.extend(out["messages"])
        cursor = out["next_cursor"]
        if not out["has_more"] or not cursor or cursor in seen:
            break
        seen.add(cursor)
        params["cursor"] = cursor
    out["messages"] = _messages(workspace, messages)
    out["complete"] = not out["has_more"]
    return out


@tool(write=False)
def slack_find_user(
    workspace: str,
    query: str,
    limit: Annotated[int, Field(ge=1)] = 10,
    refresh: bool = False,
) -> dict[str, Any]:
    """Find people by name via cached users.list (users:read); refresh bypasses the one-hour cache.
    Exact email uses users.lookupByEmail (users:read.email); U.../W... uses users.info (users:read).
    """
    query = query.strip()
    if re.fullmatch(r"[^\s@]+@[^\s@]+", query):
        try:
            users = [call("users.lookupByEmail", workspace=workspace, params={"email": query})["user"]]
        except SlackError as exc:
            if exc.error != "users_not_found":
                raise
            users = []
    elif re.fullmatch(r"[UW][A-Z0-9]+", query):
        users = [call("users.info", workspace=workspace, params={"user": query})["user"]]
    else:
        users = [
            user
            for user in cache.directory.users(workspace, refresh)
            if query.casefold()
            in " ".join(
                filter(
                    None,
                    [
                        user.get("name"),
                        user.get("real_name"),
                        user.get("profile", {}).get("real_name"),
                        user.get("profile", {}).get("display_name"),
                        user.get("profile", {}).get("email"),
                    ],
                )
            ).casefold()
        ]
    return {"users": [_user(user) for user in users if not user.get("deleted") and not user.get("is_bot")][:limit]}


@tool(write=False)
def slack_find_channel(
    workspace: str,
    query: str,
    limit: Annotated[int, Field(ge=1)] = 15,
    refresh: bool = False,
    types: str = "public_channel,private_channel",
) -> dict[str, Any]:
    """Find non-archived conversations by name; types: public_channel,private_channel,im,mpim (comma-separated).
    Needs channels:read/groups:read/im:read/mpim:read by type; users:read for DM names. Refresh reloads the cache.
    """
    requested = {part.strip() for part in types.split(",")}
    if not requested or not requested <= CONVERSATION_SCOPES.keys():
        raise ValueError("types must be a comma-separated list of public_channel, private_channel, im, mpim")
    types = ",".join(kind for kind in CONVERSATION_SCOPES if kind in requested)
    channels = cache.directory.channels(workspace, types, refresh)
    names = cache.user_names(workspace, refresh) if any(c.get("is_im") and c.get("user") for c in channels) else {}
    out = []
    for channel in channels:
        name = names.get(channel.get("user")) if channel.get("is_im") else channel.get("name")
        if query.casefold() in (name or "").casefold():
            out.append(
                {
                    key: value
                    for key, value in {
                        "id": channel.get("id"),
                        "name": name,
                        "is_private": channel.get("is_private"),
                        "is_im": channel.get("is_im"),
                        "is_mpim": channel.get("is_mpim"),
                        "user_id": channel.get("user"),
                    }.items()
                    if value not in (None, "")
                }
            )
            if len(out) >= limit:
                break
    return {"channels": out}


@tool(write=False)
def slack_user_info(workspace: str, user_id: str) -> dict[str, Any]:
    """A user's name, title, email, timezone, status and role via users.info.
    Needs users:read, and users:read.email for the email."""
    return {"user": _user(call("users.info", workspace=workspace, params={"user": user_id})["user"], details=True)}


@tool(write=False)
def slack_channel_info(workspace: str, channel_id: str) -> dict[str, Any]:
    """Name, kind, topic, purpose, member count and your read marker via conversations.info;
    needs channels:read/groups:read/im:read/mpim:read for the conversation type."""
    channel = call("conversations.info", workspace=workspace, params={"channel": channel_id})["channel"]
    keys = ("id", "name", "is_private", "is_im", "is_mpim", "is_archived", "user", "num_members", "last_read")
    out = {key: channel.get(key) for key in keys} | {
        "topic": (channel.get("topic") or {}).get("value"),
        "purpose": (channel.get("purpose") or {}).get("value"),
    }
    return {"channel": _compact(out)}
