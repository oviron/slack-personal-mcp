import os
import re

import httpx

from .config import workspace_tokens

SLACK = "https://slack.com/api"


def workspace_key(workspace: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", workspace.strip().lower()).strip("-")


def token(workspace: str) -> str:
    tokens = workspace_tokens()
    key = workspace_key(workspace)
    env_names = tokens.get(key)
    if not env_names:
        raise RuntimeError(f"Unknown Slack workspace {workspace!r}; choose one of: {', '.join(tokens)}")
    for env_name in env_names:
        value = os.environ.get(env_name)
        if value:
            return value
    raise RuntimeError(f"Slack workspace {key!r} is not configured; set {env_names[0]}")


def call(
    method: str,
    *,
    workspace: str,
    json_body: dict | None = None,
    params: dict | None = None,
    form: dict | None = None,
    timeout: int = 15,
) -> dict:
    headers = {"Authorization": f"Bearer {token(workspace)}"}
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


def resolve_channel(workspace: str, channel_id: str) -> str:
    """Resolve a user id (U.../W...) to the DM channel with that user (needs im:write)."""
    if channel_id[:1] in ("U", "W"):
        data = call("conversations.open", workspace=workspace, form={"users": channel_id})
        return data["channel"]["id"]
    return channel_id


def file_summary(f: dict) -> dict:
    return {
        "id": f.get("id"),
        "name": f.get("name"),
        "title": f.get("title"),
        "filetype": f.get("filetype"),
        "mimetype": f.get("mimetype"),
        "size": f.get("size"),
    }


def message(m: dict) -> dict:
    out = {"ts": m.get("ts"), "user": m.get("user") or m.get("username"), "text": m.get("text")}
    if m.get("files"):
        out["files"] = [file_summary(f) for f in m["files"]]
    return out
