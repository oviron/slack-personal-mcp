from slack_personal_mcp.style import check


def test_clean_text_passes():
    result = check("AFAIK no. One small backend fix is left, PR is up. The backend lead knows the status better.")
    assert result == {"errors": [], "warnings": []}


def test_dirty_text_is_flagged():
    result = check("Great question — I’ll leverage this and delve in. It will be done by tomorrow.")
    errors, warnings = " ".join(result["errors"]), " ".join(result["warnings"])
    for part in ("em dash", "right single quote", "great question", "leverag", "delve"):
        assert part in errors
    assert "will be done" in warnings and "by tomorrow" in warnings
