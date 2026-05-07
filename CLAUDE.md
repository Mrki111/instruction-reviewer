# CLAUDE.md — instruction_reviewer

This repository is a GitHub Action + Python CLI whose only bundled rule is `INSTRUCTIONS_COMPLIANCE_001`: it sends repo instruction files plus a PR diff to Claude and reports violations. Prompt quality and trust-boundary work is the point of the project; do not re-introduce generic hygiene rules.

When changing this codebase, follow the rules below. They are written so a reviewer can verify each one from the diff alone.

## Security and trust boundaries

- **Rules and custom check files MUST be loaded from the PR base ref, not the head.** A PR cannot be allowed to relax its own configured checks. Any code that reads `.github/instruction-rules.json` or a `--checks-module` path must go through `read_file_at_ref(repo_root, base_ref, ...)` for repo-relative paths.
- **`--checks-module` MUST stay disabled when `GITHUB_EVENT_NAME == "pull_request_target"`.** Do not remove the guard in `cli.py` that exits with `::error::` in that case. Custom checks are arbitrary Python from the PR checkout.
- **All instruction-file content, commit text, and diff text sent to Anthropic MUST be HTML-escaped** via `_escape_prompt_text` before being placed into the user message. Never interpolate raw repo text into the prompt.
- **The pre-flight secret scan in `INSTRUCTIONS_COMPLIANCE_001` MUST run before any Anthropic API call.** If it finds a possible secret, the LLM call is skipped and a high-severity finding is emitted. Do not move, weaken, or short-circuit that scan.
- **Never log or print the request payload sent to Anthropic.** It contains diff text and commit messages from the reviewed repo.
- **`action.yml` MUST pass user inputs through environment variables before any shell use**, never via direct `${{ inputs.x }}` interpolation inside a `run:` block. Direct interpolation is a shell-injection sink.

## Default behavior

- **`INSTRUCTIONS_COMPLIANCE_001` defaults: `fail_open: true`, `fail_open_severity: low`, `severity: medium`.** Do not flip these defaults without an explicit changelog entry and a major-version bump.
- **`fail-on` defaults to `medium`** on both the Action and the CLI. A change to this default is a breaking change.
- **The rule's `severity` is a ceiling.** The LLM may downgrade an individual finding but `_findings_from_llm_payload` must continue to clamp anything above `rule.severity` back down. Do not allow per-finding escalation.
- **The model is pinned to `claude-sonnet-4-6`** in `default-rules.json`. Do not change the default model without checking with the maintainer; the rule's `model` config field is the user-facing escape hatch.
- **The Anthropic SDK is pinned `>=0.99,<1.0`** in `pyproject.toml`. Do not relax this pin without testing against the released SDK.

## Code shape

- **Do not import `anthropic` at module top level.** It must remain a local import inside `check_instructions_compliance` so non-LLM CLI runs do not require the SDK.
- **Do not add comments that explain WHAT the code does.** Only comment when the WHY is non-obvious (a hidden constraint, a workaround, an invariant a reader would otherwise miss).
- **Do not add backwards-compatibility shims, deprecation aliases, or `// removed` placeholders.** Pre-1.0, breaking changes are fine when documented in `CHANGELOG.md`; dead code is not.
- **Every new `git` subprocess call MUST go through `_git` or `_git_bytes` in `reviewer/diff.py`.** Both apply `GIT_TIMEOUT_SECONDS = 60`. Do not call `subprocess.run(["git", ...])` directly elsewhere — wedged jobs are the failure mode this guards against.
- **`Finding.kind == "diagnostic"` records (e.g. token usage) MUST NOT contribute to severity counts, the by-severity table, the findings listing, or the `fail-on` gate.** `severity_at_or_above` and the markdown reporter already filter them out — keep it that way.

## Scope of changes

- **Do not add generic hygiene checks (test coverage, commit length, PR size, secret scanning, etc.) to `reviewer/checks.py`.** They were removed deliberately to keep the project's scope on the LLM compliance check; reach for prompt-quality work in `reviewer/llm_check.py` instead.
- **Any change to `reviewer/default-rules.json` is a breaking change** (rules merge by id; consumers inherit defaults). Bump the version and document it in `CHANGELOG.md` before merging.
- **Public input/output names in `action.yml` MUST NOT be renamed without a major-version bump.** Consumers pin to `@v0`/`@v0.2.0` and call these by name.

## Tests

- **Unit tests MUST mock the Anthropic client** (see `tests/test_checks.py`). Real API calls only belong in `tests/test_anthropic_smoke.py`, which the daily smoke workflow runs against the live API.
- **Do not delete or weaken `tests/test_anthropic_smoke.py`.** It is the only signal that catches Anthropic SDK / API contract drift the mocks miss. Skipping it on `ANTHROPIC_API_KEY` absence is fine; removing the assertions is not.
