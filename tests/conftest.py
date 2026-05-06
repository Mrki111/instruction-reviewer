from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

import pytest


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "commit.gpgsign", "false")
    return tmp_path


@pytest.fixture
def commit(repo: Path) -> Callable[..., str]:
    def _commit(files: dict[str, str], message: str) -> str:
        for path, content in files.items():
            full = repo / path
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", message)
        return _git(repo, "rev-parse", "HEAD").strip()

    return _commit
