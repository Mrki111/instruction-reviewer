from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.util
import json
import os
import re
import sys
import types
from pathlib import Path

from reviewer.checks import (
    CheckConfigurationError,
    _match_any,
    run_checks,
    severity_at_or_above,
    unimplemented_rule_ids,
)
from reviewer.diff import GitError, build_pr_diff, list_files_at_ref, read_file_at_ref
from reviewer.instructions import InstructionFile
from reviewer.reporters import (
    emit_annotations,
    escape_command_data,
    post_sticky_comment,
    render_json,
    render_report,
    set_outputs,
    write_step_summary,
)
from reviewer.rules import Rule, _parse_bool, load_rules_from_texts

DEFAULT_INSTRUCTION_GLOBS = ["AGENTS.md", "CLAUDE.md"]
BUNDLED_DEFAULT_RULES = Path(__file__).parent / "default-rules.json"
AUTO_RULES_PATH = Path(".github/instruction-rules.json")
IGNORED_INSTRUCTION_DIRS = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "site-packages",
}


def _split_globs(value: str) -> list[str]:
    return [g.strip() for g in re.split(r"[,\n]+", value or "") if g.strip()]


def _resolve_instructions_at_ref(
    repo_root: Path, ref: str, raw: str
) -> list[InstructionFile]:
    try:
        candidate_paths = list_files_at_ref(repo_root, ref)
    except GitError as e:
        raise GitError(f"Could not list instruction files at {ref}: {e}") from e
    globs = _split_globs(raw) or [f"**/{name}" for name in DEFAULT_INSTRUCTION_GLOBS]
    found: list[InstructionFile] = []
    seen: set[str] = set()
    for rel in candidate_paths:
        if rel in seen:
            continue
        if _ignored_instruction_path(rel) or not _match_any(globs, rel):
            continue
        seen.add(rel)
        try:
            content = read_file_at_ref(repo_root, ref, rel)
        except GitError:
            continue
        found.append(InstructionFile(path=rel, content=content))
    return found


def _ignored_instruction_path(path: str) -> bool:
    return any(part in IGNORED_INSTRUCTION_DIRS for part in Path(path).parts)


def _repo_relative_path(raw: str, label: str) -> str:
    path = Path(raw)
    if path.is_absolute():
        raise ValueError(f"repo-relative {label} path expected.")
    rel = path.as_posix()
    parts = Path(rel).parts
    if not rel or ".." in parts:
        raise ValueError(f"{label.capitalize()} path must stay inside the repository: {raw}")
    return rel


def _repo_relative_rule_path(raw: str) -> str:
    return _repo_relative_path(raw, "rule")


def _read_user_rules_from_base(
    repo_root: Path, base_ref: str, raw: str
) -> tuple[str | None, str]:
    """Return user rule JSON from the trusted base ref.

    Repository-relative rule files are part of the reviewed repository's policy,
    so the PR head must not be allowed to relax them. Absolute paths remain
    local/trusted escape hatches for non-Action CLI usage.
    """
    if raw:
        path = Path(raw)
        if path.is_absolute():
            try:
                rel = _repo_relative_rule_path(
                    path.resolve().relative_to(repo_root).as_posix()
                )
            except ValueError:
                return path.read_text(encoding="utf-8"), str(path)
            try:
                return read_file_at_ref(repo_root, base_ref, rel), f"{base_ref}:{rel}"
            except GitError as e:
                raise ValueError(f"User rules file not found at base ref: {rel}") from e
        rel = _repo_relative_rule_path(raw)
        try:
            return read_file_at_ref(repo_root, base_ref, rel), f"{base_ref}:{rel}"
        except GitError as e:
            raise ValueError(f"User rules file not found at base ref: {rel}") from e

    rel = AUTO_RULES_PATH.as_posix()
    try:
        return read_file_at_ref(repo_root, base_ref, rel), f"{base_ref}:{rel}"
    except GitError:
        return None, rel


def _load_rules_for_review(
    default_path: Path, repo_root: Path, raw_user_rules: str, base_ref: str
) -> list[Rule]:
    user_text, user_label = _read_user_rules_from_base(
        repo_root, base_ref, raw_user_rules
    )
    return load_rules_from_texts(default_path, user_text, user_label)


def _read_pr_number_from_event() -> int | None:
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        return None
    try:
        event = json.loads(Path(event_path).read_text())
    except (OSError, json.JSONDecodeError):
        return None
    pr = event.get("pull_request") or {}
    number = pr.get("number")
    if isinstance(number, int):
        return number
    issue = event.get("issue") or {}
    if isinstance(issue.get("number"), int) and "pull_request" in issue:
        return int(issue["number"])
    return None


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="instruction-reviewer",
        description=(
            "Review a PR diff against base-ref AGENTS.md / CLAUDE.md "
            "instructions."
        ),
    )
    p.add_argument("--base-ref", required=True, help="Base ref or SHA to diff against.")
    p.add_argument("--head-ref", required=True, help="Head ref or SHA being reviewed.")
    p.add_argument(
        "--instructions",
        default="",
        help="Comma- or newline-separated globs for instruction files.",
    )
    p.add_argument(
        "--rules",
        default="",
        help=(
            "Path to a user rules JSON, merged onto defaults. If omitted, "
            ".github/instruction-rules.json is auto-detected when present."
        ),
    )
    p.add_argument(
        "--default-rules",
        default="",
        help="Path to default rules JSON. Defaults to the bundled file.",
    )
    p.add_argument(
        "--checks-module",
        default="",
        help=(
            "Comma- or newline-separated Python module names or .py files to "
            "import before running checks. Extensions register checks with "
            "reviewer.checks.register()."
        ),
    )
    p.add_argument(
        "--fail-on",
        choices=["low", "medium", "high"],
        default="medium",
        help="Severity threshold that fails the run.",
    )
    p.add_argument("--repo-root", default=".", help="Repository root path.")
    p.add_argument(
        "--report-path",
        default="instruction-review.md",
        help="Where to write the markdown report.",
    )
    p.add_argument(
        "--json-path",
        default="",
        help="If set, also write the report as JSON to this path.",
    )
    p.add_argument(
        "--github-output",
        action="store_true",
        help="Emit GitHub Actions step summary, annotations, and outputs.",
    )
    p.add_argument(
        "--comment-on-pr",
        default="true",
        help="When --github-output is set, post a sticky PR comment (true/false).",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    default_path = Path(args.default_rules) if args.default_rules else BUNDLED_DEFAULT_RULES
    if not default_path.exists():
        print(f"::error::Default rules file not found: {default_path}", file=sys.stderr)
        return 2

    try:
        rules = _load_rules_for_review(
            default_path, repo_root, args.rules, args.base_ref
        )
    except (OSError, ValueError) as e:
        print(f"::error::Invalid rules configuration: {e}", file=sys.stderr)
        return 2

    if _is_pull_request_target() and _split_globs(args.checks_module):
        print(
            "::error::--checks-module is disabled on pull_request_target because "
            "it would execute Python from the pull request checkout with base "
            "repository privileges.",
            file=sys.stderr,
        )
        return 2

    try:
        _load_check_extensions(repo_root, args.checks_module, args.base_ref)
    except Exception as e:
        print(f"::error::Could not load check extension: {e}", file=sys.stderr)
        return 2

    unknown_rule_ids = unimplemented_rule_ids(rules)
    if unknown_rule_ids:
        _emit_warning(
            "No check implementation registered for enabled rule id(s): "
            + ", ".join(unknown_rule_ids),
            github=args.github_output,
        )

    try:
        diff, commits = build_pr_diff(args.base_ref, args.head_ref, repo_root)
    except GitError as e:
        print(f"::error::{e}", file=sys.stderr)
        return 2

    try:
        instructions = _resolve_instructions_at_ref(
            repo_root, args.base_ref, args.instructions
        )
    except GitError as e:
        print(f"::error::{e}", file=sys.stderr)
        return 2
    if not instructions:
        _emit_notice(
            "No instruction files found at the PR base; running bundled and configured rules only.",
            github=args.github_output,
        )

    try:
        findings = run_checks(rules, diff, commits, instructions)
    except CheckConfigurationError as e:
        print(f"::error::{e}", file=sys.stderr)
        return 2
    report = render_report(
        findings, diff, commits, instructions, args.fail_on, rules=rules
    )

    report_path = Path(args.report_path)
    report_path.write_text(report, encoding="utf-8")

    if args.json_path:
        Path(args.json_path).write_text(
            render_json(findings, diff, commits, instructions, args.fail_on),
            encoding="utf-8",
        )

    fail_count = severity_at_or_above(findings, args.fail_on)

    if args.github_output:
        write_step_summary(report)
        emit_annotations(findings)
        set_outputs(
            **{
                "report-path": str(report_path),
                "fail-count": fail_count,
            }
        )
        try:
            should_comment = _parse_bool(args.comment_on_pr, "--comment-on-pr")
        except ValueError as e:
            print(f"::error::{e}", file=sys.stderr)
            return 2
        if should_comment:
            token = os.environ.get("GITHUB_TOKEN")
            repo = os.environ.get("GITHUB_REPOSITORY")
            pr_number = _read_pr_number_from_event()
            if token and repo and pr_number:
                try:
                    post_sticky_comment(report, repo, pr_number, token)
                except Exception as e:  # network / parse failures shouldn't fail the job
                    _emit_warning(
                        f"Failed to post sticky comment: {e}",
                        github=True,
                    )
            else:
                missing = [
                    name
                    for name, ok in [
                        ("GITHUB_TOKEN", bool(token)),
                        ("GITHUB_REPOSITORY", bool(repo)),
                        ("PR number from event", pr_number is not None),
                    ]
                    if not ok
                ]
                _emit_notice(
                    f"Skipping sticky comment; missing: {', '.join(missing)}",
                    github=True,
                )

    return 1 if fail_count > 0 else 0


def _emit_notice(message: str, github: bool) -> None:
    if github:
        print(f"::notice::{escape_command_data(message)}")
    else:
        print(f"Notice: {message}", file=sys.stderr)


def _emit_warning(message: str, github: bool) -> None:
    if github:
        print(f"::warning::{escape_command_data(message)}")
    else:
        print(f"Warning: {message}", file=sys.stderr)


def _is_pull_request_target() -> bool:
    return os.environ.get("GITHUB_EVENT_NAME") == "pull_request_target"


def _load_check_extensions(
    repo_root: Path, raw: str, base_ref: str | None = None
) -> None:
    for item in _split_globs(raw):
        if item.endswith(".py") or "/" in item or "\\" in item:
            _load_check_extension_file(repo_root, item, base_ref)
        else:
            _load_check_extension_module(repo_root, item, base_ref)


def _load_check_extension_file(
    repo_root: Path, raw_path: str, base_ref: str | None
) -> None:
    path = Path(raw_path)
    if path.is_absolute():
        try:
            rel = _repo_relative_path(
                path.resolve().relative_to(repo_root.resolve()).as_posix(),
                "extension",
            )
        except ValueError:
            _load_check_extension_local_file(path)
            return
    else:
        rel = _repo_relative_path(raw_path, "extension")

    if base_ref is None:
        _load_check_extension_local_file(repo_root / rel)
        return

    try:
        source = read_file_at_ref(repo_root, base_ref, rel)
    except GitError as e:
        raise ValueError(f"extension file not found at base ref: {rel}") from e
    _load_check_extension_source(source, f"{base_ref}:{rel}")


def _load_check_extension_local_file(path: Path) -> None:
    if not path.exists():
        raise ValueError(f"extension file not found: {path}")
    digest = hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:12]
    module_name = f"_instruction_reviewer_ext_{digest}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"could not import extension file: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)


def _load_check_extension_source(source: str, label: str) -> None:
    digest = hashlib.sha1(label.encode("utf-8")).hexdigest()[:12]
    module_name = f"_instruction_reviewer_ext_{digest}"
    module = types.ModuleType(module_name)
    module.__file__ = label
    sys.modules[module_name] = module
    try:
        exec(compile(source, label, "exec"), module.__dict__)
    except Exception:
        sys.modules.pop(module_name, None)
        raise


def _load_check_extension_module(
    repo_root: Path, module_name: str, base_ref: str | None
) -> None:
    if base_ref is None:
        importlib.import_module(module_name)
        return

    # Trust boundary: filter sys.path entries inside repo_root so we cannot
    # accidentally import a sibling module from the PR head checkout. This
    # does NOT cover packages already installed into site-packages — those
    # were chosen by a trusted workflow step (e.g. `pip install`), not by
    # the PR. The `pull_request_target` guard in main() blocks --checks-module
    # outright, which is the load-bearing defense for fork PRs.
    old_path = list(sys.path)
    sys.path = [
        entry
        for entry in sys.path
        if not _path_inside_repo(Path(entry or os.getcwd()), repo_root)
    ]
    try:
        importlib.import_module(module_name)
    finally:
        sys.path = old_path


def _path_inside_repo(path: Path, repo_root: Path) -> bool:
    try:
        path.resolve().relative_to(repo_root.resolve())
        return True
    except ValueError:
        return False


if __name__ == "__main__":
    sys.exit(main())
