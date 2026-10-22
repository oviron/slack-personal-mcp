import contextlib
import json
import os
import stat
import tempfile

import httpx
from mcp.server.fastmcp.exceptions import ToolError

from .app import tool
from .config import file_roots
from .slack import OutcomeUnknown, SlackError, call, client, file_summary, token, workspace_key, write_target
from .tools_write import enforce_style

MAX_FILE = 1 << 30  # Slack's own upload limit is 1 GB


def allowed_path(path: str) -> str:
    """Resolve an absolute path (symlinks included) and refuse it outside SLACK_FILE_ROOTS,
    under a hidden file or folder, or in ~/Library unless the roots were set explicitly."""
    expanded = os.path.expanduser(path)
    if not os.path.isabs(expanded):
        raise ToolError(f"Path must be absolute: {path}")
    real = os.path.realpath(expanded)
    roots, explicit = file_roots()
    for root in roots:
        if real != root and not real.startswith(root.rstrip(os.sep) + os.sep):
            continue
        parts = [] if real == root else os.path.relpath(real, root).split(os.sep)
        if any(p.startswith(".") for p in parts):
            raise ToolError(f"Refusing {path}: hidden files and folders are off limits")
        if not explicit and root == os.path.realpath(os.path.expanduser("~")) and parts[:1] == ["Library"]:
            raise ToolError(f"Refusing {path}: ~/Library is off limits")
        return real
    raise ToolError(f"Refusing {path}: outside SLACK_FILE_ROOTS ({os.pathsep.join(roots)})")


def check_slack_url(url: str) -> None:
    """The token goes only to Slack over https."""
    parsed = httpx.URL(url)
    host = parsed.host or ""
    if parsed.scheme != "https" or not (host == "slack.com" or host.endswith(".slack.com")):
        raise ToolError(f"Refusing to send the token to {parsed.scheme}://{host}")


@tool(write=True)
def slack_upload_file(
    workspace: str,
    channel_id: str,
    file_path: str,
    initial_comment: str = "",
    title: str = "",
    thread_ts: str = "",
    allow_promise: bool = False,
) -> dict:
    """Upload a local file to a Slack channel/DM AS THE USER, with an optional comment.
    Same style gate as slack_send_message applies to initial_comment and title.
    channel_id: C.../G.../D..., or a user id U... to upload into the DM with that person
    (your own id = your self-DM; needs im:write). thread_ts: upload as a thread reply.
    file_path: absolute path inside SLACK_FILE_ROOTS (default: home and temp folders; hidden
    files and ~/Library refused). title: file title, defaults to the filename.
    """
    for text, what in ((initial_comment, "the comment"), (title, "the title")):
        if text:
            enforce_style(text, allow_promise, what)
    real = allowed_path(file_path)
    name = os.path.basename(os.path.expanduser(file_path))
    try:
        fd = os.open(real, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise ToolError(f"Cannot open {file_path}: {exc.strerror}") from None
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode):
        os.close(fd)
        raise ToolError(f"Not a regular file: {file_path}")
    with os.fdopen(fd, "rb") as handle:
        if info.st_size == 0:
            raise ToolError(f"Refusing to upload an empty file: {file_path}")
        if info.st_size > MAX_FILE:
            raise ToolError(f"{file_path} is {info.st_size} bytes; Slack takes up to {MAX_FILE}")
        channel = write_target(workspace, channel_id)

        ticket = call(
            "files.getUploadURLExternal", workspace=workspace, form={"filename": name, "length": info.st_size}
        )
        file_id = ticket["file_id"]
        try:
            upload = client().post(ticket["upload_url"], files={"file": (name, handle)}, timeout=300)
        except httpx.HTTPError as exc:
            upload = None
            failure = type(exc).__name__
        else:
            failure = f"HTTP {upload.status_code}"
        if upload is None or upload.status_code != 200:
            raise SlackError(f"Upload of {name} failed while sending bytes ({failure}); nothing was shared. {file_id=}")

    body: dict = {"files": json.dumps([{"id": file_id, "title": title or name}]), "channel_id": channel}
    if initial_comment:
        body["initial_comment"] = initial_comment
    if thread_ts:
        body["thread_ts"] = thread_ts
    try:
        data = call("files.completeUploadExternal", workspace=workspace, form=body, timeout=60)
    except OutcomeUnknown as exc:
        raise OutcomeUnknown(f"{exc} {file_id=}") from None
    except SlackError as exc:
        raise SlackError(f"{exc}; the file was uploaded but not shared. {file_id=}", error=exc.error) from None
    shared = (data.get("files") or [{}])[0]
    return {
        "workspace": workspace_key(workspace),
        "file_id": shared.get("id") or file_id,
        "name": name,
        "channel": channel,
    }


def _shares(f: dict) -> list[dict]:
    out = []
    for kind in ("public", "private"):
        for channel, items in ((f.get("shares") or {}).get(kind) or {}).items():
            for item in items:
                share = {"channel": channel, "ts": item.get("ts"), "thread_ts": item.get("thread_ts")}
                out.append({k: v for k, v in share.items() if v})
    return out


@tool(write=False)
def slack_file_info(workspace: str, file_id: str) -> dict:
    """File metadata by id (files.info, needs files:read): name, type, size, owner, mode, and every
    message that shares it (channel, ts, thread_ts). External files carry external_url."""
    f = call("files.info", workspace=workspace, params={"file": file_id})["file"]
    out = file_summary(f) | {
        "user": f.get("user"),
        "created": f.get("created"),
        "mode": f.get("mode"),
        "is_external": f.get("is_external"),
        "external_url": f.get("external_url") if f.get("is_external") else None,
        "permalink": f.get("permalink"),
        "shares": _shares(f),
    }
    return {k: v for k, v in out.items() if v not in (None, [])}


@tool(write=False)
def slack_list_files(
    workspace: str, channel_id: str = "", user_id: str = "", types: str = "", count: int = 20, page: int = 1
) -> dict:
    """List files, newest first (files.list, needs files:read). Filter by channel_id and/or
    user_id (uploader). types: comma list of all, spaces, snippets, images, gdocs, zips, pdfs.
    page: 1-based; the reply says how many pages there are.
    """
    params: dict = {"count": count, "page": page}
    if channel_id:
        params["channel"] = channel_id
    if user_id:
        params["user"] = user_id
    if types:
        params["types"] = types
    data = call("files.list", workspace=workspace, params=params)
    paging = data.get("paging") or {}
    return {
        "files": [
            file_summary(f) | {"user": f.get("user"), "created": f.get("created")} for f in data.get("files", [])
        ],
        "page": paging.get("page", page),
        "pages": paging.get("pages"),
        "total": paging.get("total"),
    }


@tool(write=True, destructive=True)
def slack_download_file(workspace: str, file_id: str, dest_dir: str = "~/Downloads/slack") -> dict:
    """Download a Slack file by id to a local folder (needs files:read). Returns the saved path.
    Saved as <file_id>-<name>, replacing an earlier copy only once the new one is complete.
    dest_dir: inside SLACK_FILE_ROOTS, like slack_upload_file. External files (Google Drive and
    the like) are not downloaded: the reply gives their external_url.
    """
    f = call("files.info", workspace=workspace, params={"file": file_id})["file"]
    if f.get("is_external") or f.get("mode") == "external":
        return {"file_id": file_id, "external_url": f.get("external_url"), "downloaded": False}
    url = f.get("url_private_download") or f.get("url_private")
    if not url:
        raise ToolError(f"File {file_id} has no downloadable content (mode={f.get('mode')})")
    check_slack_url(url)
    size = f.get("size")
    if size and size > MAX_FILE:
        raise ToolError(f"File {file_id} is {size} bytes; the limit is {MAX_FILE}")
    dest = allowed_path(dest_dir)
    os.makedirs(dest, exist_ok=True)
    name = os.path.basename(f.get("name") or "file").lstrip(".") or "file"
    path = os.path.join(dest, f"{file_id}-{name}")
    headers = {"Authorization": f"Bearer {token(workspace)}"}

    fd, part = tempfile.mkstemp(dir=dest, prefix=f".{file_id}-", suffix=".part")
    try:
        with (
            os.fdopen(fd, "wb") as out,
            client().stream("GET", url, headers=headers, follow_redirects=True, timeout=300) as resp,
        ):
            # Without files:read Slack answers with its HTML login page instead of the file.
            html = "text/html" in resp.headers.get("content-type", "") and f.get("filetype") != "html"
            if resp.status_code != 200 or html:
                raise SlackError(f"Slack download failed: HTTP {resp.status_code} {resp.headers.get('content-type')}")
            written = 0
            for chunk in resp.iter_bytes():
                written += len(chunk)
                if written > MAX_FILE:
                    raise SlackError(f"Download of {file_id} exceeds {MAX_FILE} bytes")
                out.write(chunk)
        if size is not None and written != size:
            raise SlackError(f"Download of {file_id} is incomplete: got {written} of {size} bytes")
        os.replace(part, path)
    except httpx.HTTPError as exc:
        raise SlackError(f"Slack download of {file_id} failed: {type(exc).__name__}") from None
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(part)
    return {"workspace": workspace_key(workspace), "file_id": file_id, "size": written, "path": path}
