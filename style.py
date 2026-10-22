import re

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
    r"\blet me know if you (have|need|'d)\b",
    r"\bfeel free to\b",
    r"\b(great|good|excellent) question\b",
    r"\b(i'd|i would) be (happy|glad) to\b",
    r"\bhappy to help\b",
    r"\bdelve\b",
    r"\b(deep dive|dive into|let's dive)\b",
    r"\bit'?s (worth|important) (noting|mentioning|to note)\b",
    r"\bplease note that\b",
    r"\bin conclusion\b",
    r"\bto summari[sz]e\b",
    r"\brest assured\b",
    r"\bseamless(ly)?\b",
    r"\bleverag(e|ing)\b",
    r"\butili[sz]e\b",
    r"\bfirst and foremost\b",
    r"\bat the end of the day\b",
    r"\bneedless to say\b",
    r"\bas an ai\b",
    r"\bi apologi[sz]e for\b",
    r"\bcertainly[!,]",
]

# Concrete promises / deadlines. Warn-only (overridable with allow_promise).
PROMISES = [
    r"\bi('| wi)ll (have|get|finish|deliver|send|fix|ship|deploy|do)\b",
    r"\bwill be (done|ready|fixed|deployed|merged|shipped)\b",
    r"\bby (tomorrow|tonight|today|eod|end of day|monday|tuesday|wednesday|thursday|friday|next week)\b",
    r"\bguarantee(d|s)?\b",
    r"\bdefinitely\b",
    r"\bi promise\b",
    r"\bfor sure\b",
    r"\bno later than\b",
    r"\bin \d+ (minutes|hours|days)\b",
]


def check(text: str) -> dict:
    errors: list[str] = []
    warnings: list[str] = []

    for ch, hint in AI_TYPOGRAPHY.items():
        if ch in text:
            errors.append(f"typography: {hint}")

    low = text.lower()
    for pat in SLOP:
        m = re.search(pat, low)
        if m:
            errors.append(f'slop phrase: "{m.group(0).strip()}"')

    for pat in PROMISES:
        m = re.search(pat, low)
        if m:
            warnings.append(f'concrete promise: "{m.group(0).strip()}"')

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


if __name__ == "__main__":
    clean = check("AFAIK no. One small backend fix is left, PR is up. The backend lead knows the status better.")
    assert clean["errors"] == [] and clean["warnings"] == [], clean

    dirty = check("Great question — I’ll leverage this and delve in. It will be done by tomorrow.")
    assert any("em dash" in e for e in dirty["errors"]), dirty
    assert any("right single quote" in e for e in dirty["errors"]), dirty
    assert any("great question" in e for e in dirty["errors"]), dirty
    assert any("leverag" in e for e in dirty["errors"]), dirty
    assert any("delve" in e for e in dirty["errors"]), dirty
    assert any("will be done" in w for w in dirty["warnings"]), dirty
    assert any("by tomorrow" in w for w in dirty["warnings"]), dirty

    print("style.py self-test OK")
