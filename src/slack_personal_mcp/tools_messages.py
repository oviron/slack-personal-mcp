import datetime as dt
import time
from typing import Any

from mcp.server.fastmcp.exceptions import ToolError

from .app import tool
from .slack import SlackError, call, workspace_key, write_target
from .tools_write import enforce_style, message_text, with_warnings

MAX_SCHEDULE_DAYS = 120  # Slack schedules up to 120 days ahead
MAX_UNREAD_CHANNELS = 20


def iso(seconds: float | str) -> str:
    return dt.datetime.fromtimestamp(float(seconds), dt.timezone.utc).isoformat(timespec="seconds")


def _own_message(workspace: str, channel: str, ts: str) -> dict[str, Any]:
    """Return the message at ts, refusing unless the user wrote it: an admin token could touch anyone's."""
    me = call("auth.test", workspace=workspace)["user_id"]
    params = {"channel": channel, "ts": ts, "oldest": ts, "latest": ts, "inclusive": "true"}
    try:
        messages = call("conversations.replies", workspace=workspace, params=params).get("messages", [])
    except SlackError as exc:
        if exc.error != "thread_not_found":
            raise
        messages = []
    found = next((m for m in messages if m.get("ts") == ts), None)
    if found is None:
        raise ToolError(f"No message {ts} in {channel}")
    if found.get("user") != me:
        raise ToolError(f"Message {ts} was posted by {found.get('user') or found.get('bot_id')}, not by you")
    return found


@tool(write=False)
def slack_get_permalink(workspace: str, channel_id: str, ts: str) -> dict[str, Any]:
    """Link to a message (chat.getPermalink): opens it in Slack, inside its thread for replies."""
    data = call("chat.getPermalink", workspace=workspace, params={"channel": channel_id, "message_ts": ts})
    return {"channel": channel_id, "ts": ts, "permalink": data.get("permalink")}


@tool(write=True, idempotent=True)
def slack_edit_message(
    workspace: str, channel_id: str, ts: str, text: str, format: str = "markdown", allow_promise: bool = False
) -> dict[str, Any]:
    """Replace the text of one of YOUR messages (chat.update, chat:write). Messages by others are
    refused even with an admin token. Same style gate and format as slack_send_message.
    """
    content = message_text(text, format, allow_promise)
    channel = write_target(workspace, channel_id)
    _own_message(workspace, channel, ts)
    data = call("chat.update", workspace=workspace, json_body={"channel": channel, "ts": ts} | content)
    return with_warnings({"workspace": workspace_key(workspace), "channel": channel, "ts": ts, "edited": True}, data)


@tool(write=True, destructive=True)
def slack_delete_message(workspace: str, channel_id: str, ts: str) -> dict[str, Any]:
    """Delete one of YOUR messages (chat.delete, chat:write). Messages by others are refused even
    with an admin token. Cannot be undone.
    """
    channel = write_target(workspace, channel_id)
    _own_message(workspace, channel, ts)
    call("chat.delete", workspace=workspace, json_body={"channel": channel, "ts": ts})
    return {"workspace": workspace_key(workspace), "channel": channel, "ts": ts, "deleted": True}


def post_time(post_at: str) -> int:
    """Unix seconds from unix seconds or ISO 8601 with a timezone; must be in the next 120 days."""
    value = post_at.strip()
    if value.replace(".", "", 1).isdigit():
        when = float(value)
    else:
        try:
            parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00").replace("z", "+00:00"))
        except ValueError:
            raise ToolError(f"post_at {post_at!r} is neither unix seconds nor ISO 8601") from None
        if parsed.tzinfo is None:
            raise ToolError(f"post_at {post_at!r} needs a timezone, e.g. 2026-10-23T09:00:00+03:00")
        when = parsed.timestamp()
    now = time.time()
    if when <= now:
        raise ToolError(f"post_at {iso(when)} is in the past")
    if when > now + MAX_SCHEDULE_DAYS * 86400:
        raise ToolError(f"post_at {iso(when)} is more than {MAX_SCHEDULE_DAYS} days ahead")
    return int(when)


@tool(write=True)
def slack_schedule_message(
    workspace: str,
    channel_id: str,
    text: str,
    post_at: str,
    thread_ts: str = "",
    reply_broadcast: bool = False,
    format: str = "markdown",
    allow_promise: bool = False,
) -> dict[str, Any]:
    """Schedule a message AS THE USER (chat.scheduleMessage, chat:write). post_at: ISO 8601 with
    a timezone (2026-10-23T09:00:00+03:00) or unix seconds, up to 120 days ahead. Other arguments
    and the style gate as in slack_send_message.
    """
    if reply_broadcast and not thread_ts:
        raise ToolError("reply_broadcast needs thread_ts")
    content = message_text(text, format, allow_promise)
    when = post_time(post_at)
    channel = write_target(workspace, channel_id)
    body: dict = {"channel": channel, "post_at": when} | content
    if thread_ts:
        body["thread_ts"] = thread_ts
        if reply_broadcast:
            body["reply_broadcast"] = True
    data = call("chat.scheduleMessage", workspace=workspace, json_body=body)
    out = {
        "workspace": workspace_key(workspace),
        "channel": data.get("channel", channel),
        "scheduled_message_id": data.get("scheduled_message_id"),
        "post_at": iso(data.get("post_at", when)),
    }
    return with_warnings(out, data)


@tool(write=False)
def slack_list_scheduled(workspace: str, channel_id: str = "", limit: int = 100, cursor: str = "") -> dict[str, Any]:
    """Your messages scheduled but not yet sent (chat.scheduledMessages.list), soonest first within
    a page. Pass next_cursor as cursor for the next page.
    """
    params: dict = {"limit": limit}
    if channel_id:
        params["channel"] = channel_id
    if cursor:
        params["cursor"] = cursor
    data = call("chat.scheduledMessages.list", workspace=workspace, params=params)
    items = sorted(data.get("scheduled_messages", []), key=lambda m: m.get("post_at", 0))
    return {
        "scheduled": [
            {"id": m.get("id"), "channel": m.get("channel_id"), "post_at": iso(m["post_at"]), "text": m.get("text")}
            for m in items
            if m.get("post_at")
        ],
        "next_cursor": (data.get("response_metadata") or {}).get("next_cursor") or None,
    }


@tool(write=True, idempotent=True, destructive=True)
def slack_cancel_scheduled(workspace: str, channel_id: str, scheduled_message_id: str) -> dict[str, Any]:
    """Cancel a scheduled message (chat.deleteScheduledMessage, chat:write)."""
    channel = write_target(workspace, channel_id)
    call(
        "chat.deleteScheduledMessage",
        workspace=workspace,
        json_body={"channel": channel, "scheduled_message_id": scheduled_message_id},
    )
    return {"workspace": workspace_key(workspace), "channel": channel, "cancelled": scheduled_message_id}


@tool(write=True, idempotent=True)
def slack_set_status(
    workspace: str, text: str = "", emoji: str = "", expires_in_minutes: int = 0, allow_promise: bool = False
) -> dict[str, Any]:
    """Set the user's Slack status (users.profile.set, needs users.profile:write). Empty text and
    emoji clear it. emoji: name like palm_tree. expires_in_minutes: 0 keeps it until changed.
    The style gate applies to text: everyone in the workspace sees it.
    """
    if text:
        enforce_style(text, allow_promise, "the status")
    emoji = emoji.strip()
    if emoji and not emoji.startswith(":"):
        emoji = f":{emoji.strip(':')}:"
    expiration = int(time.time()) + expires_in_minutes * 60 if expires_in_minutes > 0 else 0
    profile = {"status_text": text, "status_emoji": emoji, "status_expiration": expiration}
    call("users.profile.set", workspace=workspace, json_body={"profile": profile})
    out = {"workspace": workspace_key(workspace), "status_text": text, "status_emoji": emoji}
    return out | {"expires": iso(expiration)} if expiration else out


@tool(write=False)
def slack_unread(workspace: str, channel_ids: list[str], limit: int = 50) -> dict[str, Any]:
    """How many messages arrived after your read marker in each of up to 20 channels/DMs
    (conversations.info + conversations.history). Slack's API has no full unread inbox like the app,
    so name the channels. count stops at limit; has_more tells there are more.
    """
    if not channel_ids or len(channel_ids) > MAX_UNREAD_CHANNELS:
        raise ToolError(f"Give 1 to {MAX_UNREAD_CHANNELS} channel ids")
    me = call("auth.test", workspace=workspace)["user_id"]
    out = []
    for channel in channel_ids:
        try:
            info = call("conversations.info", workspace=workspace, params={"channel": channel})["channel"]
            last_read = info.get("last_read")
            if not last_read:
                out.append({"channel": channel, "error": "Slack returned no read marker (last_read) for it"})
                continue
            params = {"channel": channel, "oldest": last_read, "limit": limit}
            data = call("conversations.history", workspace=workspace, params=params)
        except SlackError as exc:
            out.append({"channel": channel, "error": str(exc)})
            continue
        unread = [m for m in data.get("messages", []) if m.get("user") != me]
        row = {"channel": channel, "name": info.get("name"), "unread": len(unread), "has_more": data.get("has_more")}
        if last_read != "0000000000.000000":
            row["last_read"] = iso(last_read)
        out.append({k: v for k, v in row.items() if v is not None})
    return {"channels": out}


@tool(write=True, idempotent=True)
def slack_mark_read(workspace: str, channel_id: str, ts: str) -> dict[str, Any]:
    """Move your read marker in a channel/DM to ts (conversations.mark; needs channels:write,
    groups:write, im:write or mpim:write for that kind of conversation).
    """
    channel = write_target(workspace, channel_id)
    call("conversations.mark", workspace=workspace, json_body={"channel": channel, "ts": ts})
    return {"workspace": workspace_key(workspace), "channel": channel, "marked": ts}
