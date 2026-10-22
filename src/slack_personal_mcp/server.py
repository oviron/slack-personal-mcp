from . import tools_files, tools_read, tools_write  # noqa: F401  (register tools)
from .app import mcp
from .config import load_env


def main(default_env_file: str | None = None) -> None:
    load_env(default_env_file)
    mcp.run()
