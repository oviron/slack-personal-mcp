"""Run from a clone: .venv/bin/python server.py. Reads .env next to this file unless SLACK_ENV_FILE is set."""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))

from slack_personal_mcp.server import main  # noqa: E402

if __name__ == "__main__":
    main(default_env_file=os.path.join(HERE, ".env"))
