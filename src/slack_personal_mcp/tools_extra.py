from typing import Annotated, Any, Literal

from pydantic import Field

from . import cache
from .app import tool
from .read_api import call
from .slack import SlackError

_SEARCH_FIELDS = {
    "messages": (
        "author_user_id",
        "author_name",
        "team_id",
        "channel_id",
        "channel_name",
        "message_ts",
        "content",
        "is_author_bot",
        "permalink",
    ),
    "files": (
        "file_id",
        "title",
        "file_type",
        "content",
        "uploader_user_id",
        "author_user_id",
        "author_name",
        "team_id",
        "date_created",
        "date_updated",
        "permalink",
    ),
    "channels": (
        "channel_id",
        "id",
        "name",
        "topic",
        "purpose",
        "team_id",
        "creator_user_id",
        "creator_name",
        "date_created",
        "date_updated",
        "permalink",
    ),
    "users": ("user_id", "full_name", "title", "timezone", "email", "permalink"),
}


def _rts(workspace: str, body: dict) -> dict[str, Any]:
    try:
        return call("assistant.search.context", workspace=workspace, json_body=body)
    except SlackError as exc:
        if exc.error in ("not_allowed", "feature_not_enabled"):
            raise SlackError(
                f"Slack assistant.search.context: {exc.error}. "
                "Real-time Search API is unavailable for this app/workspace. "
                "Check workspace feature availability and app eligibility (internal or directory-published apps). "
                "Use slack_search_messages for legacy message search.",
                error=exc.error,
            ) from None
        raise


def _project(item: dict, fields: tuple[str, ...]) -> dict[str, Any]:
    return {key: item[key] for key in fields if item.get(key) not in (None, "", [], {})}


@tool(write=False)
def slack_list_emoji(workspace: str, query: str = "") -> dict[str, Any]:
    """List custom emoji by case-insensitive name substring, with URLs or alias targets. Needs emoji:read."""
    emoji = call("emoji.list", workspace=workspace).get("emoji", {})
    return {
        "emoji": [
            {"name": name} | ({"alias": value[6:]} if value.startswith("alias:") else {"url": value})
            for name, value in emoji.items()
            if query.casefold() in name.casefold()
        ]
    }


@tool(write=False)
def slack_list_usergroups(workspace: str, include_users: bool = False) -> dict[str, Any]:
    """List user groups and counts; include_users adds member IDs. Needs usergroups:read."""
    data = call(
        "usergroups.list",
        workspace=workspace,
        params={"include_users": str(include_users).lower(), "include_count": "true"},
    )
    return {
        "usergroups": [
            _project(group, ("id", "name", "handle", "description", "user_count"))
            | ({"users": group.get("users", [])} if include_users else {})
            for group in data.get("usergroups", [])
        ]
    }


@tool(write=False)
def slack_get_reactions(workspace: str, channel_id: str, ts: str) -> dict[str, Any]:
    """Get message reaction names, counts and users; users_complete flags partial lists.
    Needs reactions:read and users:read for cached user names.
    """
    data = call("reactions.get", workspace=workspace, params={"channel": channel_id, "timestamp": ts, "full": "true"})
    reactions = data.get("message", {}).get("reactions", [])
    names = cache.user_names(workspace) if any(reaction.get("users") for reaction in reactions) else {}
    return {
        "channel_id": channel_id,
        "ts": ts,
        "reactions": [
            {
                "name": reaction["name"],
                "count": reaction["count"],
                "users": [
                    {"user_id": user_id, "user_name": names.get(user_id)} for user_id in reaction.get("users", [])
                ],
                "users_complete": len(reaction.get("users", [])) == reaction["count"],
            }
            for reaction in reactions
        ],
    }


@tool(write=False)
def slack_read_canvas(
    workspace: str, canvas_id: str, content_type: Literal["markdown", "html"] = "markdown"
) -> dict[str, Any]:
    """Read full canvas content as Markdown (default) or HTML via canvases.getContent. Needs canvases:read."""
    data = call(
        "canvases.getContent", workspace=workspace, json_body={"canvas_id": canvas_id, "content_type": content_type}
    )
    return {"canvas_id": canvas_id, "content_type": content_type, "content": data["content"]}


@tool(write=False)
def slack_search(
    workspace: str,
    query: str,
    content_types: Annotated[list[Literal["messages", "files", "channels", "users"]], Field(min_length=1)]
    | None = None,
    cursor: str = "",
    limit: Annotated[int, Field(ge=1, le=20)] = 20,
    channel_types: Annotated[list[Literal["public_channel", "private_channel", "im", "mpim"]], Field(min_length=1)]
    | None = None,
) -> dict[str, Any]:
    """Search with RTS and cursor; type lists default to messages in public_channel.
    Needs search:read.public; optionally search:read.private, search:read.im, search:read.mpim,
    search:read.files, search:read.users. User tokens need no action_token.
    """
    data = _rts(
        workspace,
        {
            "query": query,
            "content_types": content_types if content_types is not None else ["messages"],
            "channel_types": channel_types if channel_types is not None else ["public_channel"],
            "limit": limit,
        }
        | ({"cursor": cursor} if cursor else {}),
    )
    results = data.get("results", {})
    next_cursor = (data.get("response_metadata", {}).get("next_cursor") or "").strip()
    return {
        "results": {
            kind: [_project(item, fields) for item in results[kind]]
            for kind, fields in _SEARCH_FIELDS.items()
            if kind in results
        },
        "next_cursor": next_cursor,
        "has_more": bool(next_cursor),
    }
