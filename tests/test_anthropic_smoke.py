"""Live smoke test against the Anthropic API.

Skipped unless ANTHROPIC_API_KEY is set, so it only runs in the dedicated
smoke workflow. Exists to detect SDK/API contract drift that the mocked
unit tests cannot catch.
"""
from __future__ import annotations

import os

import pytest

from reviewer.diff import Diff, FileChange
from reviewer.instructions import InstructionFile
from reviewer.llm_check import check_instructions_compliance
from reviewer.rules import Rule

pytestmark = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set (skip outside the smoke workflow)",
)


def test_live_compliance_call_succeeds() -> None:
    rule = Rule(
        id="INSTRUCTIONS_COMPLIANCE_001",
        enabled=True,
        severity="medium",
        description="",
        # fail_open=False — any SDK/API/JSON-parse failure raises rather than
        # being swallowed as a low-severity finding. Loud failure is the point.
        config={"fail_open": False, "timeout_seconds": 60.0, "max_retries": 1},
    )
    diff = Diff(
        base="b",
        head="h",
        merge_base="b",
        files=[FileChange("foo.py", "M", 1, 0)],
        raw=(
            "diff --git a/foo.py b/foo.py\n"
            "--- a/foo.py\n"
            "+++ b/foo.py\n"
            "@@ -0,0 +1,1 @@\n"
            "+badName = 1\n"
        ),
    )
    instructions = [
        InstructionFile(
            path="AGENTS.md",
            content="- Always use snake_case names for variables.",
        )
    ]

    findings = check_instructions_compliance(rule, diff, [], instructions)

    # Don't assert the model finds the violation — model behavior drifts.
    # Asserting we got a list back proves the SDK call shape, the
    # output_config/json_schema contract, and our parser all still work.
    assert isinstance(findings, list)

    # The token-usage diagnostic must be present: it proves response.usage
    # still exposes input_tokens/output_tokens with the expected types. If
    # the SDK ever renames these fields or changes their types, the
    # diagnostic silently disappears, which would mask the drift.
    diagnostics = [f for f in findings if f.kind == "diagnostic"]
    assert len(diagnostics) == 1
    usage = diagnostics[0].metadata["usage"]
    assert usage.get("input_tokens", 0) > 0
    assert "output_tokens" in usage
