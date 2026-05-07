from __future__ import annotations

import codecs
import fnmatch
import re
import shlex
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Callable, Iterable

from reviewer.diff import Commit, Diff
from reviewer.instructions import InstructionFile
from reviewer.rules import Rule

SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2}


class CheckConfigurationError(RuntimeError):
    """A check cannot run because of missing configuration (e.g. API key).

    The CLI converts this into an exit-2 with a ``::error::`` annotation rather
    than letting it surface as a stack trace. Distinct from a finding, which
    counts toward the fail-on threshold but doesn't abort the run.
    """


@dataclass
class Finding:
    rule_id: str
    severity: str
    message: str
    path: str | None = None
    line: int | None = None
    # "violation" for real findings, "skipped" for fail-open / bail-out
    # markers, "diagnostic" for observability records (e.g. token usage).
    # The renderer uses this to surface whether load-bearing checks actually
    # executed; diagnostics are filtered out of severity counts and tables.
    kind: str = "violation"
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class SecretPattern:
    pattern: re.Pattern[str]
    label: str
    value_group: str | None = None


CheckFn = Callable[
    [Rule, Diff, list[Commit], list[InstructionFile]],
    Iterable[Finding] | None,
]
CHECKS: dict[str, CheckFn] = {}


def register(rule_id: str) -> Callable[[CheckFn], CheckFn]:
    def deco(fn: CheckFn) -> CheckFn:
        CHECKS[rule_id] = fn
        return fn

    return deco


def run_checks(
    rules: list[Rule],
    diff: Diff,
    commits: list[Commit],
    instructions: list[InstructionFile],
) -> list[Finding]:
    findings: list[Finding] = []
    # Loop through every rule and call its registered check function.
    for rule in rules:
        if not rule.enabled:
            continue
        fn = CHECKS.get(rule.id)
        if fn is None:
            continue
        # Append the findings returned by this check to the running list.
        result = fn(rule, diff, commits, instructions) or []
        findings.extend(result)
    return findings


def unimplemented_rule_ids(rules: Iterable[Rule]) -> list[str]:
    return sorted(
        {rule.id for rule in rules if rule.enabled and rule.id not in CHECKS}
    )


def severity_at_or_above(findings: Iterable[Finding], threshold: str) -> int:
    t = SEVERITY_ORDER[threshold]
    return sum(
        1
        for f in findings
        if f.kind != "diagnostic" and SEVERITY_ORDER[f.severity] >= t
    )


# --- helpers ---------------------------------------------------------------


def _match_any(patterns: list[str], path: str) -> bool:
    """Match against either the full path or the basename, using fnmatch.

    fnmatch's ``*`` matches ``/`` permissively, so ``src/**/test_*.py`` and
    similar work without globstar support. The remaining gaps:

    - ``**/`` prefix should also match a basename (zero leading segments).
    - ``/**/`` in the middle should also match zero path segments
      (``src/**/test_*.py`` should match ``src/test_x.py``).
    - ``/**`` suffix should match files under the directory.
    """
    if not patterns:
        return False
    name = PurePosixPath(path).name
    for pattern in patterns:
        if fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(name, pattern):
            return True
        if pattern.startswith("**/") and fnmatch.fnmatch(name, pattern[3:]):
            return True
        if pattern.endswith("/**") and path.startswith(pattern[:-3] + "/"):
            return True
        if "/**/" in pattern and fnmatch.fnmatch(path, pattern.replace("/**/", "/")):
            return True
    return False


def _decode_diff_path_token(token: str, prefix: str) -> str | None:
    if "\\" in token:
        token = codecs.decode(token, "unicode_escape")
    if token == "/dev/null":
        return None
    return token[len(prefix):] if token.startswith(prefix) else None


def _parse_diff_path_line(line: str, marker: str, prefix: str) -> str | None:
    if not line.startswith(f"{marker} "):
        return None
    try:
        parts = shlex.split(line)
    except ValueError:
        parts = line.split(maxsplit=1)
    if len(parts) < 2 or parts[0] != marker:
        return None
    return _decode_diff_path_token(parts[1], prefix)


def _parse_diff_git_header(line: str) -> tuple[str | None, str | None]:
    if not line.startswith("diff --git "):
        return None, None
    try:
        parts = shlex.split(line)
    except ValueError:
        parts = line.split()
    if len(parts) < 4:
        return None, None
    old_path = _decode_diff_path_token(parts[2], "a/")
    new_path = _decode_diff_path_token(parts[3], "b/")
    return old_path, new_path


def _str_list_config(rule: Rule, key: str, default: list[str]) -> list[str]:
    value = rule.config.get(key, default)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise CheckConfigurationError(
            f"{rule.id}.{key} must be a list of strings."
        )
    return value


def _int_config(
    rule: Rule,
    key: str,
    default: int,
    *,
    min_value: int | None = None,
) -> int:
    value = rule.config.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise CheckConfigurationError(f"{rule.id}.{key} must be an integer.")
    if min_value is not None and value < min_value:
        raise CheckConfigurationError(
            f"{rule.id}.{key} must be greater than or equal to {min_value}."
        )
    return value


def _float_config(
    rule: Rule,
    key: str,
    default: float,
    *,
    min_value: float | None = None,
) -> float:
    value = rule.config.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CheckConfigurationError(f"{rule.id}.{key} must be a number.")
    value = float(value)
    if min_value is not None and value < min_value:
        raise CheckConfigurationError(
            f"{rule.id}.{key} must be greater than or equal to {min_value}."
        )
    return value


def _severity_config(rule: Rule, key: str, default: str) -> str:
    value = rule.config.get(key, default)
    if not isinstance(value, str) or value not in SEVERITY_ORDER:
        raise CheckConfigurationError(
            f"{rule.id}.{key} must be one of: {', '.join(SEVERITY_ORDER)}."
        )
    return value


def _bool_config(value: Any, default: bool, *, rule_id: str, key: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
        raise CheckConfigurationError(f"{rule_id}.{key} must be a boolean.")
    if value is None:
        return default
    raise CheckConfigurationError(f"{rule_id}.{key} must be a boolean.")


# --- bundled checks --------------------------------------------------------


@register("TESTS_001")
def check_tests_reported(
    rule: Rule, diff: Diff, commits: list[Commit], instructions: list[InstructionFile]
) -> list[Finding]:
    test_patterns = _str_list_config(rule, "test_patterns", [])
    source_patterns = _str_list_config(rule, "source_patterns", [])

    src_changed = [
        f for f in diff.files
        if _match_any(source_patterns, f.path) and not _match_any(test_patterns, f.path)
    ]
    test_changed = [f for f in diff.files if _match_any(test_patterns, f.path)]

    if src_changed and not test_changed:
        sample = ", ".join(f.path for f in src_changed[:3])
        more = "" if len(src_changed) <= 3 else f", +{len(src_changed) - 3} more"
        return [
            Finding(
                rule_id=rule.id,
                severity=rule.severity,
                message=(
                    f"{len(src_changed)} source file(s) changed but no tests "
                    f"were modified ({sample}{more})."
                ),
            )
        ]
    return []


@register("INSTR_001")
def check_instructions_modified(
    rule: Rule, diff: Diff, commits: list[Commit], instructions: list[InstructionFile]
) -> list[Finding]:
    names = _str_list_config(
        rule, "instruction_filenames", ["AGENTS.md", "CLAUDE.md"]
    )
    instr_files = [f for f in diff.files if PurePosixPath(f.path).name in names]
    if not instr_files:
        return []
    return [
        Finding(
            rule_id=rule.id,
            severity=rule.severity,
            message=(
                "Instruction file(s) modified — make sure the rest of the diff "
                f"reflects the new guidance: {', '.join(f.path for f in instr_files)}"
            ),
            path=instr_files[0].path,
        )
    ]


@register("COMMITS_001")
def check_commit_subject_length(
    rule: Rule, diff: Diff, commits: list[Commit], instructions: list[InstructionFile]
) -> list[Finding]:
    max_len = _int_config(rule, "max_subject_length", 72, min_value=1)
    out: list[Finding] = []
    for c in commits:
        if len(c.subject) > max_len:
            out.append(
                Finding(
                    rule_id=rule.id,
                    severity=rule.severity,
                    message=(
                        f"Commit {c.sha[:7]} subject is {len(c.subject)} chars "
                        f"(max {max_len}): {c.subject[:80]}"
                    ),
                )
            )
    return out


@register("COMMITS_002")
def check_commit_body(
    rule: Rule, diff: Diff, commits: list[Commit], instructions: list[InstructionFile]
) -> list[Finding]:
    min_files = _int_config(rule, "min_files_for_body", 3, min_value=1)
    out: list[Finding] = []
    for c in commits:
        if len(c.files) >= min_files and not c.body.strip():
            out.append(
                Finding(
                    rule_id=rule.id,
                    severity=rule.severity,
                    message=(
                        f"Commit {c.sha[:7]} touches {len(c.files)} files but has "
                        "no message body explaining the change."
                    ),
                )
            )
    return out


@register("SIZE_001")
def check_pr_size(
    rule: Rule, diff: Diff, commits: list[Commit], instructions: list[InstructionFile]
) -> list[Finding]:
    max_lines = _int_config(rule, "max_lines_changed", 1000, min_value=1)
    total = sum(f.additions + f.deletions for f in diff.files)
    if total > max_lines:
        return [
            Finding(
                rule_id=rule.id,
                severity=rule.severity,
                message=(
                    f"PR changes {total} lines across {len(diff.files)} file(s) "
                    f"(threshold {max_lines}). Consider splitting it."
                ),
            )
        ]
    return []


SECRET_PATTERNS: list[SecretPattern] = [
    SecretPattern(re.compile(r"AKIA[0-9A-Z]{16}"), "AWS access key id"),
    SecretPattern(
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
        "Private key block",
    ),
    SecretPattern(re.compile(r"ghp_[A-Za-z0-9]{30,}"), "GitHub personal access token"),
    SecretPattern(re.compile(r"gho_[A-Za-z0-9]{30,}"), "GitHub OAuth token"),
    SecretPattern(re.compile(r"ghs_[A-Za-z0-9]{30,}"), "GitHub server-to-server token"),
    SecretPattern(
        re.compile(
            r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*['\"](?P<value>[^'\"\s]{16,})['\"]"
        ),
        "Hard-coded credential literal",
        value_group="value",
    ),
]


PLACEHOLDER_SECRET_TERMS = {
    "changeme",
    "change_me",
    "dummy",
    "example",
    "fake",
    "fixture",
    "not_secret",
    "notsecret",
    "placeholder",
    "redacted",
    "sample",
    "test",
    "testing",
}


def _is_placeholder_secret(match: re.Match[str], secret_pattern: SecretPattern) -> bool:
    if secret_pattern.value_group is None:
        return False
    value = match.group(secret_pattern.value_group).lower()
    normalized = re.sub(r"[^a-z0-9]+", "_", value)
    return any(term in normalized for term in PLACEHOLDER_SECRET_TERMS)


def _secret_label_for_text(text: str) -> str | None:
    for secret_pattern in SECRET_PATTERNS:
        match = secret_pattern.pattern.search(text)
        if match and not _is_placeholder_secret(match, secret_pattern):
            return secret_pattern.label
    return None


def _iter_added_lines(raw_diff: str) -> Iterable[tuple[str | None, int, str]]:
    """Yield ``(path, new_line_no, content)`` for each ``+`` line in the diff.

    ``path`` is ``None`` for ``+`` lines that appear before any ``+++`` header
    (only relevant for malformed diffs); ``content`` excludes the leading ``+``.
    Headers and ``-`` lines do not advance the new-side counter.
    """
    current_path: str | None = None
    new_line_no = 0
    for line in raw_diff.splitlines():
        new_path = _parse_diff_path_line(line, "+++", "b/")
        if new_path is not None:
            current_path = new_path
            new_line_no = 0
            continue
        if line.startswith("+++ "):
            current_path = None
            continue
        hunk = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
        if hunk:
            new_line_no = int(hunk.group(1)) - 1
            continue
        if line.startswith("+") and not line.startswith("+++"):
            new_line_no += 1
            yield current_path, new_line_no, line[1:]
        elif line.startswith(" "):
            new_line_no += 1


def _secret_findings_for_diff(rule_id: str, severity: str, diff: Diff) -> list[Finding]:
    findings: list[Finding] = []
    for path, lineno, content in _iter_added_lines(diff.raw):
        secret_label = _secret_label_for_text(content)
        if secret_label:
            findings.append(
                Finding(
                    rule_id=rule_id,
                    severity=severity,
                    message=f"Possible {secret_label} introduced on this line.",
                    path=path,
                    line=lineno,
                )
            )
    return findings


def _secret_findings_for_commits(
    rule_id: str, severity: str, commits: list[Commit]
) -> list[Finding]:
    findings: list[Finding] = []
    for commit in commits:
        for line in [commit.subject, *commit.body.splitlines()]:
            secret_label = _secret_label_for_text(line)
            if secret_label:
                findings.append(
                    Finding(
                        rule_id=rule_id,
                        severity=severity,
                        message=(
                            f"Possible {secret_label} found in commit "
                            f"{commit.sha[:7]} message."
                        ),
                    )
                )
                break
    return findings


@register("SECRETS_001")
def check_secrets(
    rule: Rule, diff: Diff, commits: list[Commit], instructions: list[InstructionFile]
) -> list[Finding]:
    return [
        *_secret_findings_for_diff(rule.id, rule.severity, diff),
        *_secret_findings_for_commits(rule.id, rule.severity, commits),
    ]


# Imported at the bottom for side effects: registers INSTRUCTIONS_COMPLIANCE_001
# against this module's CHECKS dict. The import lives here (rather than in
# reviewer/__init__.py) so any caller of `from reviewer.checks import …` also
# triggers registration without needing a separate setup step.
from reviewer import llm_check as _llm_check  # noqa: E402, F401
