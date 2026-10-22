import json
import os
import re

import httpx
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from style import check, format_violations

HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.environ.get("SLACK_ENV_FILE", os.path.join(HERE, ".env")))

SLACK = "https://slack.com/api"
TOKEN_PREFIX = "SLACK_USER_TOKEN_"
# Every SLACK_USER_TOKEN_<ALIAS> in the environment is a workspace: ACME_CORP -> acme-corp.
WORKSPACE_TOKENS = {
    name[len(TOKEN_PREFIX):].lower().replace("_", "-"): (name,)
    for name in sorted(os.environ)
    if name.startswith(TOKEN_PREFIX)
}

# SLACK_STYLE_GATE=off lets sends through unchecked; slack_lint still works.
STYLE_GATE = os.environ.get("SLACK_STYLE_GATE", "on").strip().lower() not in ("0", "off", "false", "no")

mcp = FastMCP("slack-personal")


def _workspace_key(workspace: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", workspace.strip().lower()).strip("-")


def _token(workspace: str) -> str:
    key = _workspace_key(workspace)
    env_names = WORKSPACE_TOKENS.get(key)
    if not env_names:
        raise RuntimeError(
            f"Unknown Slack workspace {workspace!r}; choose one of: {', '.join(WORKSPACE_TOKENS)}"
        )
    for env_name in env_names:
        token = os.environ.get(env_name)
        if token:
            return token
    raise RuntimeError(f"Slack workspace {key!r} is not configured; set {env_names[0]}")


def _call(
    method: str,
    *,
    workspace: str,
    json_body: dict | None = None,
    params: dict | None = None,
    form: dict | None = None,
    timeout: int = 15,
) -> dict:
    headers = {"Authorization": f"Bearer {_token(workspace)}"}
    if json_body is not None:
        headers["Content-Type"] = "application/json; charset=utf-8"
        resp = httpx.post(f"{SLACK}/{method}", headers=headers, json=json_body, timeout=timeout)
    elif form is not None:
        resp = httpx.post(f"{SLACK}/{method}", headers=headers, data=form, timeout=timeout)
    else:
        resp = httpx.get(f"{SLACK}/{method}", headers=headers, params=params or {}, timeout=timeout)
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack {method} error: {data.get('error')}")
    return data


def _file_summary(f: dict) -> dict:
    return {
        "id": f.get("id"),
        "name": f.get("name"),
        "title": f.get("title"),
        "filetype": f.get("filetype"),
        "mimetype": f.get("mimetype"),
        "size": f.get("size"),
    }


def _message(m: dict) -> dict:
    out = {"ts": m.get("ts"), "user": m.get("user") or m.get("username"), "text": m.get("text")}
    if m.get("files"):
        out["files"] = [_file_summary(f) for f in m["files"]]
    return out


def _channel(workspace: str, channel_id: str) -> str:
    """Resolve a user id (U.../W...) to the DM channel with that user (needs im:write)."""
    if channel_id[:1] in ("U", "W"):
        data = _call("conversations.open", workspace=workspace, form={"users": channel_id})
        return data["channel"]["id"]
    return channel_id


@mcp.tool()
def slack_list_workspaces() -> str:
    """List configured workspace aliases and verify which Slack account each token belongs to."""
    out = []
    for workspace in WORKSPACE_TOKENS:
        try:
            data = _call("auth.test", workspace=workspace)
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
    result = check(text) if STYLE_GATE else {"errors": [], "warnings": []}
    if result["errors"] or (result["warnings"] and not allow_promise):
        return "REFUSED - rewrite before sending.\n" + format_violations(result, allow_promise)
    body: dict = {"channel": channel_id, "text": text}
    if thread_ts:
        body["thread_ts"] = thread_ts
    data = _call("chat.postMessage", workspace=workspace, json_body=body)
    return f"sent workspace={_workspace_key(workspace)} ts={data['ts']} channel={data['channel']}"


@mcp.tool()
def slack_add_reaction(workspace: str, channel_id: str, ts: str, name: str) -> str:
    """Add an emoji reaction to a message AS THE USER (reactions.add, needs reactions:write).
    ts: the message timestamp. name: emoji name without colons, e.g. eyes.
    """
    name = name.strip().strip(":")
    _call(
        "reactions.add",
        workspace=workspace,
        json_body={"channel": channel_id, "timestamp": ts, "name": name},
    )
    return f"reacted workspace={_workspace_key(workspace)} channel={channel_id} ts={ts} name={name}"


@mcp.tool()
def slack_remove_reaction(workspace: str, channel_id: str, ts: str, name: str) -> str:
    """Remove the user's emoji reaction from a message (reactions.remove, needs reactions:write)."""
    name = name.strip().strip(":")
    _call(
        "reactions.remove",
        workspace=workspace,
        json_body={"channel": channel_id, "timestamp": ts, "name": name},
    )
    return f"unreacted workspace={_workspace_key(workspace)} channel={channel_id} ts={ts} name={name}"


@mcp.tool()
def slack_upload_file(
    workspace: str,
    channel_id: str,
    file_path: str,
    initial_comment: str = "",
    title: str = "",
    thread_ts: str = "",
    allow_promise: bool = False,
) -> str:
    """Upload a local file to a Slack channel/DM AS THE USER, with an optional comment.
    Same style gate as slack_send_message applies to initial_comment.
    channel_id: C.../G.../D..., or a user id U... to upload into the DM with that person
    (your own id = your self-DM; needs im:write). thread_ts: upload as a thread reply.
    file_path: absolute path on this machine. title: file title, defaults to the filename.
    """
    if initial_comment and STYLE_GATE:
        result = check(initial_comment)
        if result["errors"] or (result["warnings"] and not allow_promise):
            return "REFUSED - rewrite the comment before sending.\n" + format_violations(result, allow_promise)

    path = os.path.expanduser(file_path)
    if not os.path.isfile(path):
        raise RuntimeError(f"No such file: {path}")
    size = os.path.getsize(path)
    if size == 0:
        raise RuntimeError(f"Refusing to upload an empty file: {path}")
    name = os.path.basename(path)
    channel_id = _channel(workspace, channel_id)

    ticket = _call(
        "files.getUploadURLExternal",
        workspace=workspace,
        form={"filename": name, "length": size},
    )
    with open(path, "rb") as handle:
        upload = httpx.post(ticket["upload_url"], files={"file": (name, handle)}, timeout=120)
    if upload.status_code != 200:
        raise RuntimeError(f"Slack upload failed: HTTP {upload.status_code}")

    body: dict = {
        "files": json.dumps([ {"id": ticket["file_id"], "title": title or name} ]),
        "channel_id": channel_id,
    }
    if initial_comment:
        body["initial_comment"] = initial_comment
    if thread_ts:
        body["thread_ts"] = thread_ts
    data = _call("files.completeUploadExternal", workspace=workspace, form=body, timeout=60)
    shared = data.get("files", [ {} ])[0]
    return (
        f"uploaded workspace={_workspace_key(workspace)} file_id={shared.get('id')} "
        f"name={name} channel={channel_id}"
    )


@mcp.tool()
def slack_file_info(workspace: str, file_id: str) -> str:
    """File metadata by id (files.info, needs files:read): name, type, size, owner, where shared."""
    f = _call("files.info", workspace=workspace, params={"file": file_id})["file"]
    out = _file_summary(f) | {
        "user": f.get("user"),
        "created": f.get("created"),
        "channels": f.get("channels", []) + f.get("groups", []) + f.get("ims", []),
        "permalink": f.get("permalink"),
    }
    return json.dumps(out, ensure_ascii=False, indent=2)


@mcp.tool()
def slack_list_files(
    workspace: str, channel_id: str = "", user_id: str = "", types: str = "", count: int = 20
) -> str:
    """List files, newest first (files.list, needs files:read). Filter by channel_id and/or
    user_id (uploader). types: comma list of all, spaces, snippets, images, gdocs, zips, pdfs.
    """
    params: dict = {"count": count}
    if channel_id:
        params["channel"] = channel_id
    if user_id:
        params["user"] = user_id
    if types:
        params["types"] = types
    data = _call("files.list", workspace=workspace, params=params)
    out = [
        _file_summary(f) | {"user": f.get("user"), "created": f.get("created")}
        for f in data.get("files", [])
    ]
    return json.dumps(out, ensure_ascii=False, indent=2)


@mcp.tool()
def slack_download_file(workspace: str, file_id: str, dest_dir: str = "~/Downloads/slack") -> str:
    """Download a Slack file by id to a local folder (needs files:read). Returns the saved path.
    Saved as <file_id>-<name> so repeated downloads overwrite the same file.
    """
    f = _call("files.info", workspace=workspace, params={"file": file_id})["file"]
    url = f.get("url_private_download") or f.get("url_private")
    if not url:
        raise RuntimeError(f"File {file_id} has no downloadable content (mode={f.get('mode')})")
    dest = os.path.expanduser(dest_dir)
    os.makedirs(dest, exist_ok=True)
    name = os.path.basename(f.get("name") or "file").lstrip(".") or "file"
    path = os.path.join(dest, f"{file_id}-{name}")
    headers = {"Authorization": f"Bearer {_token(workspace)}"}
    with httpx.stream("GET", url, headers=headers, follow_redirects=True, timeout=120) as resp:
        # Without files:read Slack answers 200 with its HTML login page instead of the file.
        if resp.status_code != 200 or (
            "text/html" in resp.headers.get("content-type", "") and f.get("filetype") != "html"
        ):
            raise RuntimeError(f"Slack download failed: HTTP {resp.status_code} {resp.headers.get('content-type')}")
        with open(path, "wb") as handle:
            for chunk in resp.iter_bytes():
                handle.write(chunk)
    return f"downloaded workspace={_workspace_key(workspace)} file_id={file_id} size={os.path.getsize(path)} path={path}"


@mcp.tool()
def slack_search_messages(workspace: str, query: str, count: int = 15) -> str:
    """Search messages you can see (search:read). Slack search syntax supported."""
    data = _call("search.messages", workspace=workspace, params={"query": query, "count": count})
    matches = data.get("messages", {}).get("matches", [])
    out = [
        {
            "channel": m.get("channel", {}).get("id"),
            "channel_name": m.get("channel", {}).get("name"),
            "user": m.get("username") or m.get("user"),
            "ts": m.get("ts"),
            "text": m.get("text"),
            "permalink": m.get("permalink"),
            **({"files": [_file_summary(f) for f in m["files"]]} if m.get("files") else {}),
        }
        for m in matches
    ]
    return json.dumps(out, ensure_ascii=False, indent=2)


@mcp.tool()
def slack_read_channel(workspace: str, channel_id: str, limit: int = 20) -> str:
    """Read recent messages in a channel/DM (conversations.history)."""
    data = _call(
        "conversations.history",
        workspace=workspace,
        params={"channel": channel_id, "limit": limit},
    )
    out = [_message(m) for m in data.get("messages", [])]
    return json.dumps(out, ensure_ascii=False, indent=2)


@mcp.tool()
def slack_read_thread(workspace: str, channel_id: str, thread_ts: str, limit: int = 50) -> str:
    """Read a full thread (conversations.replies)."""
    data = _call(
        "conversations.replies",
        workspace=workspace,
        params={"channel": channel_id, "ts": thread_ts, "limit": limit},
    )
    out = [_message(m) for m in data.get("messages", [])]
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
        data = _call("users.list", workspace=workspace, params=params)
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
        data = _call("conversations.list", workspace=workspace, params=params)
        for c in data.get("channels", []):
            if q in (c.get("name") or "").lower():
                out.append({"id": c.get("id"), "name": c.get("name"), "is_private": c.get("is_private")})
                if len(out) >= limit:
                    break
        cursor = data.get("response_metadata", {}).get("next_cursor", "")
        if not cursor:
            break
    return json.dumps(out, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    mcp.run()
