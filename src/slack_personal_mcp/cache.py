import hashlib
import threading
import time
from copy import deepcopy

from .read_api import call
from .slack import SlackError, token, workspace_key

TTL = 3600


class DirectoryCache:
    def __init__(self):
        self._lock = threading.RLock()
        self._identities: dict[str, tuple[bytes, str, float]] = {}
        self._entries: dict[tuple, tuple[float, list[dict]]] = {}

    def _identity(self, workspace: str) -> tuple[str, str, bytes]:
        workspace = workspace_key(workspace)
        credential = hashlib.sha256(token(workspace).encode()).digest()
        cached = self._identities.get(workspace)
        if cached is None or cached[0] != credential or cached[2] <= time.monotonic():
            team_id = call("auth.test", workspace=workspace).get("team_id")
            if not team_id:
                raise SlackError("Slack auth.test: missing team_id; cannot isolate the directory cache")
            if cached and (cached[0], cached[1]) != (credential, team_id):
                self._entries = {key: value for key, value in self._entries.items() if key[0] != workspace}
            self._identities[workspace] = credential, team_id, time.monotonic() + TTL
        return workspace, self._identities[workspace][1], credential

    def _load(self, workspace: str, method: str, collection: str, params: dict, refresh: bool) -> list[dict]:
        with self._lock:
            key = (*self._identity(workspace), method, params.get("types", ""))
            cached = self._entries.get(key)
            if cached and cached[0] > time.monotonic() and not refresh:
                return deepcopy(cached[1])
            items = []
            cursor = ""
            seen = set()
            while True:
                data = call(method, workspace=workspace, params=params | ({"cursor": cursor} if cursor else {}))
                items.extend(data.get(collection, []))
                cursor = (data.get("response_metadata", {}).get("next_cursor") or "").strip()
                if not cursor:
                    break
                if cursor in seen:
                    raise SlackError(f"Slack {method}: repeated pagination cursor; directory is incomplete")
                seen.add(cursor)
            self._entries[key] = time.monotonic() + TTL, items
            return deepcopy(items)

    def users(self, workspace: str, refresh: bool = False) -> list[dict]:
        return self._load(workspace, "users.list", "members", {"limit": 200}, refresh)

    def channels(self, workspace: str, types: str, refresh: bool = False) -> list[dict]:
        return self._load(
            workspace,
            "conversations.list",
            "channels",
            {"limit": 200, "types": types, "exclude_archived": "true"},
            refresh,
        )


directory = DirectoryCache()


def user_names(workspace: str, refresh: bool = False) -> dict[str, str]:
    return {
        user["id"]: name
        for user in directory.users(workspace, refresh)
        if user.get("id")
        and (
            name := user.get("profile", {}).get("display_name")
            or user.get("name")
            or user.get("profile", {}).get("real_name")
            or user.get("real_name")
        )
    }
