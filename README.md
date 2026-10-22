# slack-personal-mcp

Minimal MCP server that talks to Slack with per-workspace personal user tokens
(`xoxp-`), so messages post **as the user** with no app attribution badge.
Every Slack tool requires an explicit workspace alias to prevent cross-workspace
reads or sends. Includes a deterministic style gate that blocks AI typography
and AI-slop before sending.

## Tools

- `slack_list_workspaces()` - list configured aliases and verify their Slack identity
- `slack_send_message(workspace, channel_id, text, thread_ts?, reply_broadcast?, format?, unfurl_links?, allow_promise?)` - post as the user; style gate enforced. `format` is `markdown` (default, standard Markdown up to 12,000 characters) or `mrkdwn`; link previews are off unless `unfurl_links=true`
- `slack_upload_file(workspace, channel_id, file_path, initial_comment?, title?, thread_ts?, allow_promise?)` - upload a local file as the user (`thread_ts` for a thread reply); style gate applies to the comment and title. On failure the error names the step and the `file_id`
- `slack_file_info(workspace, file_id)` - file metadata: name, type, size, owner, mode, every message that shares it, `external_url` for external files
- `slack_list_files(workspace, channel_id?, user_id?, types?, count?, page?)` - files in a channel or by uploader, newest first, with page count
- `slack_download_file(workspace, file_id, dest_dir?)` - save a file locally (default `~/Downloads/slack`), returns the path. Writes to a temporary file and replaces the old copy only after the size matches; external files return their `external_url` instead
- `slack_add_reaction(workspace, channel_id, ts, name)` - react to a message as the user (`name` without colons, e.g. `eyes`)
- `slack_remove_reaction(workspace, channel_id, ts, name)` - remove that reaction
- `slack_lint(text)` - dry-run the style gate on a draft
- `slack_search_messages(workspace, query, count?)`
- `slack_read_channel(workspace, channel_id, limit?)`
- `slack_read_thread(workspace, channel_id, thread_ts, limit?)`
- `slack_find_user(workspace, query, limit?)`
- `slack_find_channel(workspace, query, limit?)`

Read and search results list message attachments under `files` (id, name, type, size).

In every writing tool `channel_id` may be a user id `U...`: the message, file or reaction
goes to the DM with that person (needs `im:write`).

`slack_upload_file` reads and `slack_download_file` writes only inside `SLACK_FILE_ROOTS`
(default: home and the temp folders). Hidden files and folders such as `~/.ssh` or `.env`,
and `~/Library` under the default roots, are refused, symlinks included. The client still
asks you to approve each upload: check the path before you do.

Errors come back as MCP tool errors. Rate-limited calls wait for Slack's `Retry-After`
and retry within 30 seconds. A send that fails after the request left the machine is
never retried: the error says the outcome is unknown, so check the channel first.

## Style gate

`src/slack_personal_mcp/style.py` runs on every send:

- **errors (hard block)**: AI typography (em/en dash, ellipsis char, curly quotes, nbsp, bullet) and AI-slop phrases.
- **warnings (block unless `allow_promise=true`)**: concrete promises / deadlines.

Code spans and blocks, URLs, Slack mentions (`<@U...>`, `<#C...>`, `<!here>`) and quoted
lines are skipped; link labels are checked like any other text;
diagnostics include zero-based character ranges in the original text. Phrase lists
cover English and common Russian slop and promises; typography rules apply to both
languages. Set `SLACK_STYLE_GATE=off` to send without the gate;
`slack_lint` keeps working either way.

## Setup

Requires Python 3.10+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync                # creates .venv with the locked dependencies
uv run pytest          # offline tests
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
keep it `chmod 600`; `.env.example` is a template):

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
| `SLACK_READ_ONLY` | `1` hides every tool that changes Slack or local files |
| `SLACK_FILE_ROOTS` | folders, separated by `:`, that upload reads from and download writes to; default home and temp |
| `SLACK_WRITE_CHANNELS` | comma-separated ids (`C...`, `D...`, `U...`) writes may target; a DM by user id needs that `U...` listed; unset allows any |

## Registration

A stdio server: register `.venv/bin/python server.py` from this folder with your MCP
client, for example

```bash
claude mcp add slack-personal -- /path/to/slack-personal-mcp/.venv/bin/python /path/to/slack-personal-mcp/server.py
```
