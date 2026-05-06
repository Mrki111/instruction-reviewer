from __future__ import annotations

from pathlib import Path


def _action_text() -> str:
    return (Path(__file__).parents[1] / "action.yml").read_text()


def test_action_default_fail_threshold_matches_enforcing_quick_start() -> None:
    text = _action_text()

    assert "  fail-on:\n" in text
    assert "    default: medium\n" in text


def test_action_does_not_interpolate_inputs_inside_run_script() -> None:
    text = _action_text()
    run_reviewer_section = text.split("    - name: Run reviewer", 1)[1]
    run_script = run_reviewer_section.split("      run: |", 1)[1]

    assert "${{ inputs." not in run_script
    assert '--instructions "$INPUT_INSTRUCTIONS"' in run_script
    assert '--rules "$INPUT_RULES"' in run_script
    assert '--checks-module "$INPUT_CHECKS_MODULE"' in run_script
    assert '--fail-on "$INPUT_FAIL_ON"' in run_script
    assert '--comment-on-pr "$INPUT_COMMENT_ON_PR"' in run_script
