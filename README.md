# slack-personal-mcp

An MCP server that works in Slack with your own user token (`xoxp-`). Messages, files,
reactions and status changes come from you, without the "via app" badge a bot token adds.

- Every tool that touches Slack takes an explicit `workspace` alias, so a read or a send
  never lands in the wrong workspace by default.
- A style gate checks outgoing text for AI typography (em dash, curly quotes, ellipsis
  character), stock AI phrases and unapproved promises before anything is sent.
- `SLACK_READ_ONLY=1` hides every tool that changes Slack or local files;
  `SLACK_WRITE_CHANNELS` limits writes to listed conversations.

It runs over stdio on your machine. The official Slack MCP server is a hosted service with
OAuth and admin approval; this one is for a person who wants their own token on their own
computer.

## Tools

30 tools. In the table, R is read-only, W writes to Slack, D deletes or overwrites
something. `SLACK_READ_ONLY=1` leaves the 19 R tools.

| Tool | | What it does | User token scopes |
|---|---|---|---|
| `slack_list_workspaces()` | R | Configured aliases and whose token each one is (`auth.test`) | none |
| `slack_lint(text)` | R | Run the style gate on a draft without sending | none |
| `slack_send_message(workspace, channel_id, text, thread_ts?, reply_broadcast?, format?, unfurl_links?, allow_promise?)` | W | Post as you | `chat:write` |
| `slack_edit_message(workspace, channel_id, ts, text, format?, allow_promise?)` | W | Edit one of your messages | `chat:write`; `*:history` by type to check the author |
| `slack_delete_message(workspace, channel_id, ts)` | D | Delete one of your messages | `chat:write`; `*:history` by type to check the author |
| `slack_schedule_message(workspace, channel_id, text, post_at, thread_ts?, reply_broadcast?, format?, allow_promise?)` | W | Schedule a message up to 120 days ahead | `chat:write` |
| `slack_list_scheduled(workspace, channel_id?, limit?, cursor?)` | R | Your scheduled messages, soonest first | none |
| `slack_cancel_scheduled(workspace, channel_id, scheduled_message_id)` | D | Cancel a scheduled message | `chat:write` |
| `slack_get_permalink(workspace, channel_id, ts)` | R | Link to a message | none |
| `slack_add_reaction(workspace, channel_id, ts, name)` | W | React as you (`name` without colons, e.g. `eyes`) | `reactions:write` |
| `slack_remove_reaction(workspace, channel_id, ts, name)` | D | Remove your reaction | `reactions:write` |
| `slack_get_reactions(workspace, channel_id, ts)` | R | Reactions on a message, with who reacted | `reactions:read`, `users:read` |
| `slack_set_status(workspace, text?, emoji?, expires_in_minutes?, allow_promise?)` | W | Set or clear your status | `users.profile:write` |
| `slack_mark_read(workspace, channel_id, ts)` | W | Move your read marker | `channels:write`, `groups:write`, `im:write` or `mpim:write` by conversation type |
| `slack_unread(workspace, channel_ids, limit?)` | R | Messages after your read marker in up to 20 named conversations | `*:read` and `*:history` by type |
| `slack_read_channel(workspace, channel_id, limit?, cursor?, oldest?, latest?, inclusive?)` | R | One page of channel or DM history | `channels:history`, `groups:history`, `im:history`, `mpim:history` by type |
| `slack_read_thread(workspace, channel_id, thread_ts, limit?, cursor?, oldest?, latest?, inclusive?, all_pages?, max_pages?)` | R | A thread, one page or all of it | same as above |
| `slack_search_messages(workspace, query, count?, page?, sort?, sort_dir?, in_channel?, from_user?, before?, after?, has?)` | R | Message search with Slack query syntax | `search:read` |
| `slack_search(workspace, query, content_types?, cursor?, limit?, channel_types?)` | R | Real-time Search over messages, files, channels and users | `search:read.public`; `search:read.private`, `search:read.im`, `search:read.mpim`, `search:read.files`, `search:read.users` for those types |
| `slack_find_user(workspace, query, limit?, refresh?)` | R | People by name, exact email or `U...` id | `users:read`; `users:read.email` for email |
| `slack_find_channel(workspace, query, limit?, refresh?, types?)` | R | Conversations by name, including DMs with `types="im,mpim"` | `channels:read`, `groups:read`, `im:read`, `mpim:read` by type; `users:read` for DM names |
| `slack_user_info(workspace, user_id)` | R | Name, title, email, timezone, status | `users:read`; `users:read.email` for the email |
| `slack_channel_info(workspace, channel_id)` | R | Name, topic, purpose, member count, your read marker | `*:read` by type |
| `slack_list_usergroups(workspace, include_users?)` | R | User groups and member counts | `usergroups:read` |
| `slack_list_emoji(workspace, query?)` | R | Custom emoji | `emoji:read` |
| `slack_read_canvas(workspace, canvas_id, content_type?)` | R | Canvas as Markdown or HTML | `canvases:read` |
| `slack_upload_file(workspace, channel_id, file_path, initial_comment?, title?, thread_ts?, allow_promise?)` | W | Upload a local file as you | `files:write` |
| `slack_file_info(workspace, file_id)` | R | File metadata and every message that shares it | `files:read` |
| `slack_list_files(workspace, channel_id?, user_id?, types?, count?, page?)` | R | Files in a channel or by a person, newest first | `files:read` |
| `slack_download_file(workspace, file_id, dest_dir?)` | D | Save a file locally (default `~/Downloads/slack`) | `files:read` |

`*:read` and `*:history` stand for the `channels:`, `groups:`, `im:` or `mpim:` scope that
matches the conversation type. Message authors get names from a directory cache, which needs `users:read`. In every
writing tool `channel_id` may be a user id `U...`: the message, file or reaction goes to
your DM with that person, which needs `im:write`.

If a scope is missing, the error names it. Add it under **User Token Scopes**, reinstall
the app and put the new token in `.env`.

## How the tools behave

**Sending.** `format="markdown"` (the default) sends standard Markdown as `markdown_text`,
up to 12,000 characters; `format="mrkdwn"` sends Slack's own markup as `text`, up to
40,000. Link previews are off unless `unfurl_links=true`. To reply in a thread, pass the
`ts` of the thread's first message as `thread_ts`, not the `ts` of a reply.
`reply_broadcast=true` also shows the reply in the channel.

**Your messages only.** `slack_edit_message` and `slack_delete_message` check the author
first and refuse messages by anyone else, even when an admin token could change them.

**Approval.** The MCP client, not this server, asks you before it calls a tool. Tools
carry MCP annotations (read-only, destructive, idempotent), so a client can ask only for
writes. Keep approval on for writes and uploads and read the text or path before you
allow it.

**Files.** `slack_upload_file` reads and `slack_download_file` writes only inside
`SLACK_FILE_ROOTS` (default: home and the temp folders). Hidden files and folders such as
`~/.ssh` or `.env`, and `~/Library` under the default roots, are refused, symlinks
included. Downloads go to a temporary file and replace an existing copy only once the
size matches. Files stored outside Slack return their `external_url` instead.

**Pages.** Reads return one page at a time and say whether more exist.
`slack_read_channel` returns `{messages, next_cursor, has_more}`: pass `next_cursor` back
as `cursor` with the same other arguments. `slack_read_thread` adds `complete`, and with
`all_pages=true` follows the cursor up to `max_pages` (default 10).
`slack_search_messages` returns `{messages, total, page, pages}`; `slack_search` and
`slack_list_scheduled` return `next_cursor`. `oldest` and `latest` take Slack timestamps
such as `1729612345.123456`.

**Messages.** Each message keeps Slack's `ts` and `text` and adds `time` (ISO 8601, UTC),
`user_id` and `user_name`, thread fields, reactions and `files`. Text from legacy
attachments is in `attachments_text`, text from blocks in `blocks_text`; a `rich_text`
block that only repeats `text` is left out. Empty fields are dropped.

**Names.** `slack_find_user` and `slack_find_channel` search full `users.list` and
`conversations.list` directories, cached in memory for an hour per workspace;
`refresh=true` reloads. An exact email goes through `users.lookupByEmail` and a `U...`
id through `users.info`, without the directory.

**Unread.** Slack's API has no unread inbox like the app has. `slack_unread` takes the
conversations you name and counts messages from others after your read marker.

**Search.** `slack_search_messages` uses the classic `search.messages`. `slack_search`
uses the [Real-time Search API](https://docs.slack.dev/reference/methods/assistant.search.context/),
which works for internal apps; if your workspace does not offer it, the error says so.

**Results.** Tools return structured content with an output schema. Errors come back as
MCP tool errors.

## Style gate

`src/slack_personal_mcp/style.py` checks every message, edit, scheduled message, status,
upload comment and file title before it leaves:

- **Errors** block the send: AI typography (em and en dash, ellipsis character, curly
  quotes, non-breaking space, bullet character) and stock AI phrases.
- **Warnings** block the send unless `allow_promise=true`: concrete promises and
  deadlines.

Code, URLs, Slack mentions (`<@U...>`, `<#C...>`, `<!here>`) and quoted lines are not
checked; link labels are. Each finding gives a zero-based character range. Phrase lists
cover English and Russian. `SLACK_STYLE_GATE=off` turns the gate off for sends;
`slack_lint` works either way.

## Setup

Requires Python 3.10+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/oviron/slack-personal-mcp
cd slack-personal-mcp
uv sync
```

`uv run pytest` runs the offline tests.

### Slack app and token

Each workspace needs its own Slack app.

1. At <https://api.slack.com/apps> choose **Create New App**, then **From an app
   manifest**, pick the workspace and paste
   [`slack-app-manifest.yaml`](slack-app-manifest.yaml).
2. **Install to Workspace**. Workspaces that need admin approval for apps ask an admin
   first.
3. Copy the **User OAuth Token** (`xoxp-...`) from **OAuth & Permissions**.

The manifest asks for every scope in the table above. Remove the ones you don't need
before installing; the matching tools then fail with `missing_scope`. `channels:write`
and `groups:write` are only for `slack_mark_read`, but they also allow managing channels,
so leave them out if you don't need it.

Keep token rotation off: the manifest sets `token_rotation_enabled: false`. With rotation
on, Slack issues `xoxe.xoxp-` tokens that expire after 12 hours, this server does not
refresh them, and Slack does not let you turn rotation off again. An app with rotation on
has to be replaced by a new one.

The app is internal to your workspace, so Slack's
[May 2025 rate limits](https://docs.slack.dev/changelog/2025/05/29/rate-limit-changes-for-non-marketplace-apps)
for commercially distributed apps (one `conversations.history` call a minute) do not
apply. Don't distribute one app to other workspaces: create one per workspace.

### Configuration

Tokens come from the environment, or from `.env` next to `server.py` (ignored by git;
keep it `chmod 600`). Copy [`.env.example`](.env.example) to start:

```dotenv
SLACK_USER_TOKEN_ACME=xoxp-...
SLACK_USER_TOKEN_SIDE_PROJECT=xoxp-...
```

Each `SLACK_USER_TOKEN_<ALIAS>` adds a workspace; `SIDE_PROJECT` becomes the alias
`side-project`. `slack_list_workspaces` shows whose token each alias holds:

```json
{"workspaces": [{"workspace": "acme", "configured": true, "team": "Acme", "team_id": "T0123",
  "url": "https://acme.slack.com/", "user": "jane", "user_id": "U0456"}]}
```

| Variable | Meaning |
|---|---|
| `SLACK_USER_TOKEN_<ALIAS>` | User token for one workspace |
| `SLACK_ENV_FILE` | Read this file instead of `.env` next to `server.py`; empty reads none |
| `SLACK_STYLE_GATE` | `off` sends without the style gate (default on) |
| `SLACK_READ_ONLY` | `1` hides every tool that changes Slack or local files |
| `SLACK_FILE_ROOTS` | Folders, separated by `:`, that uploads read from and downloads write to; default home and temp |
| `SLACK_WRITE_CHANNELS` | Comma-separated ids (`C...`, `D...`, `U...`) writes may target; a DM by user id needs that `U...` listed; unset allows any |

## Registering with a client

The server speaks MCP over stdio. Point the client at the clone's Python and
`server.py`; the examples use `/path/to/slack-personal-mcp`.

Claude Code:

```bash
claude mcp add slack-personal -- /path/to/slack-personal-mcp/.venv/bin/python /path/to/slack-personal-mcp/server.py
```

Codex, in `~/.codex/config.toml`:

```toml
[mcp_servers.slack-personal]
command = "/path/to/slack-personal-mcp/.venv/bin/python"
args = ["/path/to/slack-personal-mcp/server.py"]
```

Claude Desktop (`claude_desktop_config.json`) and Cursor (`.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "slack-personal": {
      "command": "/path/to/slack-personal-mcp/.venv/bin/python",
      "args": ["/path/to/slack-personal-mcp/server.py"]
    }
  }
}
```

Without a clone, uv runs a tagged release straight from GitHub. There is no `server.py`
next to it, so name the env file:

```bash
SLACK_ENV_FILE=~/.config/slack-personal-mcp/.env uvx --from git+https://github.com/oviron/slack-personal-mcp@v0.2.2 slack-personal-mcp
```

## Troubleshooting

| Error | What to do |
|---|---|
| `missing_scope` | Add the scope the error names, reinstall the app, replace the token |
| `not_in_channel` | Join the conversation in Slack first |
| `channel_not_found` | Check the id; private conversations and DMs need you to be a member |
| `invalid_auth`, `token_revoked`, `account_inactive` | Create a new user token and replace it in `.env` |
| `token_expired` | Token rotation is on and cannot be turned off: create a new app from the manifest and use its token |
| `ratelimited` | The server already waited for `Retry-After` within 30 seconds; try again later |
| `outcome unknown` | A send failed after it left your machine and was not retried; check the channel before sending again |
| `feature_not_enabled` from `slack_search` | Real-time Search is not available in this workspace; use `slack_search_messages` |

Changes to the code or `.env` take effect when the client restarts the server.

## Releasing

The package is not published to PyPI or the MCP Registry; releases are git tags.

1. Bump the version in `pyproject.toml` and `src/slack_personal_mcp/__init__.py`, run
   `uv lock`, then merge.
2. Tag `vX.Y.Z` on `main` and create a GitHub release.

## License

MIT
