import re


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


def _letters(text: str) -> str:
    return re.sub(r"\W+", "", text).casefold()


def blocks_text(blocks: list[dict], text: str = "") -> str:
    """Text from section, context and rich_text blocks. rich_text usually mirrors the message text
    people type, so a rich_text block whose words are already in text is skipped."""
    parts = []
    for block in blocks:
        if block.get("type") == "section":
            parts.append(_element_text(block.get("text") or {}))
            parts.extend(_element_text(field) for field in block.get("fields", []))
        elif block.get("type") == "context":
            parts.append(_element_text(block))
        elif block.get("type") == "rich_text":
            rich = _element_text(block)
            if not text or _letters(rich) not in _letters(text):
                parts.append(rich)
    return "\n".join(part for part in parts if part)
