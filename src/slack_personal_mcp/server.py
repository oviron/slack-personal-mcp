import contextlib
import sys

import anyio
from mcp import types
from mcp.server.stdio import stdio_server
from mcp.shared.message import SessionMessage

from . import tools_extra, tools_files, tools_messages, tools_read, tools_write  # noqa: F401  (register tools)
from .app import drop_write_tools, mcp
from .channel import (
    CAPABILITY,
    INSTRUCTIONS,
    NOTIFICATION,
    ChannelConfig,
    ChannelEvent,
    SocketModeListener,
    configure_logging,
    parse_args,
)
from .config import load_env, read_only


def main(default_env_file: str | None = None, argv: list[str] | None = None) -> None:
    channel = parse_args(sys.argv[1:] if argv is None else argv)
    load_env(default_env_file)
    if read_only():
        drop_write_tools()
    if channel is None:
        mcp.run()
    else:
        configure_logging()
        anyio.run(run_with_channel, channel)


async def run_with_channel(config: ChannelConfig, streams=None) -> None:
    """stdio MCP server that also declares claude/channel and pushes Slack messages once the client is ready."""
    server = mcp._mcp_server
    server.instructions = INSTRUCTIONS
    options = server.create_initialization_options(experimental_capabilities={CAPABILITY: {}})
    ready = anyio.Event()

    async def on_initialized(_: types.InitializedNotification) -> None:
        ready.set()

    server.notification_handlers[types.InitializedNotification] = on_initialized

    async with (stdio_server() if streams is None else contextlib.nullcontext(streams)) as (read_stream, write_stream):

        async def emit(event: ChannelEvent) -> None:
            notification = types.JSONRPCNotification(
                jsonrpc="2.0", method=NOTIFICATION, params={"content": event.content, "meta": event.meta}
            )
            await write_stream.send(SessionMessage(types.JSONRPCMessage(notification)))

        listener = SocketModeListener(config, emit)
        async with anyio.create_task_group() as tg:
            tg.start_soon(listener.run, ready)
            await server.run(read_stream, write_stream, options)
            tg.cancel_scope.cancel()

