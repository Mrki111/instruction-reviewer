from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class FileChange:
    path: str
    status: str  # one of A, M, D, R, C, T, U
    additions: int = 0
    deletions: int = 0


@dataclass
class Diff:
    base: str
    head: str
    merge_base: str
    files: list[FileChange] = field(default_factory=list)
    raw: str = ""


@dataclass
class Commit:
    sha: str
    author_name: str
    author_email: str
    subject: str
    body: str
    files: list[str] = field(default_factory=list)


class GitError(RuntimeError):
    pass


GIT_TIMEOUT_SECONDS = 60


def _git(repo: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as e:
        raise GitError(
            f"git {' '.join(args)} timed out after {GIT_TIMEOUT_SECONDS}s"
        ) from e
    if result.returncode != 0:
        raise GitError(
            f"git {' '.join(args)} failed (exit {result.returncode}): "
            f"{result.stderr.strip()}"
        )
    return result.stdout


def _git_bytes(repo: Path, *args: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as e:
        raise GitError(
            f"git {' '.join(args)} timed out after {GIT_TIMEOUT_SECONDS}s"
        ) from e
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise GitError(
            f"git {' '.join(args)} failed (exit {result.returncode}): {stderr}"
        )
    return result.stdout


def _decode_path(value: bytes) -> str:
    return value.decode("utf-8", errors="surrogateescape")


def _resolve_merge_base(repo: Path, base: str, head: str) -> str:
    try:
        return _git(repo, "merge-base", base, head).strip()
    except GitError:
        # Fall back to the literal base ref if merge-base can't be computed
        # (e.g., shallow clone, unrelated history).
        return _git(repo, "rev-parse", base).strip()


def _parse_name_status_z(data: bytes) -> dict[str, str]:
    """Returns {path: status_letter} keyed by the post-rename (new) path."""
    result: dict[str, str] = {}
    fields = [part for part in data.split(b"\0") if part]
    i = 0
    while i < len(fields):
        status = _decode_path(fields[i])
        i += 1
        if not status:
            continue
        status_letter = status[0]  # 'R100' -> 'R'
        if status_letter in {"R", "C"}:
            if i + 1 >= len(fields):
                break
            i += 1  # old path
            path = _decode_path(fields[i])
            i += 1
        else:
            if i >= len(fields):
                break
            path = _decode_path(fields[i])
            i += 1
        result[path] = status_letter
    return result


def _parse_numstat_z(data: bytes) -> dict[str, tuple[int, int]]:
    """Returns {path: (additions, deletions)} keyed by the post-rename path.

    With ``-z``, rename/copy lines are encoded as
    ``ADD<TAB>DEL<TAB><NUL>old<NUL>new<NUL>``. For binary files ADD/DEL are
    ``-``.
    """
    result: dict[str, tuple[int, int]] = {}
    fields = data.split(b"\0")
    i = 0
    while i < len(fields):
        record = fields[i]
        i += 1
        if not record:
            continue
        parts = record.split(b"\t")
        if len(parts) < 3:
            continue
        adds = 0 if parts[0] == b"-" else int(parts[0])
        dels = 0 if parts[1] == b"-" else int(parts[1])
        if parts[2] == b"":
            if i + 1 >= len(fields):
                break
            i += 1  # old path
            path = _decode_path(fields[i])
            i += 1
        else:
            path = _decode_path(parts[2])
        result[path] = (adds, dels)
    return result


def get_diff(repo: Path, merge_base: str, head: str) -> Diff:
    raw = _git(repo, "diff", "--no-ext-diff", f"{merge_base}..{head}")
    name_status = _parse_name_status_z(
        _git_bytes(repo, "diff", "--no-ext-diff", "--name-status", "-z", f"{merge_base}..{head}")
    )
    numstat = _parse_numstat_z(
        _git_bytes(repo, "diff", "--no-ext-diff", "--numstat", "-z", f"{merge_base}..{head}")
    )

    files: list[FileChange] = []
    for path, status in name_status.items():
        adds, dels = numstat.get(path, (0, 0))
        files.append(
            FileChange(path=path, status=status, additions=adds, deletions=dels)
        )

    return Diff(base=merge_base, head=head, merge_base=merge_base, files=files, raw=raw)


def get_commits(repo: Path, merge_base: str, head: str) -> list[Commit]:
    fmt = "%H%x1f%an%x1f%ae%x1f%s%x1f%b"
    out = _git(repo, "log", f"--pretty=format:{fmt}%x1e", f"{merge_base}..{head}")
    commits: list[Commit] = []
    for raw in out.split("\x1e"):
        raw = raw.strip("\n")
        if not raw:
            continue
        parts = raw.split("\x1f", 4)
        if len(parts) < 5:
            continue
        sha, an, ae, subject, body = parts
        files_raw = _git_bytes(repo, "show", "--pretty=format:", "--name-only", "-z", sha)
        files = [_decode_path(part) for part in files_raw.split(b"\0") if part]
        commits.append(
            Commit(
                sha=sha,
                author_name=an,
                author_email=ae,
                subject=subject,
                body=body,
                files=files,
            )
        )
    return commits


def list_files_at_ref(repo: Path, ref: str) -> list[str]:
    """List every tracked file at ``ref``. Filtering is the caller's job.

    Pathspecs are not accepted because git's pathspec language varies between
    plumbing commands: ``ls-tree`` does not honor ``**`` or ``:(glob)`` magic,
    and ``git grep -l -e ''`` silently drops empty files. The caller filters
    the returned list with their own glob semantics instead.
    """
    out = _git_bytes(repo, "ls-tree", "-rz", "--name-only", ref)
    return [_decode_path(part) for part in out.split(b"\0") if part]


def read_file_at_ref(repo: Path, ref: str, path: str) -> str:
    return _git(repo, "show", f"{ref}:{path}")


def build_pr_diff(
    base_ref: str, head_ref: str, repo_root: Path
) -> tuple[Diff, list[Commit]]:
    repo_root = Path(repo_root)
    if not (repo_root / ".git").exists() and not _is_inside_worktree(repo_root):
        raise GitError(f"{repo_root} is not a git repository.")
    merge_base = _resolve_merge_base(repo_root, base_ref, head_ref)
    diff = get_diff(repo_root, merge_base, head_ref)
    diff.base = base_ref  # report the user-facing ref, not the merge base
    commits = get_commits(repo_root, merge_base, head_ref)
    return diff, commits


def _is_inside_worktree(repo_root: Path) -> bool:
    try:
        return _git(repo_root, "rev-parse", "--is-inside-work-tree").strip() == "true"
    except GitError:
        return False
