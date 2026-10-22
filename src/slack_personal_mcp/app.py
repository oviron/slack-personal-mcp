import functools
from collections.abc import Callable

import anyio.to_thread
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

mcp = FastMCP("slack-personal")

# Tools that change something in Slack or on disk; SLACK_READ_ONLY drops them at startup.
WRITE_TOOLS: set[str] = set()


def tool(
    *, write: bool, idempotent: bool = False, destructive: bool = False, local: bool = False
) -> Callable[[Callable], Callable]:
    """Register a sync tool: it runs in a worker thread so slow Slack calls do not block the MCP session."""

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        async def run(**kwargs):
            return await anyio.to_thread.run_sync(functools.partial(fn, **kwargs))

        annotations = ToolAnnotations(
            readOnlyHint=not write,
            destructiveHint=destructive if write else None,
            idempotentHint=idempotent if write else None,
            openWorldHint=not local,
        )
        mcp.add_tool(run, name=fn.__name__, description=fn.__doc__, annotations=annotations)
        if write:
            WRITE_TOOLS.add(fn.__name__)
        return fn

    return decorator


def drop_write_tools() -> None:
    for name in sorted(WRITE_TOOLS):
        mcp.remove_tool(name)
