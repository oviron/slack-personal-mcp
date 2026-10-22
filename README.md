# slack-personal-mcp

Minimal MCP server that talks to Slack with per-workspace personal user tokens
(`xoxp-`), so messages post **as the user** with no app attribution badge.
Every Slack tool requires an explicit workspace alias to prevent cross-workspace
reads or sends. Includes a deterministic style gate that blocks AI typography
and AI-slop before sending.

## Tools

- `slack_list_workspaces()` - list configured aliases and verify their Slack identity
- `slack_send_message(workspace, channel_id, text, thread_ts?, allow_promise?)` - post as the user; style gate enforced
- `slack_upload_file(workspace, channel_id, file_path, initial_comment?, title?, thread_ts?, allow_promise?)` - upload a local file as the user (`channel_id` may be a user id `U...` for that DM, `thread_ts` for a thread reply); style gate applies to the comment
- `slack_file_info(workspace, file_id)` - file metadata: name, type, size, owner, where shared
- `slack_list_files(workspace, channel_id?, user_id?, types?, count?)` - files in a channel or by uploader, newest first
- `slack_download_file(workspace, file_id, dest_dir?)` - save a file locally (default `~/Downloads/slack`), returns the path
- `slack_add_reaction(workspace, channel_id, ts, name)` - react to a message as the user (`name` without colons, e.g. `eyes`)
- `slack_remove_reaction(workspace, channel_id, ts, name)` - remove that reaction
- `slack_lint(text)` - dry-run the style gate on a draft
- `slack_search_messages(workspace, query, count?)`
- `slack_read_channel(workspace, channel_id, limit?)`
- `slack_read_thread(workspace, channel_id, thread_ts, limit?)`
- `slack_find_user(workspace, query, limit?)`
- `slack_find_channel(workspace, query, limit?)`

Read and search results list message attachments under `files` (id, name, type, size).

## Style gate

`style.py` runs on every send:

- **errors (hard block)**: AI typography (em/en dash, ellipsis char, curly quotes, nbsp, bullet) and AI-slop phrases.
- **warnings (block unless `allow_promise=true`)**: concrete promises / deadlines.

The phrase lists are English. Set `SLACK_STYLE_GATE=off` to send without the gate;
`slack_lint` keeps working either way.

## Setup

Requires Python 3.10+.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python style.py        # self-test
```

### Slack app and token

Each workspace needs its own Slack app with a user token. At
<https://api.slack.com/apps>: **Create New App** > **From an app manifest**, pick the
workspace and paste

```yaml
display_information:
  name: slack-personal-mcp
oauth_config:
  scopes:
    user:
      - chat:write
      - files:read
      - files:write
      - search:read
      - channels:history
      - groups:history
      - im:history
      - mpim:history
      - channels:read
      - groups:read
      - users:read
      - reactions:write
      - im:write
```

Then **Install to Workspace** and copy the **User OAuth Token** (`xoxp-...`) from
**OAuth & Permissions**. Workspaces that require admin approval for apps will ask an
admin first. Bot tokens are not used.

Without `reactions:write` the reaction tools fail with `missing_scope`; without
`files:read` so do file info, list and download; without `im:write` so does
`conversations.open`, so DM uploads need an existing `D...` id. After changing scopes,
reinstall the app and replace the token.

### Configuration

Tokens are read from the environment, or from `.env` next to `server.py` (gitignored;
keep it `chmod 600`):

```dotenv
SLACK_USER_TOKEN_ACME=xoxp-...
SLACK_USER_TOKEN_SIDE_PROJECT=xoxp-...
```

Each `SLACK_USER_TOKEN_<ALIAS>` adds a workspace alias: `SIDE_PROJECT` becomes
`side-project`. Tools take the alias as `workspace`; `slack_list_workspaces` shows which
account each token belongs to.

| Variable | Meaning |
|---|---|
| `SLACK_USER_TOKEN_<ALIAS>` | user token for one workspace |
| `SLACK_ENV_FILE` | read this env file instead of `.env` next to `server.py` |
| `SLACK_STYLE_GATE` | `off` sends without the style gate (default on) |

## Registration

A stdio server: register `.venv/bin/python server.py` from this folder with your MCP
client, for example

```bash
claude mcp add slack-personal -- /path/to/slack-personal-mcp/.venv/bin/python /path/to/slack-personal-mcp/server.py
```
