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
- `slack_search_messages(workspace, query, count?, page?, sort?, sort_dir?, in_channel?, from_user?, before?, after?, has?)`
- `slack_read_channel(workspace, channel_id, limit?, cursor?, oldest?, latest?, inclusive?)`
- `slack_read_thread(workspace, channel_id, thread_ts, limit?, cursor?, oldest?, latest?, inclusive?, all_pages?, max_pages?)`
- `slack_find_user(workspace, query, limit?, refresh?)`
- `slack_find_channel(workspace, query, limit?, refresh?, types?)`
- `slack_user_info(workspace, user_id)`
- `slack_channel_info(workspace, channel_id)`
- `slack_get_permalink(workspace, channel_id, ts)` - link to a message
- `slack_edit_message(workspace, channel_id, ts, text, format?, allow_promise?)` - edit one of your own messages; style gate enforced
- `slack_delete_message(workspace, channel_id, ts)` - delete one of your own messages. Both refuse messages by others, even with an admin token
- `slack_schedule_message(workspace, channel_id, text, post_at, thread_ts?, reply_broadcast?, format?, allow_promise?)` - schedule a message up to 120 days ahead (`post_at` is ISO 8601 with a timezone or unix seconds)
- `slack_list_scheduled(workspace, channel_id?)`, `slack_cancel_scheduled(workspace, channel_id, scheduled_message_id)` - pending scheduled messages
- `slack_set_status(workspace, text?, emoji?, expires_in_minutes?)` - set or clear your status (needs `users.profile:write`); style gate on the text
- `slack_unread(workspace, channel_ids)` - messages after your read marker in up to 20 named channels (Slack's API has no full unread inbox)
- `slack_mark_read(workspace, channel_id, ts)` - move your read marker (needs `channels:write`, `groups:write`, `im:write` or `mpim:write` for that kind of conversation)

## Чтение и поиск

Инструменты из `tools_read.py` возвращают объекты в MCP `structuredContent` с
`outputSchema`. Списки обёрнуты в `workspaces`, `users`, `channels` или `messages`;
инструменты информации возвращают `user` или `channel`.

`slack_read_channel` читает одну страницу и возвращает
`{messages, next_cursor, has_more}`. Для продолжения передайте `next_cursor` как
`cursor`, сохранив остальные параметры. `oldest` и `latest` принимают Slack timestamps,
например `1729612345.123456`; `inclusive=true` включает сообщения на границах диапазона.
`limit` задаёт размер страницы: по умолчанию 20 для канала и 50 для треда, от 1 до 1000.
Slack может вернуть меньше сообщений, поэтому проверяйте курсор и `has_more`.

`slack_read_thread` принимает `thread_ts` корневого сообщения и возвращает те же поля
плюс `complete`. По умолчанию читает одну страницу; `all_pages=true` обходит страницы
до исчерпания курсора или бюджета `max_pages` (по умолчанию 10, от 1 до 100).
При оставшихся сообщениях `complete=false`. Обход также останавливается, если Slack
не дал новый курсор. Полнота относится к переданному диапазону и начальному курсору.

Сообщения сохраняют исходные `ts` и `text`; рядом с `ts` есть время `time` в ISO 8601
с UTC `+00:00`. Автор представлен раздельными `user_id` и `user_name`, в том числе в
поиске. Также возвращаются `thread_ts`, `reply_count`, `subtype`, `bot_id`, `bot_name`,
`edited`, реакции с `name` и `count`, файлы в `files`. Текст вложений вынесен в
`attachments_text` (pretext, title, text, fields), текст блоков в `blocks_text`
(section, context; rich_text только когда в нём есть то, чего нет в `text`). Пустые поля пропускаются.

`slack_search_messages` сохраняет сырой `query` и добавляет фильтры `in_channel`,
`from_user`, `before`, `after`, `has` как операторы `in:`, `from:`, `before:`, `after:`,
`has:`. Пример: `query="release", in_channel="C123", from_user="U123", has="file"`.
`count` от 1 до 100 (по умолчанию 15), `page` от 1 до 100, `sort=score|timestamp`,
`sort_dir=asc|desc` (по умолчанию `score`, `desc`). Ответ: `{messages, total, page, pages}`.

Поиск по имени использует полные каталоги `users.list` и `conversations.list`.
Кеш живёт в памяти процесса один час, изолирован по workspace и `team_id`, защищён
от одновременной загрузки из потоков и сбрасывается при смене токена.
`refresh=true` обновляет каталог соответствующего поиска; для DM обновляет и имена.
Запуск сервера не загружает каталоги. При первом обращении к каталогу вызывается
`auth.test` для определения `team_id`; первое чтение сообщений с авторами загружает
каталог пользователей. Имена берутся из этого же кеша.

Точный email в `slack_find_user` идёт через `users.lookupByEmail`, `U...`/`W...` через
`users.info`, без сканирования каталога. `slack_user_info` и `slack_channel_info`
возвращают данные `users.info` и `conversations.info` без кеша.

`slack_find_channel` по умолчанию ищет неархивные публичные и приватные каналы
(`types="public_channel,private_channel"`). Для личных и групповых переписок передайте
`types="im,mpim"`; пустой `query` выводит первые `limit` результатов. У DM поле `name`
содержит имя собеседника из кеша, а `user_id` его идентификатор. Каталоги разных наборов
`types` кешируются отдельно. Типы соответствуют [conversations.list](https://docs.slack.dev/reference/methods/conversations.list/).

| Чтение | User token scopes |
|---|---|
| История и треды | `channels:history`, `groups:history`, `im:history`, `mpim:history` по типу переписки |
| Поиск сообщений | `search:read` |
| Каталог, профиль и имена авторов/собеседников | `users:read` |
| Точный email | `users:read.email` ([users.lookupByEmail](https://docs.slack.dev/reference/methods/users.lookupByEmail/)) |
| Каталог и информация о каналах | `channels:read`, `groups:read` по типу канала |
| Каталог и информация о DM/MPIM | `im:read`, `mpim:read` по типу переписки |

Для email и DM/MPIM добавьте соответствующие scopes в **User Token Scopes** приложения:
их нет в базовом манифесте ниже. При `missing_scope` ошибка называет нужные scopes.
После изменения scopes переустановите приложение и замените токен.

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
