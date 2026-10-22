import re

IGNORED = re.compile(
    r"```[\s\S]*?(?:```|\Z)|`[^`\r\n]*`|<[@#!][^>\r\n]*>|(?:https?://|mailto:)[^\s<>|`]+|^[ \t]*>[^\r\n]*",
    re.IGNORECASE | re.MULTILINE,
)

# AI typography tells: char -> human replacement hint. Hard-blocked.
AI_TYPOGRAPHY = {
    "—": 'em dash (—) -> use "-" or split the sentence',
    "–": 'en dash (–) -> use "-"',
    "…": 'ellipsis (…) -> use "..."',
    "‘": "left single quote -> use '",
    "’": "right single quote/apostrophe -> use '",
    "“": 'left double quote -> use "',
    "”": 'right double quote -> use "',
    "«": 'guillemet («) -> use "',
    "»": 'guillemet (») -> use "',
    "„": 'low double quote -> use "',
    " ": "non-breaking space -> use a normal space",
    " ": "narrow no-break space -> use a normal space",
    "•": 'bullet (•) -> use "-"',
}

# AI-slop phrases. Hard-blocked.
SLOP = [
    r"\bi hope (this|that) helps\b",
    r"\bi hope this (message|email) finds you well\b",
    r"\blet me know if you(?: (?:have|need|want)|(?:['’]d| would) like)\b",
    r"\bfeel free\b",
    r"\b(great|good|excellent) question\b",
    r"\b(i['’]d|i would) be (happy|glad) to\b",
    r"\bhappy to help\b",
    r"\bdelve\b",
    r"\b(deep dive|dive into|let's dive)\b",
    r"\bit'?s (worth|important) (noting|mentioning|to note)\b",
    r"\bplease note that\b",
    r"\bin conclusion\b",
    r"\bto summari[sz]e\b",
    r"\brest assured\b",
    r"\bseamless(ly)?\b",
    r"\bleverag(?:e[sd]?|ing)\b",
    r"\butili[sz](?:e[sd]?|ing)\b",
    r"\bfirst and foremost\b",
    r"\bat the end of the day\b",
    r"\bneedless to say\b",
    r"\bas an ai\b",
    r"\bi apologi[sz]e for\b",
    r"\bcertainly[!,]",
    r"\babsolutely[!,]",
    r"\bнадеюсь,? это поможет\b",
    r"\bне стесняйтесь\b",
    r"\bобращайтесь,? если\b",
    r"\bотличный вопрос\b",
    r"\bбуду рад(?:а)? помочь\b",
]

DEADLINE = (
    r"(?:tomorrow|tonight|today|eod|(?:the )?end of (?:the )?day|(?:this|next) week|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
)
INTERVAL = r"\bin \d+ (?:minute|hour|day|week)s?\b"
PROMISE_CONTEXT = r"\b(?:will|(?:i|we|you|he|she|it|they)['’]ll|promise|guarantee)\b"
NEGATION = (
    r"\b(?:won['’]t|wouldn['’]t|can['’]t|cannot|don['’]t|doesn['’]t|didn['’]t|"
    r"(?:will|would) (?:not|never)|(?:can|do|does|did) not|"
    r"not|never|no|не)"
)
NEGATED_MAIN_PROMISE = (
    rf"{NEGATION}\s+(?:promise|guarantee|обещаю|гарантирую)\b"
    r"(?:\s+(?:что|that)\b.*|(?:(?!\b(?:and|и)\b).)*)$"
)

RU_DEADLINE = (
    r"(?:завтра|сегодня|послезавтра|на (?:этой|следующей) неделе|"
    r"в (?:понедельник|вторник|среду|четверг|пятницу|субботу|воскресенье)|"
    r"через \d+ (?:минуту|минуты|минут|час|часа|часов|день|дня|дней|неделю|недели|недель))"
)
RU_BY = (
    r"(?:к (?:понедельнику|среде|четвергу|пятнице|субботе|воскресенью|вечеру|утру|обеду|концу (?:дня|недели))|"
    r"ко вторнику|до (?:вечера|обеда|конца (?:дня|недели)))"
)
RU_PROMISE_VERB = (
    r"(?:сделаю|сделаем|пришлю|пришлём|пришлем|отправлю|отправим|закончу|закончим|доделаю|доделаем|"
    r"починю|починим|выложу|выложим|скину|подготовлю|подготовим|напишу|напишем|посмотрю|посмотрим|"
    r"проверю|проверим|вернусь|отвечу)"
)
RU_PROMISE_CONTEXT = rf"\b(?:{RU_PROMISE_VERB}|будет (?:готово|готов|готова|сделано|исправлено)|обещаю|гарантирую)\b"
BY_DEADLINE = rf"\bby {DEADLINE}\b"
RU_BY_DEADLINE = rf"\b{RU_BY}\b"

# Concrete promises / deadlines. Warn-only (overridable with allow_promise).
PROMISES = [
    r"\bi(?:['’]ll| will) (?:(?:have|get|finish|deliver|send|fix|ship|deploy|do)\b|"
    rf"(?!not\b|never\b)[a-z]+\b[^.!?;,\r\n]*?\b{DEADLINE}\b)",
    r"\bwill be (done|ready|fixed|deployed|merged|shipped)\b",
    BY_DEADLINE,
    r"\bguarantee(d|s)?\b",
    r"\bdefinitely\b",
    r"\bi promise\b",
    r"\bfor sure\b",
    r"\bno later than\b",
    INTERVAL,
    rf"\b{RU_PROMISE_VERB}\b[^.!?;,\r\n]*?\b{RU_DEADLINE}\b",
    RU_BY_DEADLINE,
    r"\bбудет (?:готово|готов|готова|сделано|исправлено)\b[^.!?;,\r\n]*?\b(?:завтра|сегодня)\b",
    r"\b(?:гарантирую|обещаю)\b",
    r"\bточно будет готово\b",
]


def _negated(clause: str, start: int) -> bool:
    return bool(
        re.search(rf"{NEGATION}\s+(?:a\s+)?$", clause[:start], re.IGNORECASE)
        or re.match(rf"{PROMISE_CONTEXT}\s+(?:not|never)\b", clause[start:], re.IGNORECASE)
        or re.search(NEGATED_MAIN_PROMISE, clause[:start], re.IGNORECASE)
    )


def check(text: str) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    masked = IGNORED.sub(lambda match: re.sub(r"[^\r\n]", " ", match.group()), text)

    for ch, hint in AI_TYPOGRAPHY.items():
        for match in re.finditer(re.escape(ch), masked):
            errors.append(f"typography at [{match.start()}:{match.end()}]: {match.group()!r}; {hint}")

    for pat in SLOP:
        for match in re.finditer(pat, masked, re.IGNORECASE):
            errors.append(f"slop phrase at [{match.start()}:{match.end()}]: {match.group()!r}")

    promise_text = re.sub(
        r"\b(?:but|however|но)\b", lambda match: ";" * len(match.group()), masked, flags=re.IGNORECASE
    )
    promise_text = re.sub(r",(?=[ \t]*(?:что|that)\b)", " ", promise_text, flags=re.IGNORECASE)
    for clause_match in re.finditer(r"[^.!?;,\r\n]+", promise_text):
        clause = clause_match.group()
        for pat in PROMISES:
            for match in re.finditer(pat, clause, re.IGNORECASE):
                if _negated(clause, match.start()):
                    continue
                if pat in (INTERVAL, BY_DEADLINE, RU_BY_DEADLINE):
                    context = clause[: match.start()]
                    if not context.strip():
                        context = clause
                    context_pattern = RU_PROMISE_CONTEXT if pat == RU_BY_DEADLINE else PROMISE_CONTEXT
                    cues = list(re.finditer(context_pattern, context, re.IGNORECASE))
                    standalone_deadline = pat != INTERVAL and clause.strip() == match.group()
                    if not standalone_deadline and (not cues or _negated(clause, cues[-1].start())):
                        continue
                    if pat == INTERVAL and (
                        re.search(r"\b(?:failed|took|ago)\b", context, re.IGNORECASE)
                        or re.match(r" +ago\b", clause[match.end() :], re.IGNORECASE)
                    ):
                        continue
                start, end = clause_match.start() + match.start(), clause_match.start() + match.end()
                warnings.append(f"concrete promise at [{start}:{end}]: {text[start:end]!r}")

    return {"errors": errors, "warnings": warnings}


def format_violations(result: dict, allow_promise: bool) -> str:
    lines = []
    if result["errors"]:
        lines.append("ERRORS (must fix):")
        lines += [f"  - {e}" for e in result["errors"]]
    if result["warnings"]:
        tag = "WARNINGS (resend with allow_promise=true if intended):"
        lines.append(tag)
        lines += [f"  - {w}" for w in result["warnings"]]
    return "\n".join(lines)
