from __future__ import annotations

from reviewer.checks import Finding
from reviewer.reporters.github import (
    STICKY_MARKER,
    _is_updateable_sticky_comment,
    emit_annotations,
    escape_command_data,
)


def test_emit_annotations_escapes_workflow_command_fields(capsys) -> None:
    emit_annotations(
        [
            Finding(
                rule_id="RULE_001",
                severity="high",
                message="bad % value\nnext",
                path="src/a:b,c.py",
                line=12,
            )
        ]
    )

    out = capsys.readouterr().out
    assert "file=src/a%3Ab%2Cc.py" in out
    assert "bad %25 value%0Anext" in out


def test_emit_annotations_skips_diagnostics(capsys) -> None:
    emit_annotations(
        [
            Finding(
                rule_id="INSTRUCTIONS_COMPLIANCE_001",
                severity="low",
                message="LLM token usage",
                kind="diagnostic",
            ),
            Finding(rule_id="RULE_001", severity="low", message="real finding"),
        ]
    )

    out = capsys.readouterr().out
    assert "LLM token usage" not in out
    assert "RULE_001: real finding" in out


def test_escape_command_data_escapes_warning_payloads() -> None:
    assert escape_command_data("bad %\r\nnext") == "bad %25%0D%0Anext"


def test_sticky_comment_only_updates_bot_comments() -> None:
    user_comment = {
        "id": 1,
        "body": f"report\n{STICKY_MARKER}",
        "user": {"login": "teammate", "type": "User"},
    }
    bot_comment = {
        "id": 2,
        "body": f"report\n{STICKY_MARKER}",
        "user": {"login": "github-actions[bot]", "type": "Bot"},
    }

    assert not _is_updateable_sticky_comment(user_comment)
    assert _is_updateable_sticky_comment(bot_comment)
