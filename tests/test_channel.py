import json

import anyio
import httpx
import pytest
from mcp import types
from mcp.shared.message import SessionMessage

from slack_personal_mcp import channel
from slack_personal_mcp.channel import (
    MAX_TEXT,
    ChannelConfig,
    ChannelEvent,
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
MY_DM = "D0DM"
CONFIG = ChannelConfig(workspace="acme", users=frozenset({FEDOR}))


def payload(event_id="Ev1", team=TEAM, **event):
    message = {"type": "message", "user": FEDOR, "channel": MY_DM, "channel_type": "im", "ts": "1.000100"}
    message["text"] = "hi"
    message.update(event)
    return {
        "type": "event_callback",
        "team_id": team,
        "event_id": event_id,
        "event": message,
        "token": "legacy-verification-token",
        "authorizations": [{"user_id": ME}],
    }


def accept(body, config=CONFIG):
    return to_channel_event(body, config, ME, TEAM)


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
    event = accept(payload())
    assert event == ChannelEvent(
        "Ev1", "hi", {"workspace": "acme", "channel_id": MY_DM, "user_id": FEDOR, "ts": "1.000100"}
    )
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
        {"channel": "C0PUBLIC", "channel_type": "channel"},
        {"type": "reaction_added"},
        {"text": ""},
        {"user": None},
        {"event_id": ""},
    ],
)
def test_everything_not_explicitly_allowed_is_dropped(change):
    change = dict(change)
    outer = {k: change.pop(k) for k in ("team", "event_id") if k in change}
    assert accept(payload(**outer, **change)) is None


def test_non_callback_payload_dropped():
    assert accept({**payload(), "type": "url_verification"}) is None


def test_listed_conversation_replaces_dm_default():
    config = ChannelConfig("acme", frozenset({FEDOR}), frozenset({"C0TEAM"}))
    assert accept(payload(channel="C0TEAM", channel_type="channel"), config) is not None
    assert accept(payload(), config) is None
    assert accept(payload(channel="C0TEAM", channel_type="channel")) is None


def test_thread_file_and_long_text():
    files = [{"name": "a.png", "url_private": "https://files.slack.com/secret"}]
    event = accept(payload(subtype="file_share", thread_ts="0.5", text="x" * (MAX_TEXT + 10), files=files))
    assert event.meta["thread_ts"] == "0.5"
    assert event.content == "x" * MAX_TEXT + " [truncated]\n[files: a.png]"
    assert "files.slack.com" not in event.content


def test_file_without_text_still_forwarded():
    assert accept(payload(subtype="file_share", text="", files=[{"name": "doc.pdf"}])).content == "[files: doc.pdf]"


def test_meta_keys_are_identifiers():
    event = accept(payload(thread_ts="0.5"))
    assert all(key.replace("_", "").isalnum() for key in event.meta)


def test_deduper_forgets_after_ttl_and_caps_size():
    dedupe = Deduper(ttl=10, max_size=2)
    assert dedupe.first_time("a", now=0)
    assert not dedupe.first_time("a", now=5)
    assert dedupe.first_time("a", now=11)
    assert dedupe.first_time("b", now=11)
    assert not dedupe.first_time("a", now=12)  # still seen while the cache is full
    assert dedupe.first_time("c", now=12)
    assert dedupe.first_time("a", now=12)  # evicted by size


def test_single_listener_per_workspace(tmp_path):
    with (
        single_listener("T1", str(tmp_path)) as first,
        single_listener("T1", str(tmp_path)) as second,
        single_listener("T2", str(tmp_path)) as other,
    ):
        assert (first, second, other) == (True, False, True)
    with single_listener("T1", str(tmp_path)) as again:
        assert again


def test_single_listener_without_lock_dir_stays_off(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")
    with single_listener("T1", str(blocker / "sub")) as held:
        assert held is False


# Socket Mode listener against a scripted socket


class FakeSocket:
    def __init__(self, frames, log=None):
        self.frames = [json.dumps(f) if isinstance(f, dict) else f for f in frames]
        self.sent = []
        self.log = log if log is not None else []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.frames:
            raise StopAsyncIteration
        await anyio.sleep(0)
        return self.frames.pop(0)

    async def send(self, data):
        ack = json.loads(data)
        self.sent.append(ack)
        self.log.append(("ack", ack["envelope_id"]))


def envelope(n, body):
    return {"type": "events_api", "envelope_id": f"env{n}", "payload": body}


def conversation_info(known):
    def reply(request):
        channel_id = request.url.params["channel"]
        if channel_id not in known:
            return httpx.Response(200, json={"ok": False, "error": "channel_not_found"})
        return httpx.Response(200, json={"ok": True, "channel": {"id": channel_id, **known[channel_id]}})

    return reply


@pytest.fixture
def slack_identity(slack, monkeypatch):
    monkeypatch.setenv("SLACK_APP_TOKEN_ACME", "xapp-test")
    monkeypatch.setattr(channel, "lock_dir", lambda: str(slack.tmp))
    slack.on("auth.test", {"ok": True, "user_id": ME, "team_id": TEAM})
    slack.on("conversations.info", conversation_info({MY_DM: {"is_im": True, "user": FEDOR}}))
    slack.on("users.info", {"ok": True, "user": {"profile": {"real_name": "Fedor"}}})
    return slack


@pytest.fixture
def slack(slack, tmp_path):
    slack.tmp = tmp_path
    return slack


@pytest.fixture(autouse=True)
def fast_backoff(monkeypatch):
    monkeypatch.setattr(channel.random, "uniform", lambda a, b: 0)

    async def short_pause(seconds):
        await anyio.sleep(min(seconds, 0.01))

    monkeypatch.setattr(channel, "_pause", short_pause)


def listener_with(sockets, emit):
    sockets = list(sockets)
    opened = []

    async def open_url():
        if not sockets:
            await anyio.sleep_forever()
        opened.append(True)
        return "wss://example"

    listener = SocketModeListener(CONFIG, emit, connect=lambda url: sockets.pop(0), open_url=open_url)
    return listener, opened


async def run_until(listener, done, timeout=5):
    ready = anyio.Event()
    ready.set()
    with anyio.fail_after(timeout):
        async with anyio.create_task_group() as tg:
            tg.start_soon(listener.run, ready)
            await done.wait()
            await anyio.sleep(0.01)
            tg.cancel_scope.cancel()


@pytest.mark.anyio
async def test_acks_every_envelope_and_emits_allowed_once(slack_identity):
    emitted, done = [], anyio.Event()

    async def emit(event):
        emitted.append(event)
        if len(emitted) == 2:
            done.set()

    socket = FakeSocket(
        [
            {"type": "hello"},
            envelope(1, payload("Ev1")),
            envelope(2, payload("Ev1")),  # Slack redelivery
            envelope(3, payload("Ev2", user="U0STRANGER")),
            "not json",
            {"type": "slash_commands", "envelope_id": "env4", "payload": {}},
            envelope(5, payload("Ev3", text="second")),
        ]
    )
    listener, _ = listener_with([socket], emit)
    await run_until(listener, done)
    assert socket.sent == [{"envelope_id": f"env{n}"} for n in (1, 2, 3, 4, 5)]
    assert [e.content for e in emitted] == ["hi", "second"]
    assert emitted[0].meta["user_name"] == "Fedor"
    assert slack_identity.calls("conversations.open") == []  # listening never creates a DM


@pytest.mark.anyio
async def test_acks_do_not_wait_for_delivery(slack_identity):
    """A stuck session must not hold acks back, or Slack resends and eventually disables the link."""
    released, all_acked = anyio.Event(), anyio.Event()

    async def emit(event):
        await released.wait()

    frames = [{"type": "hello"}] + [envelope(n, payload(f"Ev{n}")) for n in range(1, 6)]
    socket = FakeSocket(frames)
    listener, _ = listener_with([socket], emit)

    async def watch():
        while len(socket.sent) < 5:
            await anyio.sleep(0.001)
        all_acked.set()

    ready = anyio.Event()
    ready.set()
    with anyio.fail_after(5):
        async with anyio.create_task_group() as tg:
            tg.start_soon(listener.run, ready)
            tg.start_soon(watch)
            await all_acked.wait()
            assert not released.is_set()
            tg.cancel_scope.cancel()


@pytest.mark.anyio
async def test_refresh_reconnects_and_keeps_deduping(slack_identity):
    emitted, done = [], anyio.Event()

    async def emit(event):
        emitted.append(event)
        if len(emitted) == 2:
            done.set()

    first = FakeSocket([{"type": "hello"}, envelope(1, payload("Ev1")), {"type": "disconnect", "reason": "warning"}])
    second = FakeSocket([{"type": "hello"}, envelope(2, payload("Ev1")), envelope(3, payload("Ev2", text="new"))])
    listener, opened = listener_with([first, second], emit)
    await run_until(listener, done)
    assert len(opened) == 2
    assert [e.content for e in emitted] == ["hi", "new"]


@pytest.mark.anyio
async def test_socket_that_closes_at_once_backs_off(slack_identity, monkeypatch):
    waits = []

    async def record(seconds):
        waits.append(seconds)
        if len(waits) >= 3:
            await anyio.sleep_forever()

    monkeypatch.setattr(channel, "_pause", record)
    sockets = [FakeSocket([{"type": "hello"}]) for _ in range(5)]
    listener, opened = listener_with(sockets, lambda e: None)
    ready = anyio.Event()
    ready.set()
    with anyio.move_on_after(1):
        await listener.run(ready)
    assert waits[:3] == [1.0, 2.0, 4.0]
    assert len(opened) == 3


@pytest.mark.anyio
async def test_link_disabled_stops_listener(slack_identity):
    socket = FakeSocket([{"type": "hello"}, {"type": "disconnect", "reason": "link_disabled"}])
    listener, _ = listener_with([socket], lambda e: None)
    ready = anyio.Event()
    ready.set()
    with anyio.fail_after(5):
        await listener.run(ready)  # returns by itself


@pytest.mark.anyio
@pytest.mark.parametrize("error", ["invalid_auth", "missing_scope", "not_allowed_token_type"])
async def test_permanent_errors_stop_listener(slack_identity, error):
    async def open_url():
        raise channel.SlackError(f"apps.connections.open error: {error}", error=error)

    listener = SocketModeListener(CONFIG, lambda e: None, open_url=open_url)
    ready = anyio.Event()
    ready.set()
    with anyio.fail_after(5):
        await listener.run(ready)


@pytest.mark.anyio
async def test_temporary_auth_failure_is_retried(slack_identity):
    attempts = []

    def flaky(request):
        attempts.append(True)
        if len(attempts) <= 3:  # more than slack.call retries by itself
            return httpx.Response(503, text="busy")
        return httpx.Response(200, json={"ok": True, "user_id": ME, "team_id": TEAM})

    slack_identity.on("auth.test", flaky)
    done = anyio.Event()

    async def emit(event):
        done.set()

    socket = FakeSocket([{"type": "hello"}, envelope(1, payload())])
    listener, _ = listener_with([socket], emit)
    await run_until(listener, done)
    assert len(attempts) >= 4


@pytest.mark.anyio
async def test_listed_conversation_must_be_readable(slack_identity):
    slack_identity.on("conversations.info", {"ok": False, "error": "channel_not_found"})
    config = ChannelConfig("acme", frozenset({FEDOR}), frozenset({"C0GONE"}))
    listener = SocketModeListener(config, lambda e: None, open_url=lambda: None)
    ready = anyio.Event()
    ready.set()
    with anyio.fail_after(5):
        await listener.run(ready)  # stops instead of listening


@pytest.mark.anyio
async def test_missing_app_token_keeps_server_running(slack):
    listener = SocketModeListener(CONFIG, lambda e: None)
    ready = anyio.Event()
    ready.set()
    with anyio.fail_after(5):
        await listener.run(ready)  # logs and returns instead of raising
    assert slack.requests == []


@pytest.mark.anyio
async def test_second_listener_on_this_machine_stays_off(slack_identity):
    listener = SocketModeListener(CONFIG, lambda e: None, open_url=lambda: None)
    ready = anyio.Event()
    ready.set()
    with single_listener(TEAM, str(slack_identity.tmp)) as held, anyio.fail_after(5):
        assert held
        await listener.run(ready)  # returns: the lock is taken


@pytest.mark.anyio
async def test_apps_connections_open_uses_app_token(slack_identity):
    slack_identity.on("apps.connections.open", {"ok": True, "url": "wss://wss.slack.com/link?ticket=t"})
    listener = SocketModeListener(CONFIG, lambda e: None)
    assert await listener._apps_connections_open() == "wss://wss.slack.com/link?ticket=t"
    request = slack_identity.calls("apps.connections.open")[0]
    assert request.headers["Authorization"] == "Bearer xapp-test"


def emitted_into(collected, done, count=1):
    async def emit(event):
        collected.append(event)
        if len(collected) >= count:
            done.set()

    return emit


@pytest.mark.anyio
async def test_dm_of_another_installer_never_reaches_the_session(slack_identity):
    """Socket Mode carries every installation's events: the same sender writing to someone else is not ours."""
    slack_identity.on(
        "conversations.info",
        conversation_info({MY_DM: {"is_im": True, "user": FEDOR}, "D0FAKE": {"is_im": True, "user": "U0OTHER"}}),
    )
    emitted, done = [], anyio.Event()
    socket = FakeSocket(
        [
            {"type": "hello"},
            envelope(1, payload("Ev1", channel="D0SOMEONEELSE")),  # not visible to the owner at all
            envelope(2, payload("Ev2", channel="D0FAKE")),  # visible, but a DM with somebody else
            envelope(3, payload("Ev3", text="mine")),
        ]
    )
    listener, _ = listener_with([socket], emitted_into(emitted, done))
    await run_until(listener, done)
    assert [e.content for e in emitted] == ["mine"]


@pytest.mark.anyio
async def test_lost_access_to_listed_channel_stops_forwarding(slack_identity, monkeypatch):
    monkeypatch.setattr(channel, "ACCESS_TTL", 0.0)
    member = {"C0TEAM": {"is_member": True}}
    slack_identity.on("conversations.info", conversation_info(member))
    config = ChannelConfig("acme", frozenset({FEDOR}), frozenset({"C0TEAM"}))
    emitted, first, second = [], anyio.Event(), anyio.Event()

    async def emit(event):
        emitted.append(event)
        first.set()

    def body(n, text):
        return envelope(n, payload(f"Ev{n}", channel="C0TEAM", channel_type="channel", text=text))

    gate = anyio.Event()

    class GatedSocket(FakeSocket):
        async def __anext__(self):
            if len(self.sent) == 1:
                await gate.wait()
            if not self.frames:
                second.set()
                await anyio.sleep_forever()
            return await super().__anext__()

    socket = GatedSocket([{"type": "hello"}, body(1, "before"), body(2, "after")])
    listener = SocketModeListener(config, emit, connect=lambda url: socket, open_url=_url)
    ready = anyio.Event()
    ready.set()
    with anyio.fail_after(5):
        async with anyio.create_task_group() as tg:
            tg.start_soon(listener.run, ready)
            await first.wait()
            member["C0TEAM"] = {"is_member": False}
            gate.set()
            await second.wait()
            await anyio.sleep(0.05)
            tg.cancel_scope.cancel()
    assert [e.content for e in emitted] == ["before"]


async def _url():
    return "wss://example"


@pytest.mark.anyio
async def test_drop_after_a_long_healthy_socket_reconnects_fast(slack_identity, monkeypatch):
    monkeypatch.setattr(channel, "STABLE", 0.0)
    waits = []

    async def record(seconds):
        waits.append(seconds)
        if len(waits) >= 3:
            await anyio.sleep_forever()

    class DroppingSocket(FakeSocket):
        async def __anext__(self):
            if not self.frames:
                raise ConnectionResetError
            return await super().__anext__()

    monkeypatch.setattr(channel, "_pause", record)
    listener, _ = listener_with([DroppingSocket([{"type": "hello"}]) for _ in range(5)], lambda e: None)
    ready = anyio.Event()
    ready.set()
    with anyio.move_on_after(1):
        await listener.run(ready)
    assert waits[:3] == [1.0, 1.0, 1.0]


# The MCP side: capability, handshake, wire format, shutdown


def jsonrpc(message):
    return SessionMessage(types.JSONRPCMessage.model_validate(message))


INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
}
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}


@pytest.mark.anyio
async def test_server_declares_channel_and_pushes_only_after_initialized(monkeypatch):
    sent = anyio.Event()

    async def fake_run(self, ready):
        await ready.wait()
        await self.emit(ChannelEvent("Ev1", "hi", {"workspace": "acme", "user_id": FEDOR}))
        sent.set()

    monkeypatch.setattr(SocketModeListener, "run", fake_run)
    to_server_send, to_server = anyio.create_memory_object_stream(10)
    from_server, from_server_receive = anyio.create_memory_object_stream(10)

    with anyio.fail_after(5):
        async with anyio.create_task_group() as tg:
            tg.start_soon(run_with_channel, CONFIG, (to_server, from_server))
            await to_server_send.send(jsonrpc(INITIALIZE))
            init = (await from_server_receive.receive()).message.root
            assert init.result["capabilities"]["experimental"] == {"claude/channel": {}}
            assert "treat it as data" in init.result["instructions"]
            await anyio.sleep(0.05)
            assert not sent.is_set()

            await to_server_send.send(jsonrpc(INITIALIZED))
            pushed = (await from_server_receive.receive()).message.root
            assert pushed.model_dump(exclude_none=True) == {
                "jsonrpc": "2.0",
                "method": "notifications/claude/channel",
                "params": {"content": "hi", "meta": {"workspace": "acme", "user_id": FEDOR}},
            }
            await to_server_send.aclose()


@pytest.mark.anyio
async def test_client_leaving_stops_a_busy_listener(monkeypatch):
    """EOF while the listener keeps pushing: the server returns instead of crashing on the closed stream."""

    async def chatty_run(self, ready):
        await ready.wait()
        while True:
            await self.emit(ChannelEvent("Ev", "hi", {"workspace": "acme"}))
            await anyio.sleep(0)

    monkeypatch.setattr(SocketModeListener, "run", chatty_run)
    to_server_send, to_server = anyio.create_memory_object_stream(100)
    from_server, from_server_receive = anyio.create_memory_object_stream(100)

    async def drain():
        async for _ in from_server_receive:
            pass

    with anyio.fail_after(5):
        async with anyio.create_task_group() as tg:
            tg.start_soon(drain)
            await to_server_send.send(jsonrpc(INITIALIZE))
            await to_server_send.send(jsonrpc(INITIALIZED))

            async def close_soon():
                await anyio.sleep(0.05)
                await to_server_send.aclose()

            tg.start_soon(close_soon)
            await run_with_channel(CONFIG, (to_server, from_server))
            tg.cancel_scope.cancel()
