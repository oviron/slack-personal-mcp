import logging
import os
import threading
import time

import httpx

from .config import alias, workspace_tokens, write_channels

SLACK = "https://slack.com/api"

# httpx logs every request URL at INFO, and FastMCP turns INFO on: search queries would land in client logs.
for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).setLevel(logging.WARNING)

# Methods that change something in Slack. A failure after the request left the machine may still have
# been applied, so these are never retried blindly and report an unknown outcome instead.
WRITE_METHODS = {
    "chat.postMessage",
    "chat.update",
    "chat.delete",
    "chat.scheduleMessage",
    "chat.deleteScheduledMessage",
    "reactions.add",
    "reactions.remove",
    "files.completeUploadExternal",
    "conversations.mark",
    "users.profile.set",
}

MAX_ATTEMPTS = 3
RETRY_BUDGET = 30.0  # seconds a single call may spend waiting for rate limits

_client: httpx.Client | None = None
_client_lock = threading.Lock()


class SlackError(RuntimeError):
    def __init__(self, message: str, *, error: str | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.error = error
        self.retry_after = retry_after


class OutcomeUnknown(SlackError):
    """A write may or may not have been applied: check Slack before retrying."""


def client() -> httpx.Client:
    global _client
    with _client_lock:
        if _client is None:
            _client = httpx.Client(timeout=15)
        return _client


def workspace_key(workspace: str) -> str:
    return alias(workspace)


def token(workspace: str) -> str:
    tokens = workspace_tokens()
    key = workspace_key(workspace)
    env_names = tokens.get(key)
    if not env_names:
        raise SlackError(f"Unknown Slack workspace {workspace!r}; choose one of: {', '.join(tokens) or 'none set'}")
    if len(env_names) > 1:
        raise SlackError(f"Slack workspace {key!r} is ambiguous: {', '.join(env_names)} all map to it")
    value = os.environ.get(env_names[0])
    if not value:
        raise SlackError(f"Slack workspace {key!r} is not configured; set {env_names[0]}")
    return value


def _retry_after(resp: httpx.Response) -> float:
    try:
        return max(float(resp.headers.get("Retry-After", "1")), 0.0)
    except ValueError:
        return 1.0


def call(
    method: str,
    *,
    workspace: str,
    json_body: dict | None = None,
    params: dict | None = None,
    form: dict | None = None,
    timeout: float = 15,
) -> dict:
    """Call a Slack Web API method. Raises SlackError on any failure; OutcomeUnknown when a write may have landed."""
    headers = {"Authorization": f"Bearer {token(workspace)}"}
    write = method in WRITE_METHODS
    url = f"{SLACK}/{method}"
    deadline = time.monotonic() + RETRY_BUDGET
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            if json_body is not None:
                resp = client().post(url, headers=headers, json=json_body, timeout=timeout)
            elif form is not None:
                resp = client().post(url, headers=headers, data=form, timeout=timeout)
            else:
                resp = client().get(url, headers=headers, params=params or {}, timeout=timeout)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            # Nothing reached Slack: safe to retry reads and writes alike.
            if attempt < MAX_ATTEMPTS:
                time.sleep(0.5 * attempt)
                continue
            raise SlackError(f"Slack {method}: cannot connect ({type(exc).__name__})") from None
        except httpx.HTTPError as exc:
            if write:
                raise OutcomeUnknown(
                    f"Slack {method}: {type(exc).__name__} after sending; it may have been applied. "
                    "Check Slack before retrying."
                ) from None
            if attempt < MAX_ATTEMPTS:
                time.sleep(0.5 * attempt)
                continue
            raise SlackError(f"Slack {method}: {type(exc).__name__}") from None

        if resp.status_code == 429:
            wait = _retry_after(resp)
            if attempt < MAX_ATTEMPTS and time.monotonic() + wait <= deadline:
                time.sleep(wait)
                continue
            raise SlackError(
                f"Slack {method}: rate limited, retry after {wait:g}s", error="ratelimited", retry_after=wait
            )
        if resp.status_code >= 500:
            if write:
                raise OutcomeUnknown(
                    f"Slack {method}: HTTP {resp.status_code}; it may have been applied. Check Slack before retrying."
                )
            if attempt < MAX_ATTEMPTS:
                time.sleep(0.5 * attempt)
                continue
        try:
            data = resp.json()
        except ValueError:
            data = None
        if not isinstance(data, dict):
            if write and resp.status_code < 300:
                raise OutcomeUnknown(
                    f"Slack {method}: HTTP {resp.status_code} with an unreadable body; it may have been applied. "
                    "Check Slack before retrying."
                )
            raise SlackError(
                f"Slack {method}: HTTP {resp.status_code}, not a JSON response "
                f"({resp.headers.get('content-type', 'no content-type')})"
            )
        if not data.get("ok"):
            error = data.get("error") or f"HTTP {resp.status_code}"
            raise SlackError(f"Slack {method} error: {error}", error=error)
        if resp.status_code != 200:
            raise SlackError(f"Slack {method}: unexpected HTTP {resp.status_code}")
        return data
    raise AssertionError("unreachable")


def warnings(data: dict) -> list[str]:
    """Non-fatal warnings Slack attached to a successful response (e.g. message_truncated)."""
    found = [w for w in (data.get("warning") or "").split(",") if w]
    found += (data.get("response_metadata") or {}).get("warnings") or []
    return list(dict.fromkeys(found))


def resolve_channel(workspace: str, channel_id: str) -> str:
    """Resolve a user id (U.../W...) to the DM channel with that user (needs im:write)."""
    if channel_id[:1] in ("U", "W"):
        data = call("conversations.open", workspace=workspace, form={"users": channel_id})
        return data["channel"]["id"]
    return channel_id


def write_target(workspace: str, channel_id: str) -> str:
    """Check SLACK_WRITE_CHANNELS, then turn a user id into the DM with that user."""
    check_write_target(channel_id)
    return resolve_channel(workspace, channel_id)


def check_write_target(channel_id: str) -> None:
    """Refuse a write outside SLACK_WRITE_CHANNELS when it is set. A DM by user id needs that U... listed."""
    allowed = write_channels()
    if allowed is not None and channel_id not in allowed:
        raise SlackError(f"Writing to {channel_id} is not allowed: it is not listed in SLACK_WRITE_CHANNELS")


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
