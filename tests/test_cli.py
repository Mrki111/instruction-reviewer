from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from reviewer.cli import (
    _emit_notice,
    _emit_warning,
    _load_check_extensions,
    _read_user_rules_from_base,
    _resolve_instructions,
    _resolve_instructions_at_ref,
    _resolve_user_rules,
    main,
)
from reviewer.checks import CHECKS, Finding, register
from reviewer.diff import Diff, FileChange, GitError
from reviewer.instructions import InstructionFile
from reviewer.rules import Rule


@register("TEST_FIRES_001")
def _test_fires(rule, diff, commits, instructions):
    threshold = int(rule.config.get("max_lines_changed", 1))
    total = sum(f.additions + f.deletions for f in diff.files)
    if total > threshold:
        return [Finding(rule.id, rule.severity, f"test rule fired ({total} lines)")]
    return []


def test_default_instruction_discovery_is_recursive(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("root\n")
    nested = tmp_path / "packages" / "api" / "AGENTS.md"
    nested.parent.mkdir(parents=True)
    nested.write_text("nested\n")

    found = _resolve_instructions(tmp_path, "")
    assert [f.path for f in found] == ["AGENTS.md", "packages/api/AGENTS.md"]
    assert found[0].content == "root\n"
    assert found[1].content == "nested\n"


def test_instruction_discovery_ignores_generated_dirs(tmp_path: Path) -> None:
    ignored = tmp_path / "node_modules" / "pkg" / "AGENTS.md"
    ignored.parent.mkdir(parents=True)
    ignored.write_text("ignored\n")

    assert _resolve_instructions(tmp_path, "") == []


def test_instruction_loading_uses_requested_git_ref(
    repo: Path, commit: Callable
) -> None:
    base = commit(
        {
            "AGENTS.md": "base rules\n",
            "packages/api/AGENTS.md": "api base rules\n",
            "foo.py": "x = 1\n",
        },
        "base",
    )
    commit({"AGENTS.md": "weakened rules\n", "foo.py": "x = 2\n"}, "head")

    found = _resolve_instructions_at_ref(repo, base, "")

    assert [f.path for f in found] == ["AGENTS.md", "packages/api/AGENTS.md"]
    assert found[0].content == "base rules\n"
    assert found[1].content == "api base rules\n"


def test_main_reports_low_finding_when_llm_rule_enabled_without_key(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    default_rules = tmp_path / "rules.json"
    default_rules.write_text(
        json.dumps(
            {
                "rules": [
                    {
                        "id": "INSTRUCTIONS_COMPLIANCE_001",
                        "enabled": True,
                        "severity": "medium",
                    }
                ]
            }
        )
    )
    monkeypatch.setattr(
        "reviewer.cli.build_pr_diff",
        lambda base, head, repo: (
            Diff(
                base=base,
                head=head,
                merge_base=base,
                files=[FileChange("foo.py", "M", 1, 0)],
                raw=(
                    "diff --git a/foo.py b/foo.py\n"
                    "--- a/foo.py\n"
                    "+++ b/foo.py\n"
                    "@@ -0,0 +1,1 @@\n"
                    "+x = 1\n"
                ),
            ),
            [],
        ),
    )
    monkeypatch.setattr(
        "reviewer.cli._resolve_instructions_at_ref",
        lambda *args: [InstructionFile("AGENTS.md", "- Use snake_case.")],
    )

    exit_code = main(
        [
            "--base-ref",
            "base",
            "--head-ref",
            "head",
            "--repo-root",
            str(tmp_path),
            "--default-rules",
            str(default_rules),
            "--report-path",
            str(tmp_path / "report.md"),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "ANTHROPIC_API_KEY is not set" not in captured.err
    assert "ANTHROPIC_API_KEY is not set" in (tmp_path / "report.md").read_text()


def test_main_loads_instructions_from_base_ref_not_merge_base(
    tmp_path: Path, monkeypatch
) -> None:
    default_rules = tmp_path / "rules.json"
    default_rules.write_text(json.dumps({"rules": []}))
    seen_refs: list[str] = []

    monkeypatch.setattr(
        "reviewer.cli.build_pr_diff",
        lambda base, head, repo: (
            Diff(base=base, head=head, merge_base="merge-base", files=[], raw=""),
            [],
        ),
    )

    def resolve(repo_root, ref, raw):
        seen_refs.append(ref)
        return []

    monkeypatch.setattr("reviewer.cli._resolve_instructions_at_ref", resolve)

    exit_code = main(
        [
            "--base-ref",
            "base-sha",
            "--head-ref",
            "head-sha",
            "--repo-root",
            str(tmp_path),
            "--default-rules",
            str(default_rules),
            "--report-path",
            str(tmp_path / "report.md"),
        ]
    )

    assert exit_code == 0
    assert seen_refs == ["base-sha"]


def test_main_loads_user_rules_from_base_ref_not_worktree(
    tmp_path: Path, monkeypatch
) -> None:
    default_rules = tmp_path / "rules.json"
    default_rules.write_text(
        json.dumps(
            {
                "rules": [
                    {
                        "id": "TEST_FIRES_001",
                        "enabled": True,
                        "severity": "medium",
                        "max_lines_changed": 1000,
                    }
                ]
            }
        )
    )
    head_rules = tmp_path / ".github" / "instruction-rules.json"
    head_rules.parent.mkdir()
    head_rules.write_text(
        json.dumps({"rules": [{"id": "TEST_FIRES_001", "enabled": False}]})
    )

    def read_file_at_ref(repo_root, ref, path):
        assert ref == "base-sha"
        assert path == ".github/instruction-rules.json"
        return json.dumps({"rules": [{"id": "TEST_FIRES_001", "max_lines_changed": 1}]})

    monkeypatch.setattr("reviewer.cli.read_file_at_ref", read_file_at_ref)
    monkeypatch.setattr(
        "reviewer.cli.build_pr_diff",
        lambda base, head, repo: (
            Diff(
                base=base,
                head=head,
                merge_base=base,
                files=[FileChange("foo.py", "M", 2, 0)],
                raw="",
            ),
            [],
        ),
    )
    monkeypatch.setattr("reviewer.cli._resolve_instructions_at_ref", lambda *args: [])

    exit_code = main(
        [
            "--base-ref",
            "base-sha",
            "--head-ref",
            "head-sha",
            "--repo-root",
            str(tmp_path),
            "--default-rules",
            str(default_rules),
            "--fail-on",
            "medium",
            "--report-path",
            str(tmp_path / "report.md"),
        ]
    )

    assert exit_code == 1
    report = (tmp_path / "report.md").read_text()
    assert "TEST_FIRES_001" in report


def test_main_defaults_to_medium_fail_threshold(
    tmp_path: Path, monkeypatch
) -> None:
    default_rules = tmp_path / "rules.json"
    default_rules.write_text(
        json.dumps(
            {
                "rules": [
                    {
                        "id": "TEST_FIRES_001",
                        "enabled": True,
                        "severity": "medium",
                        "max_lines_changed": 1,
                    }
                ]
            }
        )
    )
    monkeypatch.setattr(
        "reviewer.cli.build_pr_diff",
        lambda base, head, repo: (
            Diff(
                base=base,
                head=head,
                merge_base=base,
                files=[FileChange("foo.py", "M", 2, 0)],
                raw="",
            ),
            [],
        ),
    )
    monkeypatch.setattr("reviewer.cli._resolve_instructions_at_ref", lambda *args: [])

    exit_code = main(
        [
            "--base-ref",
            "base-sha",
            "--head-ref",
            "head-sha",
            "--repo-root",
            str(tmp_path),
            "--default-rules",
            str(default_rules),
            "--report-path",
            str(tmp_path / "report.md"),
        ]
    )

    assert exit_code == 1


def test_user_rules_auto_discovered(tmp_path: Path) -> None:
    rules_path = tmp_path / ".github" / "instruction-rules.json"
    rules_path.parent.mkdir()
    rules_path.write_text(json.dumps({"rules": []}))

    assert _resolve_user_rules(tmp_path, "") == rules_path


def test_explicit_user_rules_are_repo_relative(tmp_path: Path) -> None:
    assert _resolve_user_rules(tmp_path, "config/rules.json") == (
        tmp_path / "config" / "rules.json"
    )


def test_in_repo_absolute_rules_path_is_loaded_from_base(
    tmp_path: Path, monkeypatch
) -> None:
    rules_path = tmp_path / ".github" / "instruction-rules.json"

    def read_file_at_ref(repo_root, ref, path):
        assert repo_root == tmp_path
        assert ref == "base-sha"
        assert path == ".github/instruction-rules.json"
        return '{"rules": []}'

    monkeypatch.setattr("reviewer.cli.read_file_at_ref", read_file_at_ref)

    text, label = _read_user_rules_from_base(tmp_path, "base-sha", str(rules_path))

    assert text == '{"rules": []}'
    assert label == "base-sha:.github/instruction-rules.json"


def test_load_check_extension_from_file(tmp_path: Path) -> None:
    extension = tmp_path / "checks_ext.py"
    extension.write_text(
        "from reviewer.checks import Finding, register\n\n"
        "@register('TEAM_EXT')\n"
        "def check_team_ext(rule, diff, commits, instructions):\n"
        "    return [Finding(rule.id, rule.severity, 'custom finding')]\n"
    )
    try:
        _load_check_extensions(tmp_path, "checks_ext.py")
        assert "TEAM_EXT" in CHECKS
    finally:
        CHECKS.pop("TEAM_EXT", None)


def test_load_check_extension_from_base_ref_not_worktree(
    repo: Path, commit: Callable
) -> None:
    base = commit(
        {
            ".github/instruction_checks.py": (
                "from reviewer.checks import Finding, register\n\n"
                "@register('TEAM_EXT_BASE')\n"
                "def check_team_ext(rule, diff, commits, instructions):\n"
                "    return [Finding(rule.id, rule.severity, 'base finding')]\n"
            ),
            "foo.py": "x = 1\n",
        },
        "base",
    )
    commit(
        {
            ".github/instruction_checks.py": (
                "from reviewer.checks import Finding, register\n\n"
                "@register('TEAM_EXT_HEAD')\n"
                "def check_team_ext(rule, diff, commits, instructions):\n"
                "    return [Finding(rule.id, rule.severity, 'head finding')]\n"
            ),
            "foo.py": "x = 2\n",
        },
        "head",
    )

    try:
        _load_check_extensions(repo, ".github/instruction_checks.py", base)

        rule = Rule(
            id="TEAM_EXT_BASE",
            enabled=True,
            severity="medium",
            description="",
            config={},
        )
        finding = next(
            iter(CHECKS["TEAM_EXT_BASE"](rule, Diff("b", "h", "b"), [], []))
        )

        assert isinstance(finding, Finding)
        assert finding.message == "base finding"
        assert "TEAM_EXT_HEAD" not in CHECKS
    finally:
        CHECKS.pop("TEAM_EXT_BASE", None)
        CHECKS.pop("TEAM_EXT_HEAD", None)


def test_main_rejects_checks_module_on_pull_request_target(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    default_rules = tmp_path / "rules.json"
    default_rules.write_text(json.dumps({"rules": []}))
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request_target")
    monkeypatch.setattr(
        "reviewer.cli.read_file_at_ref",
        lambda *args: (_ for _ in ()).throw(GitError("not found")),
    )

    exit_code = main(
        [
            "--base-ref",
            "base-sha",
            "--head-ref",
            "head-sha",
            "--repo-root",
            str(tmp_path),
            "--default-rules",
            str(default_rules),
            "--checks-module",
            ".github/checks.py",
            "--report-path",
            str(tmp_path / "report.md"),
        ]
    )

    assert exit_code == 2
    assert "--checks-module is disabled on pull_request_target" in capsys.readouterr().err


def test_github_warning_and_notice_escape_command_data(capsys) -> None:
    _emit_warning("bad % value\n::error::injected", github=True)
    _emit_notice("notice % value\nnext", github=True)

    out = capsys.readouterr().out
    assert "::warning::bad %25 value%0A::error::injected" in out
    assert "::notice::notice %25 value%0Anext" in out
