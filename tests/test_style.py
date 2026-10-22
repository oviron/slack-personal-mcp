import pytest

from slack_personal_mcp.style import check, format_violations


def test_clean_text_passes():
    result = check("AFAIK no. One small backend fix is left, PR is up. The backend lead knows the status better.")
    assert result == {"errors": [], "warnings": []}


def test_dirty_text_is_flagged():
    result = check("Great question — I’ll leverage this and delve in. It will be done by tomorrow.")
    errors, warnings = " ".join(result["errors"]).lower(), " ".join(result["warnings"]).lower()
    for part in ("em dash", "right single quote", "great question", "leverag", "delve"):
        assert part in errors
    assert "will be done" in warnings and "by tomorrow" in warnings


@pytest.mark.parametrize(
    ("text", "flagged"),
    [
        ("We leverage it.", True),
        ("She leverages it.", True),
        ("We leveraged it.", True),
        ("We are leveraging it.", True),
        ("We utilize it.", True),
        ("She utilizes it.", True),
        ("We utilized it.", True),
        ("We are utilizing it.", True),
        ("We utilise it.", True),
        ("She utilises it.", True),
        ("We utilised it.", True),
        ("We are utilising it.", True),
        ("Let me know if you'd like a call.", True),
        ("Let me know if you would like a call.", True),
        ("Let me know if you want more.", True),
        ("Let me know if you have questions.", True),
        ("Let me know if you need help.", True),
        ("Feel free.", True),
        ("Feel free to ask.", True),
        ("Absolutely! Here's the result.", True),
        ("I hope this message finds you well.", True),
        ("I hope this email finds you well.", True),
        ("I hope that helps.", True),
        ("I'd be happy to help.", True),
        ("НаДеЮсЬ, это поможет.", True),
        ("Надеюсь это поможет.", True),
        ("Не стесняйтесь спрашивать.", True),
        ("Обращайтесь, если нужна помощь.", True),
        ("Отличный вопрос.", True),
        ("Буду рад помочь.", True),
        ("Буду рада помочь.", True),
        ("The leverager is running.", False),
        ("This is leverageable.", False),
        ("It was deleveraged.", False),
        ("The identifier is leveragingly.", False),
        ("The identifier is utilize_extra.", False),
        ("The identifier is utilizes2.", False),
        ("The utility is utilitarian.", False),
        ("Absolutely necessary.", False),
        ("I hope the build passes.", False),
        ("You are certainly correct.", False),
        ("I'd like to know if you want a call.", False),
        ("Это предотличный вопросник.", False),
        ("Буду радовать команду.", False),
        ("Я понадеюсь, это поможет.", False),
    ],
)
def test_slop_phrases(text, flagged):
    assert bool(check(text)["errors"]) is flagged


@pytest.mark.parametrize(
    ("text", "flagged"),
    [
        ("I'll send it.", True),
        ("I will fix it.", True),
        ("It will be ready.", True),
        ("By tomorrow.", True),
        ("By Sunday.", True),
        ("I guarantee it.", True),
        ("I promise.", True),
        ("I will upload it today.", True),
        ("I will upload it tomorrow.", True),
        ("I'll upload it tonight.", True),
        ("I will review it next week.", True),
        ("I will review it Monday.", True),
        ("I will upload `report` tomorrow.", True),
        ("I will reply in 1 minute.", True),
        ("I will reply in 2 minutes.", True),
        ("We will respond in 1 hour.", True),
        ("We'll respond in 2 hours.", True),
        ("It will be ready in 1 day.", True),
        ("It will be ready in 2 days.", True),
        ("I promise to reply in 1 week.", True),
        ("I promise to reply in 2 weeks.", True),
        ("In 2 hours I will reply.", True),
        ("I have no blockers and I will upload it tomorrow.", True),
        ("It's not a problem, I will upload it tomorrow.", True),
        ("I won't promise, but I will upload it tomorrow.", True),
        ("I won't promise but I will upload it tomorrow.", True),
        ("The job failed, but I will reply in 1 hour.", True),
        ("Сделаю к завтра.", True),
        ("Сделаю это завтра.", True),
        ("Я сделаю до конца дня.", True),
        ("Я сделаю это сегодня.", True),
        ("Гарантирую результат.", True),
        ("Точно будет готово.", True),
        ("The job failed in 2 hours.", False),
        ("The job took 1 hour.", False),
        ("It finished in 1 day.", False),
        ("It failed 2 minutes ago.", False),
        ("The report says it failed in 1 week.", False),
        ("The job usually runs in 2 hours.", False),
        ("The job completed in 2 hours and I will review it.", False),
        ("The meeting starts in 1 hour.", False),
        ("In 1 minute.", False),
        ("In 2 weeks.", False),
        ("I will explain why the job failed in 2 hours.", False),
        ("I will explain why it took in 2 hours.", False),
        ("I will explain what happened in 2 hours ago.", False),
        ("I won't promise.", False),
        ("I will not promise.", False),
        ("I do not promise.", False),
        ("I don't guarantee it.", False),
        ("I cannot guarantee it by tomorrow.", False),
        ("I won't send it by tomorrow.", False),
        ("I will not deliver it today.", False),
        ("I won't promise it will be ready in 1 day.", False),
        ("In 2 hours I will not reply.", False),
        ("In 2 hours I won't guarantee anything.", False),
        ("I will never reply in 1 hour.", False),
        ("I'll not reply in 1 hour.", False),
        ("No guarantee.", False),
        ("It is not guaranteed.", False),
        ("Я уже сделал это сегодня.", False),
        ("Не сделаю это сегодня.", False),
        ("Не гарантирую результат.", False),
        ("Точно не будет готово.", False),
        ("Не обещаю.", False),
        ("Я перегарантирую это.", False),
    ],
)
def test_promises(text, flagged):
    assert bool(check(text)["warnings"]) is flagged


@pytest.mark.parametrize("unit", ["minute", "minutes", "hour", "hours", "day", "days", "week", "weeks"])
def test_promise_intervals_have_their_own_diagnostic(unit):
    fragment = f"in 1 {unit}"
    assert any(fragment in warning for warning in check(f"We will reply {fragment}.")["warnings"])


@pytest.mark.parametrize(
    "text",
    [
        "Run `a — b` in code.",
        "`Great question — I'll finish by tomorrow`",
        "`Надеюсь, это поможет — сделаю сегодня`",
        "```\nGreat question — I'll finish by tomorrow\n```",
        "```python\nGreat question — I'll finish by tomorrow\n```",
        "Before ```Great question — I'll finish by tomorrow``` after.",
        "```\nGreat question — I'll finish by tomorrow",
        "See https://x.com/a…b",
        "See http://example.test/leveraged/guarantee/«x»",
        "See HTTPS://example.test/utilized/definitely",
        "<https://example.test/a…b|label>",
        "<mailto:test@example.test|label>",
        "<@U123> and <#C123|general> and <!here>",
        "> Great question — I'll send by tomorrow",
        "  > Great question — I'll send by tomorrow",
        ">> Great question — I'll send by tomorrow",
        "First.\n> Great question — I'll send by tomorrow\nDone.",
        "First.\r\n> Great question — I'll send by tomorrow\r\nDone.",
        "Great `ignored` question.",
        "`I will` upload tomorrow.",
    ],
)
def test_ignored_regions(text):
    assert check(text) == {"errors": [], "warnings": []}


@pytest.mark.parametrize("character", ["—", "–", "…", "‘", "’", "“", "”", "«", "»", "„", "\u00a0", "\u202f", "•"])
def test_typography_stays_strict_in_russian(character):
    assert check(f"Текст{character}пример")["errors"]
    assert check(f"`Текст{character}пример`") == {"errors": [], "warnings": []}


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("`leverage` Great question.", "Great question"),
        ("```\nGreat question\n```\nleverage", "leverage"),
        ("https://example.test/leverage leverage", "leverage"),
        ("<https://example.test|leverage> leverage", "leverage"),
        ("> Great question\nleverage", "leverage"),
        ("x > Great question", "Great question"),
        ("`Great question", "Great question"),
        ("Текст — пример.", "—"),
        ("Текст «пример».", "«"),
        ("İ `delve` НАДЕЮСЬ, это поможет", "НАДЕЮСЬ, это поможет"),
    ],
)
def test_unmasked_errors_keep_original_positions(text, fragment):
    start = text.rindex(fragment)
    assert any(
        f"at [{start}:{start + len(fragment)}]" in error and repr(fragment) in error for error in check(text)["errors"]
    )


@pytest.mark.parametrize(
    ("text", "bucket", "spans"),
    [
        ("`—` — —", "errors", [(4, 5), (6, 7)]),
        ("`delve` delve delve", "errors", [(8, 13), (14, 19)]),
        ("> I promise\nI promise. I promise.", "warnings", [(12, 21), (23, 32)]),
    ],
)
def test_reports_each_unmasked_occurrence(text, bucket, spans):
    violations = check(text)[bucket]
    assert len(violations) == len(spans)
    for violation, (start, end) in zip(violations, spans, strict=True):
        assert f"at [{start}:{end}]" in violation
        assert repr(text[start:end]) in violation


@pytest.mark.parametrize("allow_promise", [False, True])
def test_format_violations_keeps_tool_contract(allow_promise):
    result = check("Great question. I promise.")
    formatted = format_violations(result, allow_promise)
    assert "ERRORS (must fix):" in formatted
    assert "WARNINGS (resend with allow_promise=true if intended):" in formatted
    assert all(violation in formatted for violations in result.values() for violation in violations)
    assert format_violations(check("Plain text."), allow_promise) == ""


@pytest.mark.parametrize("text", ["<https://example.test/a|Great question — I'll send by tomorrow>", "a < b — c > d"])
def test_link_labels_and_angle_brackets_are_checked(text):
    assert check(text)["errors"]


@pytest.mark.parametrize(
    "text",
    ["Посмотрю завтра", "Я пришлю к пятнице", "Будет готово завтра", "Сделаем через 2 часа", "Обещаю"],
)
def test_russian_promises(text):
    assert check(text)["warnings"]


@pytest.mark.parametrize("text", ["До завтра!", "Встреча в пятницу", "Вчера сделал", "Не обещаю, что завтра"])
def test_russian_non_promises(text):
    assert not check(text)["warnings"]
