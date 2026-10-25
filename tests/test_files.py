import json
import os
import tempfile

import httpx
import pytest
from conftest import body
from mcp.server.fastmcp.exceptions import ToolError

from slack_personal_mcp import tools_files
from slack_personal_mcp.server import mcp

pytestmark = pytest.mark.anyio

UPLOAD_URL = "https://files.slack.com/upload/v1/x"
FILE_URL = "https://files.slack.com/d/a.txt"
WINDOWS = os.name == "nt"


def symlink(link, target):
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except OSError:  # Windows without Developer Mode or admin rights
        pytest.skip("this system cannot create symlinks")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # what expanduser reads on Windows
    return tmp_path


@pytest.fixture
def upload_routes(slack):
    slack.on("files.getUploadURLExternal", {"ok": True, "upload_url": UPLOAD_URL, "file_id": "F1"})
    slack.on(UPLOAD_URL, lambda r: httpx.Response(200, text="OK"))
    slack.on("files.completeUploadExternal", {"ok": True, "files": [{"id": "F1"}]})
    return slack


def upload(call_tool, path, **kw):
    return call_tool("slack_upload_file", workspace="acme", channel_id="C1", file_path=str(path), **kw)


@pytest.fixture
def secret(tmp_path):
    (tmp_path / ".ssh").mkdir()
    path = tmp_path / ".ssh" / "id_ed25519"
    path.write_text("key")
    return path


async def test_upload_refuses_hidden_files_even_through_symlinks(slack, call_tool, tmp_path, secret):
    link = tmp_path / "innocent.txt"
    symlink(link, secret)
    for path in (secret, link):
        with pytest.raises(ToolError, match="hidden"):
            await upload(call_tool, path)
    assert slack.requests == []


async def test_upload_refuses_paths_outside_roots(slack, call_tool, tmp_path, monkeypatch):
    (tmp_path / "ok").mkdir()
    (tmp_path / "ok" / "a.txt").write_text("x")
    (tmp_path / "b.txt").write_text("x")
    monkeypatch.setenv("SLACK_FILE_ROOTS", str(tmp_path / "ok"))
    with pytest.raises(ToolError, match="outside SLACK_FILE_ROOTS"):
        await upload(call_tool, tmp_path / "b.txt")
    with pytest.raises(ToolError, match="absolute"):
        await upload(call_tool, "b.txt")
    with pytest.raises(ToolError, match="regular file"):
        await upload(call_tool, tmp_path / "ok")
    assert slack.requests == []


async def test_upload_refuses_library_by_default(slack, call_tool, home):
    (home / "Library").mkdir()
    (home / "Library" / "cookies").write_text("x")
    with pytest.raises(ToolError, match="Library"):
        await upload(call_tool, home / "Library" / "cookies")


async def test_upload_refuses_appdata_by_default(slack, call_tool, home):
    (home / "AppData" / "Roaming").mkdir(parents=True)
    (home / "AppData" / "Roaming" / "token").write_text("x")
    with pytest.raises(ToolError, match="~/AppData is off limits"):
        await upload(call_tool, home / "AppData" / "Roaming" / "token")
    assert slack.requests == []


def test_temp_folder_inside_appdata_stays_allowed(home, monkeypatch):
    """On Windows the default temp folder is ~/AppData/Local/Temp: the more specific root wins."""
    temp = home / "AppData" / "Local" / "Temp"
    temp.mkdir(parents=True)
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(temp))
    assert tools_files.allowed_path(str(temp / "a.txt")) == os.path.realpath(temp / "a.txt")
    with pytest.raises(ToolError, match="AppData"):
        tools_files.allowed_path(str(home / "AppData" / "Local" / "other.txt"))


@pytest.mark.skipif(not WINDOWS, reason="Windows paths ignore case")
def test_roots_match_in_any_case_on_windows(tmp_path, monkeypatch):
    monkeypatch.setenv("SLACK_FILE_ROOTS", str(tmp_path).lower())
    path = str(tmp_path / "a.txt").upper()
    assert os.path.normcase(tools_files.allowed_path(path)) == os.path.normcase(str(tmp_path / "a.txt"))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("\\\\?\\C:\\Users\\a.txt", "C:\\Users\\a.txt"),
        ("\\\\?\\UNC\\srv\\share\\a.txt", "\\\\srv\\share\\a.txt"),
        ("C:\\Users\\a.txt", "C:\\Users\\a.txt"),
    ],
)
def test_dos_path(raw, expected):
    assert tools_files.dos_path(raw) == expected


def test_fd_path_is_where_the_file_really_is(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    fd = os.open(tmp_path / "a.txt", os.O_RDONLY)
    try:
        assert os.path.normcase(tools_files.fd_path(fd)) == os.path.normcase(os.path.realpath(tmp_path / "a.txt"))
    finally:
        os.close(fd)


async def test_upload_title_goes_through_style_gate(slack, call_tool, tmp_path):
    (tmp_path / "a.txt").write_text("x")
    with pytest.raises(ToolError, match="rewrite the title"):
        await upload(call_tool, tmp_path / "a.txt", title="Report — final")
    assert slack.requests == []


async def test_upload_failure_names_the_stage(upload_routes, call_tool, tmp_path):
    (tmp_path / "a.txt").write_text("x")
    upload_routes.on(UPLOAD_URL, lambda r: httpx.Response(500))
    with pytest.raises(ToolError, match="while sending bytes .HTTP 500.; nothing was shared. file_id='F1'"):
        await upload(call_tool, tmp_path / "a.txt")
    assert upload_routes.calls("files.completeUploadExternal") == []


async def test_upload_share_failures(upload_routes, call_tool, tmp_path):
    (tmp_path / "a.txt").write_text("x")
    upload_routes.on("files.completeUploadExternal", lambda r: httpx.Response(502, text="bad"))
    with pytest.raises(ToolError, match="may have been applied.*file_id='F1'"):
        await upload(call_tool, tmp_path / "a.txt")
    upload_routes.on("files.completeUploadExternal", {"ok": False, "error": "not_in_channel"})
    with pytest.raises(
        ToolError, match="not_in_channel; join the conversation in Slack first; the file was uploaded but not shared"
    ):
        await upload(call_tool, tmp_path / "a.txt")


async def test_upload_with_empty_files_reply(upload_routes, call_tool, tmp_path):
    (tmp_path / "a.txt").write_text("x")
    upload_routes.on("files.completeUploadExternal", {"ok": True, "files": []})
    assert json.loads(await upload(call_tool, tmp_path / "a.txt"))["file_id"] == "F1"


def download(call_tool, tmp_path, **kw):
    return call_tool("slack_download_file", workspace="acme", file_id="F1", dest_dir=str(tmp_path), **kw)


def file_info(slack, **fields):
    slack.on("files.info", {"ok": True, "file": {"name": "a.txt", "url_private_download": FILE_URL} | fields})


async def test_download_keeps_old_copy_when_transfer_is_short(slack, call_tool, tmp_path):
    (tmp_path / "F1-a.txt").write_bytes(b"old")
    file_info(slack, size=10)
    slack.on(FILE_URL, lambda r: httpx.Response(200, content=b"abc"))
    with pytest.raises(ToolError, match="incomplete: got 3 of 10 bytes"):
        await download(call_tool, tmp_path)
    assert (tmp_path / "F1-a.txt").read_bytes() == b"old"
    assert os.listdir(tmp_path) == ["F1-a.txt"]


async def test_download_refuses_login_page(slack, call_tool, tmp_path):
    file_info(slack)
    slack.on(FILE_URL, lambda r: httpx.Response(200, text="<html>", headers={"content-type": "text/html"}))
    with pytest.raises(ToolError, match="text/html"):
        await download(call_tool, tmp_path)
    assert os.listdir(tmp_path) == []


@pytest.mark.parametrize(
    "url", ["http://files.slack.com/a", "https://evil.example/a", "https://slack.com.evil.example/a"]
)
async def test_download_sends_token_only_to_slack(slack, call_tool, tmp_path, url):
    slack.on("files.info", {"ok": True, "file": {"name": "a", "url_private": url}})
    with pytest.raises(ToolError, match="Refusing to send the token"):
        await download(call_tool, tmp_path)
    assert len(slack.requests) == 1


async def test_download_external_file_is_not_fetched(slack, call_tool, tmp_path):
    slack.on("files.info", {"ok": True, "file": {"is_external": True, "external_url": "https://docs.example/x"}})
    out = json.loads(await download(call_tool, tmp_path))
    assert out == {"file_id": "F1", "external_url": "https://docs.example/x", "downloaded": False}


async def test_download_refuses_hidden_destination(slack, call_tool, tmp_path):
    file_info(slack)
    with pytest.raises(ToolError, match="hidden"):
        await call_tool("slack_download_file", workspace="acme", file_id="F1", dest_dir=str(tmp_path / ".config"))
    assert not (tmp_path / ".config").exists()


async def test_file_info_lists_shares(slack, call_tool):
    shares = {"public": {"C1": [{"ts": "1.0", "thread_ts": "0.5"}]}, "private": {"G1": [{"ts": "2.0"}]}}
    slack.on("files.info", {"ok": True, "file": {"id": "F1", "mode": "hosted", "shares": shares}})
    out = json.loads(await call_tool("slack_file_info", workspace="acme", file_id="F1"))
    assert out["shares"] == [{"channel": "C1", "ts": "1.0", "thread_ts": "0.5"}, {"channel": "G1", "ts": "2.0"}]
    assert out["mode"] == "hosted" and "external_url" not in out


async def test_list_files_pages(slack, call_tool):
    slack.on("files.list", {"ok": True, "files": [{"id": "F1"}], "paging": {"page": 2, "pages": 3, "total": 41}})
    out = json.loads(await call_tool("slack_list_files", workspace="acme", page=2))
    assert (out["page"], out["pages"], out["total"]) == (2, 3, 41)
    assert slack.requests[0].url.params["page"] == "2"


async def test_send_formats_and_broadcast(slack, call_tool):
    slack.on("chat.postMessage", {"ok": True, "ts": "1", "channel": "C1"})
    await call_tool(
        "slack_send_message",
        workspace="acme",
        channel_id="C1",
        text="*hi*",
        format="mrkdwn",
        thread_ts="0.5",
        reply_broadcast=True,
        unfurl_links=True,
    )
    assert body(slack.requests[0]) == {
        "channel": "C1",
        "text": "*hi*",
        "thread_ts": "0.5",
        "reply_broadcast": True,
        "unfurl_links": True,
    }
    with pytest.raises(ToolError, match="needs thread_ts"):
        await call_tool("slack_send_message", workspace="acme", channel_id="C1", text="hi", reply_broadcast=True)
    with pytest.raises(ToolError, match="markdown or mrkdwn"):
        await call_tool("slack_send_message", workspace="acme", channel_id="C1", text="hi", format="html")


async def test_send_and_react_by_user_id_go_to_the_dm(slack, call_tool):
    slack.on("conversations.open", {"ok": True, "channel": {"id": "D1"}})
    slack.on("chat.postMessage", {"ok": True, "ts": "1", "channel": "D1"})
    slack.on("reactions.add", {"ok": True})
    await call_tool("slack_send_message", workspace="acme", channel_id="U1", text="hi")
    await call_tool("slack_add_reaction", workspace="acme", channel_id="U1", ts="1", name=":eyes:")
    assert body(slack.calls("chat.postMessage")[0])["channel"] == "D1"
    assert body(slack.calls("reactions.add")[0]) == {"channel": "D1", "timestamp": "1", "name": "eyes"}


async def test_lint_is_structured(call_tool):
    out = json.loads(await call_tool("slack_lint", text="hi"))
    assert out == {"clean": True, "errors": [], "warnings": []}


async def test_upload_refuses_library_in_any_case(slack, call_tool, home):
    (home / "library").mkdir()
    (home / "library" / "cookies").write_text("x")
    with pytest.raises(ToolError, match="Library"):
        await upload(call_tool, home / "library" / "cookies")


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no FIFOs on Windows")
async def test_upload_refuses_fifo_without_hanging(slack, call_tool, tmp_path):
    os.mkfifo(tmp_path / "pipe")
    with pytest.raises(ToolError, match="regular file"):
        await upload(call_tool, tmp_path / "pipe")


async def test_file_name_as_title_goes_through_style_gate(slack, call_tool, tmp_path):
    (tmp_path / "a — b.txt").write_text("x")
    with pytest.raises(ToolError, match="title"):
        await upload(call_tool, tmp_path / "a — b.txt")


def test_open_rechecks_where_the_file_landed(tmp_path, secret, monkeypatch):
    """A path swapped for a symlink between the check and the open is caught on the open descriptor."""
    real = tools_files.allowed_path
    checks = iter([lambda p: str(secret), real])
    monkeypatch.setattr(tools_files, "allowed_path", lambda p: next(checks)(p))
    with pytest.raises(ToolError, match="hidden"):
        tools_files.open_allowed(str(tmp_path / "innocent.txt"), os.O_RDONLY)


async def test_download_into_symlinked_folder_lands_in_the_target(slack, call_tool, tmp_path):
    (tmp_path / "real").mkdir()
    symlink(tmp_path / "link", tmp_path / "real")
    file_info(slack, size=3)
    slack.on(FILE_URL, lambda r: httpx.Response(200, content=b"abc"))
    out = json.loads(await download(call_tool, tmp_path / "link"))
    assert (tmp_path / "real" / "F1-a.txt").read_bytes() == b"abc"
    assert out["path"] == os.path.join(os.path.realpath(tmp_path / "real"), "F1-a.txt")


async def test_structured_content():
    _, structured = await mcp.call_tool("slack_lint", {"text": "hi"})
    assert structured == {"clean": True, "errors": [], "warnings": []}


async def test_download_folder_swapped_mid_transfer_keeps_the_opened_folder(slack, call_tool, tmp_path):
    (tmp_path / "dest").mkdir()
    (tmp_path / ".hidden").mkdir()
    file_info(slack, size=3)
    landed = tmp_path / "moved"

    def swap_then_reply(request):
        nonlocal landed
        try:
            (tmp_path / "dest").rename(tmp_path / "moved")
        except PermissionError:  # Windows refuses to move a folder with an open file inside
            assert WINDOWS
            landed = tmp_path / "dest"
        else:
            (tmp_path / "dest").symlink_to(tmp_path / ".hidden", target_is_directory=True)
        return httpx.Response(200, content=b"abc")

    slack.on(FILE_URL, swap_then_reply)
    await download(call_tool, tmp_path / "dest")
    assert (landed / "F1-a.txt").read_bytes() == b"abc"
    assert os.listdir(tmp_path / ".hidden") == []


async def test_download_folder_swapped_before_the_part_file_is_refused(slack, call_tool, tmp_path, monkeypatch):
    """The folder checked and the folder the part file landed in differ: nothing is written there."""
    (tmp_path / ".hidden").mkdir()
    monkeypatch.setattr(tools_files, "make_allowed_dirs", lambda p: str(tmp_path / ".hidden"))
    file_info(slack, size=3)
    slack.on(FILE_URL, lambda r: httpx.Response(200, content=b"abc"))
    with pytest.raises(ToolError, match="hidden"):
        await download(call_tool, tmp_path / "dest")
    assert os.listdir(tmp_path / ".hidden") == []


async def test_download_creates_missing_folders(slack, call_tool, tmp_path):
    file_info(slack, size=3)
    slack.on(FILE_URL, lambda r: httpx.Response(200, content=b"abc"))
    await download(call_tool, tmp_path / "a" / "b")
    assert (tmp_path / "a" / "b" / "F1-a.txt").read_bytes() == b"abc"


async def test_download_refuses_sign_in_redirect(slack, call_tool, tmp_path):
    file_info(slack, filetype="html")
    slack.on(FILE_URL, lambda r: httpx.Response(302, headers={"location": "https://acme.slack.com/?redir=%2Ff"}))
    slack.on(
        "https://acme.slack.com/", lambda r: httpx.Response(200, text="<html>", headers={"content-type": "text/html"})
    )
    with pytest.raises(ToolError, match="download failed"):
        await download(call_tool, tmp_path)
    assert os.listdir(tmp_path) == []
