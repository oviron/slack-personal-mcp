from .app import mcp
from .config import style_gate_enabled
from .slack import call, workspace_key
from .style import check, format_violations


@mcp.tool()
def slack_lint(text: str) -> str:
    """Dry-run the style gate on a draft without sending. Returns OK or the violations."""
    result = check(text)
    if not result["errors"] and not result["warnings"]:
        return "OK - clean"
    return format_violations(result, allow_promise=False)


@mcp.tool()
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
    result = check(text) if style_gate_enabled() else {"errors": [], "warnings": []}
    if result["errors"] or (result["warnings"] and not allow_promise):
        return "REFUSED - rewrite before sending.\n" + format_violations(result, allow_promise)
    body: dict = {"channel": channel_id, "text": text}
    if thread_ts:
        body["thread_ts"] = thread_ts
    data = call("chat.postMessage", workspace=workspace, json_body=body)
    return f"sent workspace={workspace_key(workspace)} ts={data['ts']} channel={data['channel']}"


@mcp.tool()
def slack_add_reaction(workspace: str, channel_id: str, ts: str, name: str) -> str:
    """Add an emoji reaction to a message AS THE USER (reactions.add, needs reactions:write).
    ts: the message timestamp. name: emoji name without colons, e.g. eyes.
    """
    name = name.strip().strip(":")
    call(
        "reactions.add",
        workspace=workspace,
        json_body={"channel": channel_id, "timestamp": ts, "name": name},
    )
    return f"reacted workspace={workspace_key(workspace)} channel={channel_id} ts={ts} name={name}"


@mcp.tool()
def slack_remove_reaction(workspace: str, channel_id: str, ts: str, name: str) -> str:
    """Remove the user's emoji reaction from a message (reactions.remove, needs reactions:write)."""
    name = name.strip().strip(":")
    call(
        "reactions.remove",
        workspace=workspace,
        json_body={"channel": channel_id, "timestamp": ts, "name": name},
    )
    return f"unreacted workspace={workspace_key(workspace)} channel={channel_id} ts={ts} name={name}"
