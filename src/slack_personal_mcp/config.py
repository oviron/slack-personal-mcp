import os

from dotenv import load_dotenv

TOKEN_PREFIX = "SLACK_USER_TOKEN_"


def load_env(default_env_file: str | None = None) -> None:
    """Load SLACK_ENV_FILE, else default_env_file; real environment variables win."""
    path = os.environ.get("SLACK_ENV_FILE") or default_env_file
    if path:
        load_dotenv(path)


def workspace_tokens() -> dict[str, tuple[str, ...]]:
    # Every SLACK_USER_TOKEN_<ALIAS> in the environment is a workspace: ACME_CORP -> acme-corp.
    return {
        name[len(TOKEN_PREFIX) :].lower().replace("_", "-"): (name,)
        for name in sorted(os.environ)
        if name.startswith(TOKEN_PREFIX)
    }


def style_gate_enabled() -> bool:
    # SLACK_STYLE_GATE=off lets sends through unchecked; slack_lint still works.
    return os.environ.get("SLACK_STYLE_GATE", "on").strip().lower() not in ("0", "off", "false", "no")
