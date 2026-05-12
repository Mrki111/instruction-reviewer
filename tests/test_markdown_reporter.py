from __future__ import annotations

from reviewer.checks import Finding
from reviewer.diff import Commit, Diff
from reviewer.instructions import InstructionFile
from reviewer.reporters.markdown import render_report
from reviewer.rules import Rule


def _diff() -> Diff:
    return Diff(base="b", head="h", merge_base="b", files=[], raw="")


def _llm_rule(enabled: bool = True) -> Rule:
    return Rule(
        id="INSTRUCTIONS_COMPLIANCE_001",
        enabled=enabled,
        severity="medium",
        description="",
        config={},
    )


def _instructions() -> list[InstructionFile]:
    return [InstructionFile(path="AGENTS.md", content="- rule")]


def test_llm_status_omitted_when_rules_not_passed() -> None:
    report = render_report([], _diff(), [], _instructions(), "high")
    assert "LLM compliance:" not in report


def test_llm_status_disabled_when_rule_disabled() -> None:
    report = render_report(
        [], _diff(), [], _instructions(), "high", rules=[_llm_rule(enabled=False)]
    )
    assert "LLM compliance: disabled" in report


def test_llm_status_not_applicable_when_no_instructions() -> None:
    report = render_report(
        [], _diff(), [], [], "high", rules=[_llm_rule()]
    )
    assert "not applicable" in report


def test_llm_status_ran_clean_when_enabled_with_no_findings() -> None:
    report = render_report(
        [], _diff(), [], _instructions(), "high", rules=[_llm_rule()]
    )
    assert "ran (no violations reported)" in report


def test_llm_status_skipped_when_only_skip_finding() -> None:
    skip = Finding(
        rule_id="INSTRUCTIONS_COMPLIANCE_001",
        severity="low",
        message="ANTHROPIC_API_KEY is not set",
        kind="skipped",
    )
    report = render_report(
        [skip], _diff(), [], _instructions(), "high", rules=[_llm_rule()]
    )
    assert "LLM compliance: skipped" in report
    assert "ANTHROPIC_API_KEY" in report


def test_llm_status_violation_count_when_violations_present() -> None:
    violation = Finding(
        rule_id="INSTRUCTIONS_COMPLIANCE_001",
        severity="medium",
        message="snake_case violated",
    )
    report = render_report(
        [violation], _diff(), [], _instructions(), "high", rules=[_llm_rule()]
    )
    assert "ran (1 violation(s) reported)" in report


def test_llm_tokens_line_shows_cache_hit_rate() -> None:
    diag = Finding(
        rule_id="INSTRUCTIONS_COMPLIANCE_001",
        severity="low",
        message="LLM token usage",
        kind="diagnostic",
        metadata={
            "usage": {
                "input_tokens": 200,
                "output_tokens": 40,
                "cache_creation_input_tokens": 100,
                "cache_read_input_tokens": 1600,
            },
            "model": "claude-sonnet-4-6",
        },
    )
    report = render_report(
        [diag], _diff(), [], _instructions(), "high", rules=[_llm_rule()]
    )
    # 200 fresh + 100 cache-create + 1600 cache-read = 1,900 total in
    # cache hit rate over cacheable portion = 1600 / (1600+100) = 94%
    assert "LLM tokens: 1,900 in (1,600 cached, 94% hit rate) / 40 out" in report


def test_llm_tokens_line_omitted_when_no_usage() -> None:
    report = render_report(
        [], _diff(), [], _instructions(), "high", rules=[_llm_rule()]
    )
    assert "LLM tokens:" not in report


def test_diagnostics_excluded_from_severity_table() -> None:
    diag = Finding(
        rule_id="INSTRUCTIONS_COMPLIANCE_001",
        severity="low",
        message="LLM token usage",
        kind="diagnostic",
        metadata={"usage": {"input_tokens": 100, "output_tokens": 5}},
    )
    report = render_report(
        [diag], _diff(), [], _instructions(), "high", rules=[_llm_rule()]
    )
    assert "Violations: 0" in report
    assert "| low | 0 |" in report  # no row inflation
    assert "## Findings" not in report  # no listings


def test_commit_subject_is_html_escaped_in_report() -> None:
    commit = Commit(
        sha="a" * 40,
        author_name="t",
        author_email="t@x",
        subject="oops </details><script>alert(1)</script>",
        body="",
        files=[],
    )
    report = render_report(
        [], _diff(), [commit], _instructions(), "high", rules=[_llm_rule()]
    )
    assert "</details><script>" not in report
    assert "&lt;/details&gt;&lt;script&gt;" in report


def test_findings_count_excludes_skip_markers() -> None:
    skip = Finding(
        rule_id="INSTRUCTIONS_COMPLIANCE_001",
        severity="low",
        message="skipped",
        kind="skipped",
    )
    violation = Finding(
        rule_id="INSTRUCTIONS_COMPLIANCE_001",
        severity="medium",
        message="real violation",
    )
    report = render_report(
        [skip, violation], _diff(), [], _instructions(), "high",
        rules=[_llm_rule()],
    )
    # Skip markers go to the LLM status line, not the headline count.
    assert "Violations: 1" in report
