from mcp.server.fastmcp.exceptions import ToolError

from .app import tool
from .config import style_gate_enabled
from .slack import call, check_write_target, warnings, workspace_key
from .style import check, format_violations

MAX_TEXT = 40_000  # Slack truncates longer text


def enforce_style(text: str, allow_promise: bool, what: str = "the text") -> None:
    """Raise unless the style gate passes (or is switched off)."""
    if not style_gate_enabled():
        return
    result = check(text)
    if result["errors"] or (result["warnings"] and not allow_promise):
        raise ToolError(f"REFUSED - rewrite {what} before sending.\n" + format_violations(result, allow_promise))


def with_warnings(line: str, data: dict) -> str:
    found = warnings(data)
    return f"{line} warnings={','.join(found)}" if found else line


@tool(write=False, local=True)
def slack_lint(text: str) -> str:
    """Dry-run the style gate on a draft without sending. Returns OK or the violations."""
    result = check(text)
    if not result["errors"] and not result["warnings"]:
        return "OK - clean"
    return format_violations(result, allow_promise=False)


@tool(write=True)
def slack_send_message(
    workspace: str,
    channel_id: str,
    text: str,
    thread_ts: str = "",
    allow_promise: bool = False,
) -> str:
    """Post a Slack message AS THE USER (no app badge), via personal user token.
    Style gate (enforced before send unless SLACK_STYLE_GATE=off): no AI typography
    (em/en dash, ellipsis char, curly quotes, nbsp, bullet) - use plain ASCII; no AI-slop
    phrases; no concrete promises/deadlines unless allow_promise=true. Write short and
    plain, in the user's own voice and language.
    workspace: explicit alias from slack_list_workspaces. channel_id: channel/DM id
    (e.g. C..., D...). thread_ts: reply in a thread if set.
    """
    if len(text) > MAX_TEXT:
        raise ToolError(f"Text is {len(text)} characters; Slack truncates past {MAX_TEXT}. Split it or upload a file.")
    enforce_style(text, allow_promise)
    check_write_target(channel_id)
    body: dict = {"channel": channel_id, "text": text}
    if thread_ts:
        body["thread_ts"] = thread_ts
    data = call("chat.postMessage", workspace=workspace, json_body=body)
    return with_warnings(f"sent workspace={workspace_key(workspace)} ts={data['ts']} channel={data['channel']}", data)


@tool(write=True, idempotent=True)
def slack_add_reaction(workspace: str, channel_id: str, ts: str, name: str) -> str:
    """Add an emoji reaction to a message AS THE USER (reactions.add, needs reactions:write).
    ts: the message timestamp. name: emoji name without colons, e.g. eyes.
    """
    name = name.strip().strip(":")
    check_write_target(channel_id)
    call(
        "reactions.add",
        workspace=workspace,
        json_body={"channel": channel_id, "timestamp": ts, "name": name},
    )
    return f"reacted workspace={workspace_key(workspace)} channel={channel_id} ts={ts} name={name}"


@tool(write=True, idempotent=True, destructive=True)
def slack_remove_reaction(workspace: str, channel_id: str, ts: str, name: str) -> str:
    """Remove the user's emoji reaction from a message (reactions.remove, needs reactions:write)."""
    name = name.strip().strip(":")
    check_write_target(channel_id)
    call(
        "reactions.remove",
        workspace=workspace,
        json_body={"channel": channel_id, "timestamp": ts, "name": name},
    )
    return f"unreacted workspace={workspace_key(workspace)} channel={channel_id} ts={ts} name={name}"
