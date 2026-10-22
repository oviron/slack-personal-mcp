from . import tools_files, tools_messages, tools_read, tools_write  # noqa: F401  (register tools)
from .app import drop_write_tools, mcp
from .config import load_env, read_only


def main(default_env_file: str | None = None) -> None:
    load_env(default_env_file)
    if read_only():
        drop_write_tools()
    mcp.run()
