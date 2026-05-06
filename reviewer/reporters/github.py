from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from typing import Iterable

from reviewer.checks import Finding
from reviewer.reporters.markdown import STICKY_MARKER

__all__ = [
    "STICKY_MARKER",
    "emit_annotations",
    "escape_command_data",
    "post_sticky_comment",
    "set_outputs",
    "write_step_summary",
]

GITHUB_API_TIMEOUT_SECONDS = 15


def write_step_summary(report: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(report)
        fh.write("\n")


def emit_annotations(findings: Iterable[Finding]) -> None:
    for f in findings:
        if f.kind == "diagnostic":
            continue
        level = "error" if f.severity == "high" else "warning"
        loc_parts: list[str] = []
        if f.path:
            loc_parts.append(f"file={_escape_command_property(f.path)}")
        if f.line:
            loc_parts.append(f"line={f.line}")
        loc = ",".join(loc_parts)
        prefix = f"::{level} {loc}::" if loc else f"::{level}::"
        message = _escape_command_data(f"{f.rule_id}: {f.message}")
        print(f"{prefix}{message}")


def set_outputs(**kwargs: object) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        for key, value in kwargs.items():
            value_str = str(value)
            if "\n" in value_str:
                # Multi-line outputs need a heredoc-style delimiter
                safe_key = re.sub(r"[^A-Za-z0-9_]", "_", key).upper()
                delim = f"__INSTR_REVIEWER_{safe_key}__"
                while delim in value_str:
                    delim += "_"
                fh.write(f"{key}<<{delim}\n{value_str}\n{delim}\n")
            else:
                fh.write(f"{key}={value_str}\n")


def post_sticky_comment(
    body: str, repo: str, pr_number: int, token: str
) -> None:
    """Update or create a single sticky PR comment identified by STICKY_MARKER."""
    if STICKY_MARKER not in body:
        body = f"{body}\n\n{STICKY_MARKER}\n"

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "instruction-reviewer",
    }

    existing_id = _find_sticky_comment(repo, pr_number, headers)

    payload = json.dumps({"body": body}).encode()
    if existing_id is not None:
        url = f"https://api.github.com/repos/{repo}/issues/comments/{existing_id}"
        req = urllib.request.Request(
            url,
            data=payload,
            method="PATCH",
            headers={**headers, "Content-Type": "application/json"},
        )
    else:
        url = f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments"
        req = urllib.request.Request(
            url,
            data=payload,
            method="POST",
            headers={**headers, "Content-Type": "application/json"},
        )
    try:
        with urllib.request.urlopen(req, timeout=GITHUB_API_TIMEOUT_SECONDS) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        print(
            "::warning::"
            + escape_command_data(
                f"Sticky comment request failed: {e.code} {detail}"
            ),
            file=sys.stderr,
        )


def _find_sticky_comment(
    repo: str, pr_number: int, headers: dict[str, str]
) -> int | None:
    base = f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments"
    page = 1
    while True:
        url = f"{base}?per_page=100&page={page}"
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=GITHUB_API_TIMEOUT_SECONDS) as resp:
                comments = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            print(
                "::warning::"
                + escape_command_data(
                    f"Failed to list PR comments: {e.code} {detail}"
                ),
                file=sys.stderr,
            )
            return None
        if not comments:
            return None
        for c in comments:
            if _is_updateable_sticky_comment(c):
                return int(c["id"])
        if len(comments) < 100:
            return None
        page += 1


def _escape_command_data(value: str) -> str:
    return (
        value.replace("%", "%25")
        .replace("\r", "%0D")
        .replace("\n", "%0A")
    )


def escape_command_data(value: str) -> str:
    return _escape_command_data(value)


def _escape_command_property(value: str) -> str:
    return (
        _escape_command_data(value)
        .replace(":", "%3A")
        .replace(",", "%2C")
    )


def _is_updateable_sticky_comment(comment: object) -> bool:
    if not isinstance(comment, dict):
        return False
    if STICKY_MARKER not in (comment.get("body") or ""):
        return False
    user = comment.get("user") or {}
    if not isinstance(user, dict):
        return False
    login = str(user.get("login") or "")
    user_type = str(user.get("type") or "")
    return user_type == "Bot" or login.endswith("[bot]")
