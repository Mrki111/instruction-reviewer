# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- LLM token observability. The report header gains an `LLM tokens:` line showing aggregated input/output/cache-create/cache-read counts and the prompt-cache hit rate. Same data is exposed structurally as a `kind: "diagnostic"` finding with `metadata.usage` in `--json-path` output.

### Changed
- The default `fail-on` threshold is now `medium` for both the Action and CLI, so default LLM instruction-compliance findings can fail CI.
- The composite Action now passes user inputs through environment variables before shell use, avoiding direct input interpolation inside the Bash script.
- The README now explicitly documents the data sent to Anthropic when the LLM compliance check runs.
- `Finding` gained `metadata: dict | None` and a `"diagnostic"` value for `kind`. Diagnostic findings are excluded from severity counts, the by-severity table, the findings listings, and the `fail-on` gate.
- Repo-relative custom check files are now loaded from the PR base ref, not the PR head, so a PR cannot weaken its own custom check implementation.
- GitHub annotations now skip diagnostic findings such as LLM token-usage records.

## [0.1.0] - 2026-05-06

Initial release.

### Action and CLI
- GitHub Action and `python -m reviewer` CLI that review PR diffs and commits against bundled hygiene rules and base-ref `AGENTS.md` / `CLAUDE.md` instructions.
- Markdown report (with `LLM compliance:` status line), GitHub step summary, file-level annotations, sticky PR comment.
- Custom checks via `reviewer.checks.register()` and project rule overrides via `.github/instruction-rules.json`.

### Bundled rules
- `TESTS_001`, `INSTR_001`, `COMMITS_001`, `COMMITS_002`, `SIZE_001`, `SECRETS_001` — hygiene baseline.
- `INSTRUCTIONS_COMPLIANCE_001` — LLM check using Claude Sonnet 4.6 with scoped per-directory review, base-ref pinning so a PR can't weaken its own rules, pre-flight secret scan, prompt-injection escaping, untrusted-output validation, and per-scope dedupe of fail-open findings.
- The rule's `severity` is treated as a ceiling: the LLM may downgrade an individual finding but cannot escalate past it.

### Operations
- 60-second timeout on every `git` subprocess call to prevent wedged workflow jobs.
- Daily smoke workflow that exercises the real Anthropic API to detect SDK/contract drift the mocked unit tests cannot catch.
- Release workflow that, on a `vX.Y.Z` tag push, creates a GitHub release and force-updates the `vX` major-version tag consumers pin to.
- `anthropic` dependency pinned as `>=0.99,<1.0` for compatibility with consumers.
