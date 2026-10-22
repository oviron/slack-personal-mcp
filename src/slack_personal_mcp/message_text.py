def attachments_text(attachments: list[dict]) -> str:
    parts = []
    for attachment in attachments:
        parts.extend(attachment[key] for key in ("pretext", "title", "text") if attachment.get(key))
        for field in attachment.get("fields", []):
            parts.append(": ".join(str(field[key]) for key in ("title", "value") if field.get(key)))
    return "\n".join(part for part in parts if part)


def _element_text(element: dict) -> str:
    kind = element.get("type")
    text = element.get("text")
    if isinstance(text, str):
        return text
    if kind == "link":
        return element.get("url", "")
    if kind in ("user", "channel", "emoji", "broadcast", "usergroup"):
        key, start, end = {
            "user": ("user_id", "<@", ">"),
            "channel": ("channel_id", "<#", ">"),
            "emoji": ("name", ":", ":"),
            "broadcast": ("range", "<!", ">"),
            "usergroup": ("usergroup_id", "<!subteam^", ">"),
        }[kind]
        return f"{start}{element[key]}{end}" if element.get(key) else ""
    if kind == "date":
        return element.get("fallback", "")
    separator = "" if kind == "rich_text_section" else "\n"
    return separator.join(filter(None, (_element_text(child) for child in element.get("elements", []))))


def blocks_text(blocks: list[dict], has_text: bool = False) -> str:
    """Text from section, context and rich_text blocks. rich_text mirrors the message text people type,
    so it is skipped when the message already has text."""
    parts = []
    for block in blocks:
        if block.get("type") == "section":
            parts.append(_element_text(block.get("text") or {}))
            parts.extend(_element_text(field) for field in block.get("fields", []))
        elif block.get("type") == "context" or (block.get("type") == "rich_text" and not has_text):
            parts.append(_element_text(block))
    return "\n".join(part for part in parts if part)
