import os
import re

from dotenv import load_dotenv

TOKEN_PREFIX = "SLACK_USER_TOKEN_"
OFF = ("0", "off", "false", "no")


def load_env(default_env_file: str | None = None) -> None:
    """Load SLACK_ENV_FILE (empty = none), else default_env_file; real environment variables win."""
    path = os.environ.get("SLACK_ENV_FILE", default_env_file)
    if path:
        load_dotenv(path)


def alias(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")


def workspace_tokens() -> dict[str, tuple[str, ...]]:
    """Every SLACK_USER_TOKEN_<ALIAS> is a workspace: ACME_CORP -> acme-corp. Several names may collide."""
    out: dict[str, tuple[str, ...]] = {}
    for name in sorted(os.environ):
        if name.startswith(TOKEN_PREFIX) and (key := alias(name[len(TOKEN_PREFIX) :])):
            out[key] = out.get(key, ()) + (name,)
    return out


def style_gate_enabled() -> bool:
    # SLACK_STYLE_GATE=off lets sends through unchecked; slack_lint still works.
    return os.environ.get("SLACK_STYLE_GATE", "on").strip().lower() not in OFF


def read_only() -> bool:
    """SLACK_READ_ONLY=1 removes every tool that changes something in Slack."""
    return os.environ.get("SLACK_READ_ONLY", "").strip().lower() not in ("", *OFF)


def write_channels() -> set[str] | None:
    """SLACK_WRITE_CHANNELS: comma-separated conversation or user ids writes may target; unset = anywhere."""
    raw = os.environ.get("SLACK_WRITE_CHANNELS", "").strip()
    if not raw:
        return None
    return {part.strip() for part in raw.split(",") if part.strip()}
