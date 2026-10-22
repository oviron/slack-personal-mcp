import json
from collections.abc import Callable

import httpx
import pytest

from slack_personal_mcp import cache
from slack_personal_mcp.server import mcp

Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def env(monkeypatch):
    """Fake workspace token, no real .env, no inherited Slack settings."""
    import os

    for name in list(os.environ):
        if name.startswith("SLACK_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("SLACK_ENV_FILE", os.devnull)
    monkeypatch.setenv("SLACK_USER_TOKEN_ACME", "xoxp-test")
    monkeypatch.setattr(cache, "directory", cache.DirectoryCache())


class FakeSlack:
    """Routes every sync httpx request to handlers keyed by Slack method or URL; unknown requests fail."""

    def __init__(self):
        self.routes: dict[str, Handler | dict] = {}
        self.requests: list[httpx.Request] = []

    def on(self, key: str, reply: Handler | dict) -> None:
        self.routes[key] = reply

    def handle(self, request: httpx.Request) -> httpx.Response:
        request.read()
        self.requests.append(request)
        url = str(request.url).split("?")[0]
        key = url.rsplit("/api/", 1)[-1] if "slack.com/api/" in url else url
        reply = self.routes.get(key)
        if reply is None:
            raise AssertionError(f"unexpected request {request.method} {request.url}")
        if callable(reply):
            return reply(request)
        return httpx.Response(200, json=reply)

    def calls(self, method: str) -> list[httpx.Request]:
        return [r for r in self.requests if str(r.url).split("?")[0].endswith(f"/api/{method}")]


@pytest.fixture
def slack(monkeypatch) -> FakeSlack:
    fake = FakeSlack()
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", lambda self, request: fake.handle(request))
    return fake


@pytest.fixture
def call_tool():
    async def _call(tool_name: str, /, **arguments):
        result = await mcp.call_tool(tool_name, arguments)
        content = result[0] if isinstance(result, tuple) else result
        return content[0].text

    return _call


def body(request: httpx.Request) -> dict:
    return json.loads(request.content)
