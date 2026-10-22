from .slack import SlackError
from .slack import call as slack_call

CONVERSATION_SCOPES = {
    "public_channel": "channels",
    "private_channel": "groups",
    "im": "im",
    "mpim": "mpim",
}


def call(method: str, *, workspace: str, params: dict | None = None) -> dict:
    try:
        return slack_call(method, workspace=workspace, params=params)
    except SlackError as exc:
        if exc.error != "missing_scope":
            raise
        if method == "users.lookupByEmail":
            scopes = "users:read.email"
        elif method.startswith("users."):
            scopes = "users:read"
        elif method == "search.messages":
            scopes = "search:read"
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
