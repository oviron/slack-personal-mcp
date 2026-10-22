from typing import Any

from mcp.server.fastmcp.exceptions import ToolError

from .app import tool
from .config import style_gate_enabled
from .slack import call, warnings, workspace_key, write_target
from .style import check, format_violations

MAX_TEXT = 40_000  # Slack truncates longer text
MAX_MARKDOWN = 12_000  # markdown_text limit


def enforce_style(text: str, allow_promise: bool, what: str = "the text") -> None:
    """Raise unless the style gate passes (or is switched off)."""
    if not style_gate_enabled():
        return
    result = check(text)
    if result["errors"] or (result["warnings"] and not allow_promise):
        raise ToolError(f"REFUSED - rewrite {what} before sending.\n" + format_violations(result, allow_promise))


def message_text(text: str, format: str, allow_promise: bool) -> dict[str, str]:
    """Check format, length and style; return the text under the field Slack expects for that format."""
    if format not in ("markdown", "mrkdwn"):
        raise ToolError(f"format must be markdown or mrkdwn, not {format!r}")
    limit = MAX_MARKDOWN if format == "markdown" else MAX_TEXT
    if len(text) > limit:
        raise ToolError(
            f"Text is {len(text)} characters; Slack takes up to {limit} as {format}. Split it or upload a file."
        )
    enforce_style(text, allow_promise)
    return {"markdown_text" if format == "markdown" else "text": text}


def with_warnings(out: dict, data: dict) -> dict[str, Any]:
    found = warnings(data)
    return out | {"warnings": found} if found else out


@tool(write=False, local=True)
def slack_lint(text: str) -> dict[str, Any]:
    """Dry-run the style gate on a draft without sending. clean=true means it would pass."""
    result = check(text)
    return {"clean": not result["errors"] and not result["warnings"], **result}


@tool(write=True)
def slack_send_message(
    workspace: str,
    channel_id: str,
    text: str,
    thread_ts: str = "",
    reply_broadcast: bool = False,
    format: str = "markdown",
    unfurl_links: bool = False,
    allow_promise: bool = False,
) -> dict[str, Any]:
    """Post a Slack message AS THE USER (no app badge), via personal user token.
    Style gate (enforced before send unless SLACK_STYLE_GATE=off): no AI typography
    (em/en dash, ellipsis char, curly quotes, nbsp, bullet) - use plain ASCII; no AI-slop
    phrases; no concrete promises/deadlines unless allow_promise=true. Write short and
    plain, in the user's own voice and language.
    workspace: explicit alias from slack_list_workspaces. channel_id: C.../G.../D..., or a user
    id U... to write into the DM with that person (needs im:write).
    thread_ts: reply in a thread. reply_broadcast: with thread_ts, also show the reply in the channel.
    format: markdown (default; standard Markdown, up to 12,000 chars) or mrkdwn (Slack's own:
    *bold*, <url|label>). unfurl_links: show link previews (off by default).
    """
    if reply_broadcast and not thread_ts:
        raise ToolError("reply_broadcast needs thread_ts")
    content = message_text(text, format, allow_promise)
    channel = write_target(workspace, channel_id)
    body: dict = {"channel": channel, "unfurl_links": unfurl_links} | content
    if thread_ts:
        body["thread_ts"] = thread_ts
        if reply_broadcast:
            body["reply_broadcast"] = True
    data = call("chat.postMessage", workspace=workspace, json_body=body)
    out = {"workspace": workspace_key(workspace), "channel": data["channel"], "ts": data["ts"]}
    return with_warnings(out, data)


def _react(method: str, workspace: str, channel_id: str, ts: str, name: str) -> dict[str, Any]:
    name = name.strip().strip(":")
    channel = write_target(workspace, channel_id)
    call(method, workspace=workspace, json_body={"channel": channel, "timestamp": ts, "name": name})
    return {"workspace": workspace_key(workspace), "channel": channel, "ts": ts, "name": name}


@tool(write=True, idempotent=True)
def slack_add_reaction(workspace: str, channel_id: str, ts: str, name: str) -> dict[str, Any]:
    """Add an emoji reaction to a message AS THE USER (reactions.add, needs reactions:write).
    channel_id: as in slack_send_message. ts: the message timestamp. name: emoji name without colons, e.g. eyes.
    """
    return _react("reactions.add", workspace, channel_id, ts, name)


@tool(write=True, idempotent=True, destructive=True)
def slack_remove_reaction(workspace: str, channel_id: str, ts: str, name: str) -> dict[str, Any]:
    """Remove the user's emoji reaction from a message (reactions.remove, needs reactions:write)."""
    return _react("reactions.remove", workspace, channel_id, ts, name)
