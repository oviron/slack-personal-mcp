from .slack import SlackError
from .slack import call as slack_call

CONVERSATION_SCOPES = {
    "public_channel": "channels",
    "private_channel": "groups",
    "im": "im",
    "mpim": "mpim",
}

METHOD_SCOPES = {
    "users.lookupByEmail": "users:read.email",
    "search.messages": "search:read",
    "emoji.list": "emoji:read",
    "usergroups.list": "usergroups:read",
    "reactions.get": "reactions:read",
    "canvases.getContent": "canvases:read",
    "assistant.search.context": (
        "search:read.public, search:read.private, search:read.im, search:read.mpim, "
        "search:read.files, search:read.users"
    ),
}


def call(method: str, *, workspace: str, params: dict | None = None, json_body: dict | None = None) -> dict:
    try:
        return slack_call(method, workspace=workspace, params=params, json_body=json_body)
    except SlackError as exc:
        if exc.error != "missing_scope":
            raise
        if method in METHOD_SCOPES:
            scopes = METHOD_SCOPES[method]
        elif method.startswith("users."):
            scopes = "users:read"
        elif method.startswith("conversations."):
            args = params or {}
            if method == "conversations.list":
                prefixes = [CONVERSATION_SCOPES[t] for t in args["types"].split(",")]
            else:
                prefixes = {"C": ["channels", "groups"], "D": ["im"], "G": ["groups", "mpim"]}.get(
                    args.get("channel", "")[:1], list(CONVERSATION_SCOPES.values())
                )
            suffix = "history" if method in ("conversations.history", "conversations.replies") else "read"
            scopes = ", ".join(f"{prefix}:{suffix}" for prefix in prefixes)
        else:
            scopes = "the scope required by this method"
        raise SlackError(
            f"Slack {method}: missing_scope. Add the applicable user token scope(s): {scopes}; "
            "reinstall the app and replace the token.",
            error=exc.error,
        ) from None
