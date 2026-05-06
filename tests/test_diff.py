from __future__ import annotations

from pathlib import Path
from typing import Callable

from reviewer.diff import build_pr_diff, list_files_at_ref


def test_build_pr_diff_lists_changed_files(repo: Path, commit: Callable) -> None:
    base = commit({"a.py": "x = 1\n"}, "init")
    head = commit(
        {"a.py": "x = 2\ny = 3\n", "b.py": "print()\n"}, "edit a, add b"
    )
    diff, commits = build_pr_diff(base, head, repo)

    paths = {f.path for f in diff.files}
    assert paths == {"a.py", "b.py"}
    assert len(commits) == 1
    assert commits[0].subject == "edit a, add b"
    assert "a.py" in commits[0].files
    assert "b.py" in commits[0].files


def test_build_pr_diff_handles_multiple_commits(
    repo: Path, commit: Callable
) -> None:
    base = commit({"a.py": "x = 1\n"}, "init")
    commit({"a.py": "x = 1\ny = 2\n"}, "second")
    head = commit({"a.py": "x = 1\ny = 2\nz = 3\n"}, "third")
    diff, commits = build_pr_diff(base, head, repo)
    assert len(commits) == 2
    assert {c.subject for c in commits} == {"second", "third"}
    assert any(f.path == "a.py" for f in diff.files)


def test_build_pr_diff_captures_additions_deletions(
    repo: Path, commit: Callable
) -> None:
    base = commit({"a.py": "x = 1\n"}, "init")
    head = commit({"a.py": "x = 1\ny = 2\nz = 3\n"}, "add lines")
    diff, _ = build_pr_diff(base, head, repo)
    file = next(f for f in diff.files if f.path == "a.py")
    assert file.additions == 2
    assert file.deletions == 0


def test_list_files_at_ref_includes_empty_files(
    repo: Path, commit: Callable
) -> None:
    ref = commit({"AGENTS.md": "- one rule\n", "CLAUDE.md": ""}, "init")

    found = list_files_at_ref(repo, ref)

    assert "AGENTS.md" in found
    assert "CLAUDE.md" in found  # zero-byte file must still appear


def test_list_files_at_ref_includes_nested_files(
    repo: Path, commit: Callable
) -> None:
    ref = commit(
        {
            "AGENTS.md": "root\n",
            "packages/api/AGENTS.md": "nested\n",
            "packages/api/app.py": "x = 1\n",
        },
        "init",
    )

    found = list_files_at_ref(repo, ref)

    assert "AGENTS.md" in found
    assert "packages/api/AGENTS.md" in found


def test_build_pr_diff_handles_deletion(repo: Path, commit: Callable) -> None:
    base = commit({"a.py": "x = 1\n", "b.py": "y = 2\n"}, "init")
    # Remove b.py via filesystem op + git add -A in helper
    (repo / "b.py").unlink()
    head = commit({"a.py": "x = 1\n"}, "remove b")
    diff, _ = build_pr_diff(base, head, repo)
    statuses = {f.path: f.status for f in diff.files}
    assert statuses.get("b.py") == "D"


def test_build_pr_diff_handles_rename_with_spaces(repo: Path, commit: Callable) -> None:
    base = commit({"old name.py": "x = 1\n"}, "init")
    (repo / "old name.py").rename(repo / "new name.py")
    (repo / "new name.py").write_text("x = 1\ny = 2\n")
    head = commit({}, "rename with spaces")

    diff, commits = build_pr_diff(base, head, repo)

    file = next(f for f in diff.files if f.path == "new name.py")
    assert file.status == "R"
    assert file.additions == 1
    assert commits[0].files == ["new name.py"]
