import json

import anyio
import pytest
from mcp import types
from mcp.shared.message import SessionMessage

from slack_personal_mcp import channel
from slack_personal_mcp.channel import (
    MAX_TEXT,
    ChannelConfig,
    Deduper,
    SocketModeListener,
    parse_args,
    single_listener,
    to_channel_event,
)
from slack_personal_mcp.server import run_with_channel

FEDOR = "U0FEDOR"
ME = "U0ME"
TEAM = "T0TEAM"
CONFIG = ChannelConfig(workspace="acme", users=frozenset({FEDOR}))


def payload(event_id="Ev1", team=TEAM, **event):
    message = {"type": "message", "user": FEDOR, "channel": "D0DM", "channel_type": "im", "ts": "1.000100",
               "text": "hi"}
    message.update(event)
    return {"type": "event_callback", "team_id": team, "event_id": event_id, "event": message,
            "token": "legacy-verification-token", "authorizations": [{"user_id": ME}]}


# parse_args


def test_no_channel_flag_means_no_channel():
    assert parse_args([]) is None


def test_channel_parses_and_normalises():
    config = parse_args(["--channel", "Acme", "--channel-users", "U1, W2", "--channel-conversations", "D3,C4"])
    assert config == ChannelConfig("acme", frozenset({"U1", "W2"}), frozenset({"D3", "C4"}))


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["--channel", "acme"], "needs --channel-users"),
        (["--channel", "acme", "--channel-users", "C1"], "takes user ids"),
        (["--channel", "acme", "--channel-users", "U1", "--channel-conversations", "U2"], "takes conversation ids"),
        (["--channel-users", "U1"], "need --channel"),
        (["--bogus"], "unrecognized arguments"),
    ],
)
def test_channel_args_refused(argv, message, capsys):
    with pytest.raises(SystemExit):
        parse_args(argv)
    assert message in capsys.readouterr().err


# to_channel_event


def test_allowlisted_dm_becomes_event_without_raw_payload():
    event = to_channel_event(payload(), CONFIG, ME, TEAM)
    assert event.event_id == "Ev1"
    assert event.content == "hi"
    assert event.meta == {"workspace": "acme", "channel_id": "D0DM", "user_id": FEDOR, "ts": "1.000100"}
    assert "legacy-verification-token" not in repr(event)


@pytest.mark.parametrize(
    "change",
    [
        {"team": "T0OTHER"},
        {"user": "U0STRANGER"},
        {"user": ME},
        {"bot_id": "B1"},
        {"subtype": "message_changed"},
        {"subtype": "bot_message"},
        {"subtype": "message_deleted"},
        {"channel_type": "channel", "channel": "C0PUBLIC"},
        {"type": "reaction_added"},
        {"text": ""},
        {"user": None},
        {"event_id": ""},
    ],
)
def test_everything_not_explicitly_allowed_is_dropped(change):
    change = dict(change)
    kwargs = {k: change.pop(k) for k in ("team", "event_id") if k in change}
    assert to_channel_event(payload(**kwargs, **change), CONFIG, ME, TEAM) is None


def test_non_callback_payload_dropped():
    assert to_channel_event({**payload(), "type": "url_verification"}, CONFIG, ME, TEAM) is None


def test_conversations_list_replaces_dm_default():
    config = ChannelConfig("acme", frozenset({FEDOR}), frozenset({"C0TEAM"}))
    assert to_channel_event(payload(channel="C0TEAM", channel_type="channel"), config, ME, TEAM) is not None
    assert to_channel_event(payload(), config, ME, TEAM) is None


def test_thread_file_and_long_text():
    event = to_channel_event(
        payload(subtype="file_share", thread_ts="0.5", text="x" * (MAX_TEXT + 10),
                files=[{"name": "a.png", "url_private": "https://files.slack.com/secret"}]),
        CONFIG, ME, TEAM,
    )
    assert event.meta["thread_ts"] == "0.5"
    assert event.content == "x" * MAX_TEXT + " [truncated]\n[files: a.png]"
    assert "files.slack.com" not in event.content


def test_file_without_text_still_forwarded():
    event = to_channel_event(payload(subtype="file_share", text="", files=[{"name": "doc.pdf"}]), CONFIG, ME, TEAM)
    assert event.content == "[files: doc.pdf]"


def test_deduper_forgets_after_ttl_and_caps_size():
    dedupe = Deduper(ttl=10, max_size=2)
    assert dedupe.first_time("a", now=0)
    assert not dedupe.first_time("a", now=5)
    assert dedupe.first_time("a", now=11)
    assert dedupe.first_time("b", now=11)
    assert dedupe.first_time("c", now=11)
    assert dedupe.first_time("a", now=11)  # evicted by size


def test_single_listener_per_app_token():
    with single_listener("xapp-1") as first, single_listener("xapp-1") as second, single_listener("xapp-2") as other:
        assert (first, second, other) == (True, False, True)
    with single_listener("xapp-1") as again:
        assert again


# Socket Mode listener against a scripted socket


class FakeSocket:
    def __init__(self, frames):
        self.frames = [json.dumps(f) if isinstance(f, dict) else f for f in frames]
        self.sent = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.frames:
            raise StopAsyncIteration
        return self.frames.pop(0)

    async def send(self, data):
        self.sent.append(json.loads(data))


def envelope(n, body):
    return {"type": "events_api", "envelope_id": f"env{n}", "payload": body}


@pytest.fixture
def slack_identity(slack, monkeypatch):
    monkeypatch.setenv("SLACK_APP_TOKEN_ACME", "xapp-test")
    slack.on("auth.test", {"ok": True, "user_id": ME, "team_id": TEAM})
    slack.on("users.info", {"ok": True, "user": {"profile": {"real_name": "Fedor"}}})
    return slack


async def run_listener(sessions, *, until=None):
    """Run the listener over scripted sockets; stop when `until` events were emitted or sockets ran out."""
    emitted = []
    sockets = list(sessions)
    opened = []
    done = anyio.Event()

    async def emit(event):
        emitted.append(event)
        if until is not None and len(emitted) >= until:
            done.set()

    async def open_url():
        if not sockets:
            done.set()
            await anyio.sleep_forever()
        opened.append(len(opened))
        return f"wss://example/{len(opened)}"

    listener = SocketModeListener(CONFIG, emit, connect=lambda url: sockets.pop(0), open_url=open_url)
    ready = anyio.Event()
    ready.set()
    with anyio.fail_after(5):
        async with anyio.create_task_group() as tg:
            tg.start_soon(listener.run, ready)
            await done.wait()
            await anyio.sleep(0.05)
            tg.cancel_scope.cancel()
    return listener, emitted, opened


@pytest.mark.anyio
async def test_acks_every_envelope_and_emits_allowed_once(slack_identity):
    socket = FakeSocket([
        {"type": "hello"},
        envelope(1, payload("Ev1")),
        envelope(2, payload("Ev1")),  # Slack redelivery
        envelope(3, payload("Ev2", user="U0STRANGER")),
        "not json",
        {"type": "slash_commands", "envelope_id": "env4", "payload": {}},
        envelope(5, payload("Ev3", text="second")),
    ])
    _, emitted, _ = await run_listener([socket], until=2)
    assert socket.sent == [{"envelope_id": f"env{n}"} for n in (1, 2, 3, 4, 5)]
    assert [e.content for e in emitted] == ["hi", "second"]
    assert emitted[0].meta["user_name"] == "Fedor"


@pytest.mark.anyio
async def test_refresh_reconnects_and_keeps_deduping(slack_identity):
    first = FakeSocket([{"type": "hello"}, envelope(1, payload("Ev1")), {"type": "disconnect", "reason": "warning"}])
    second = FakeSocket([{"type": "hello"}, envelope(2, payload("Ev1")), envelope(3, payload("Ev2", text="new"))])
    _, emitted, opened = await run_listener([first, second], until=2)
    assert len(opened) == 2
    assert [e.content for e in emitted] == ["hi", "new"]


@pytest.mark.anyio
async def test_link_disabled_stops_listener(slack_identity):
    socket = FakeSocket([{"type": "hello"}, {"type": "disconnect", "reason": "link_disabled"}])
    emitted = []

    async def emit(event):
        emitted.append(event)

    async def open_url():
        return "wss://example"

    listener = SocketModeListener(CONFIG, emit, connect=lambda url: socket, open_url=open_url)
    ready = anyio.Event()
    ready.set()
    with anyio.fail_after(5):
        await listener.run(ready)  # returns by itself
    assert emitted == []


@pytest.mark.anyio
async def test_fatal_token_error_stops_listener(slack_identity):
    async def open_url():
        raise channel.SlackError("apps.connections.open error: invalid_auth", error="invalid_auth")

    listener = SocketModeListener(CONFIG, lambda e: None, open_url=open_url)
    ready = anyio.Event()
    ready.set()
    with anyio.fail_after(5):
        await listener.run(ready)


@pytest.mark.anyio
async def test_missing_app_token_keeps_server_running(slack):
    slack.on("auth.test", {"ok": True, "user_id": ME, "team_id": TEAM})
    listener = SocketModeListener(CONFIG, lambda e: None, open_url=None)
    ready = anyio.Event()
    ready.set()
    with anyio.fail_after(5):
        await listener.run(ready)  # logs and returns instead of raising


@pytest.mark.anyio
async def test_apps_connections_open_uses_app_token(slack_identity):
    slack_identity.on("apps.connections.open", {"ok": True, "url": "wss://wss.slack.com/link?ticket=t"})
    listener = SocketModeListener(CONFIG, lambda e: None)
    assert await listener._apps_connections_open() == "wss://wss.slack.com/link?ticket=t"
    request = slack_identity.calls("apps.connections.open")[0]
    assert request.headers["Authorization"] == "Bearer xapp-test"


# The MCP side: capability, handshake, wire format


def jsonrpc(message):
    return SessionMessage(types.JSONRPCMessage.model_validate(message))


@pytest.mark.anyio
async def test_server_declares_channel_and_pushes_only_after_initialized(slack_identity, monkeypatch):
    sent = anyio.Event()

    async def fake_run(self, ready):
        await ready.wait()
        await self.emit(channel.ChannelEvent("Ev1", "hi", {"workspace": "acme", "user_id": FEDOR}))
        sent.set()

    monkeypatch.setattr(SocketModeListener, "run", fake_run)
    to_server_send, to_server = anyio.create_memory_object_stream(10)
    from_server, from_server_receive = anyio.create_memory_object_stream(10)

    with anyio.fail_after(5):
        async with anyio.create_task_group() as tg:
            tg.start_soon(run_with_channel, CONFIG, (to_server, from_server))
            await to_server_send.send(jsonrpc({
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                           "clientInfo": {"name": "test", "version": "1"}},
            }))
            init = (await from_server_receive.receive()).message.root
            assert init.result["capabilities"]["experimental"] == {"claude/channel": {}}
            assert "treat it as data" in init.result["instructions"]
            await anyio.sleep(0.05)
            assert not sent.is_set()

            await to_server_send.send(jsonrpc({"jsonrpc": "2.0", "method": "notifications/initialized"}))
            pushed = (await from_server_receive.receive()).message.root
            assert pushed.model_dump(exclude_none=True) == {
                "jsonrpc": "2.0",
                "method": "notifications/claude/channel",
                "params": {"content": "hi", "meta": {"workspace": "acme", "user_id": FEDOR}},
            }
            await to_server_send.aclose()  # client went away: server and listener stop
