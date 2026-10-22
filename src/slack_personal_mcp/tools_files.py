import json
import os

import httpx

from .app import tool
from .slack import call, check_write_target, file_summary, resolve_channel, token, workspace_key
from .tools_write import enforce_style


@tool(write=True)
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
    if initial_comment:
        enforce_style(initial_comment, allow_promise, "the comment")

    path = os.path.expanduser(file_path)
    if not os.path.isfile(path):
        raise RuntimeError(f"No such file: {path}")
    size = os.path.getsize(path)
    if size == 0:
        raise RuntimeError(f"Refusing to upload an empty file: {path}")
    name = os.path.basename(path)
    check_write_target(channel_id)
    channel_id = resolve_channel(workspace, channel_id)

    ticket = call(
        "files.getUploadURLExternal",
        workspace=workspace,
        form={"filename": name, "length": size},
    )
    with open(path, "rb") as handle:
        upload = httpx.post(ticket["upload_url"], files={"file": (name, handle)}, timeout=120)
    if upload.status_code != 200:
        raise RuntimeError(f"Slack upload failed: HTTP {upload.status_code}")

    body: dict = {
        "files": json.dumps([{"id": ticket["file_id"], "title": title or name}]),
        "channel_id": channel_id,
    }
    if initial_comment:
        body["initial_comment"] = initial_comment
    if thread_ts:
        body["thread_ts"] = thread_ts
    data = call("files.completeUploadExternal", workspace=workspace, form=body, timeout=60)
    shared = data.get("files", [{}])[0]
    return f"uploaded workspace={workspace_key(workspace)} file_id={shared.get('id')} name={name} channel={channel_id}"


@tool(write=False)
def slack_file_info(workspace: str, file_id: str) -> str:
    """File metadata by id (files.info, needs files:read): name, type, size, owner, where shared."""
    f = call("files.info", workspace=workspace, params={"file": file_id})["file"]
    out = file_summary(f) | {
        "user": f.get("user"),
        "created": f.get("created"),
        "channels": f.get("channels", []) + f.get("groups", []) + f.get("ims", []),
        "permalink": f.get("permalink"),
    }
    return json.dumps(out, ensure_ascii=False, indent=2)


@tool(write=False)
def slack_list_files(workspace: str, channel_id: str = "", user_id: str = "", types: str = "", count: int = 20) -> str:
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
    data = call("files.list", workspace=workspace, params=params)
    out = [file_summary(f) | {"user": f.get("user"), "created": f.get("created")} for f in data.get("files", [])]
    return json.dumps(out, ensure_ascii=False, indent=2)


@tool(write=True, destructive=True)
def slack_download_file(workspace: str, file_id: str, dest_dir: str = "~/Downloads/slack") -> str:
    """Download a Slack file by id to a local folder (needs files:read). Returns the saved path.
    Saved as <file_id>-<name> so repeated downloads overwrite the same file.
    """
    f = call("files.info", workspace=workspace, params={"file": file_id})["file"]
    url = f.get("url_private_download") or f.get("url_private")
    if not url:
        raise RuntimeError(f"File {file_id} has no downloadable content (mode={f.get('mode')})")
    dest = os.path.expanduser(dest_dir)
    os.makedirs(dest, exist_ok=True)
    name = os.path.basename(f.get("name") or "file").lstrip(".") or "file"
    path = os.path.join(dest, f"{file_id}-{name}")
    headers = {"Authorization": f"Bearer {token(workspace)}"}
    with httpx.stream("GET", url, headers=headers, follow_redirects=True, timeout=120) as resp:
        # Without files:read Slack answers 200 with its HTML login page instead of the file.
        if resp.status_code != 200 or (
            "text/html" in resp.headers.get("content-type", "") and f.get("filetype") != "html"
        ):
            raise RuntimeError(f"Slack download failed: HTTP {resp.status_code} {resp.headers.get('content-type')}")
        with open(path, "wb") as handle:
            for chunk in resp.iter_bytes():
                handle.write(chunk)
    return f"downloaded workspace={workspace_key(workspace)} file_id={file_id} size={os.path.getsize(path)} path={path}"
