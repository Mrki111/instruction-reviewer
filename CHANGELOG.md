# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.2] - 2026-05-14

### Added
- `LICENSE` (MIT) and `SECURITY.md` at the repo root. `SECURITY.md` lists the disclosure contact plus an in-scope list mapped to the project's trust boundaries: base-ref pinning of rules and custom checks, `pull_request_target` hardening, prompt-injection escaping, pre-flight secret scan, shell-injection sinks, and finding-payload escaping in the sticky comment / Step Summary / JSON report.
- README now documents prompt-injection escaping in the "Data sent to Anthropic" section, a "Requirements" line (Python 3.11+, `ANTHROPIC_API_KEY`, `pull-requests: write`), an "Example report" sample of the rendered sticky comment, a "Troubleshooting" section keyed off the report's `LLM compliance:` header line, and a "Security and license" footer linking the two new files.
- Portfolio case study at `docs/CASE_STUDY.md` covering trust-boundary engineering decisions, measured cold/warm cache cost on a pinned commit, test/coverage summary, and the self-demonstration PR result.

### Changed
- `INSTRUCTIONS_COMPLIANCE_001` compliance prompt tightened in `reviewer/llm_check.py`. The "do not flag" list now says "Rules the diff follows correctly — only return violations, never compliance" instead of the ambiguous bare "Compliance"; `path` requires a path that appears in the diff with `null` reserved for commit-message or non-file violations; `line` requires the new-side line number of a `+` line you can actually see; severity calibration disambiguates `high` (NEVER / MUST NOT or security / correctness / data-loss) from `medium` (other firm requirements without security impact). Intended to reduce ambiguous-rule false positives at the merge gate.
- `LLM compliance:` status line in the markdown report now uses a colon separator (`skipped: <reason>` and `... some scope(s) skipped: <reason>`) so it matches the troubleshooting bullets in README. Previously used an em-dash separator that did not match the documented strings.

### Fixed
- README workflow snippets and custom-check examples now reference `Mrki111/instruction-reviewer@v0` (the actual repository owner). Previously referenced `infinum/instruction-reviewer@v0`, which would 404 for anyone copy-pasting the Quick start.

## [0.4.1] - 2026-05-12

### Fixed
- The LLM pre-flight secret scan now checks every line of the outbound diff payload, including removed and context lines, before sending anything to Anthropic. Previously only added lines were scanned, so removing an existing credential-shaped line could still send that removed line in the diff payload.
- README now matches the implemented fail gate for skipped findings: skipped findings count by severity, which keeps the default low fail-open skips advisory while allowing `fail_open_severity: high` to block strict repos.
- Package metadata is bumped to `0.4.1` so the next release tag can move consumers off the stale `v0.4.0` tag state, and the release workflow now fails if a pushed `vX.Y.Z` tag does not match `pyproject.toml`.

## [0.4.0] - 2026-05-12

### Changed
- The placeholder-secret bypass in `INSTRUCTIONS_COMPLIANCE_001`'s pre-flight scan now applies uniformly to provider-shaped patterns (`ghp_`, `gho_`, `ghs_`, `AKIA`). Previously only the generic credential pattern bypassed; provider patterns always tripped, which broke realistic test fixtures. A token whose random tail contains `fake`, `test`, `fixture`, etc. now bypasses for every pattern. Real opaque tokens still trip.
- LLM JSON-parse errors no longer include any of the model's response text in the finding message. Errors now surface as `response was not valid JSON (<msg> at char N)`. Closes a path where a malformed response could carry diff bytes into the sticky comment, step summary, or `--json-path` output.
- Commit subjects are HTML-escaped in the markdown report's commits block so a crafted `</details>` or stray tag in a commit subject cannot mangle the sticky comment.

### Fixed
- `action.yml` `rules` input description now states that repo-relative paths are read from `base-ref`.
- README clarified: matching ids merge over bundled defaults; unknown ids do not inherit (they use dataclass defaults `enabled: true`, `severity: medium`).
- Architecture map in README now points to `reviewer/llm_check.py` (the only registered rule) rather than `reviewer/checks.py`.

### Internal
- Dead head-ref helpers (`_resolve_instructions`, `_resolve_user_rules`) removed from `reviewer/cli.py`.
- All GitHub Actions pinned to commit SHAs across `action.yml` and workflows.
- Test workflow now triggers on push to `master` (was `main`); post-merge CI now runs.
- New trust-boundary tests: `..` traversal rejection in `_read_user_rules_from_base`, absolute-paths-outside-repo escape hatch, `--json-path` end-to-end output shape, severity-override pinning, commit-subject escape regression test, secret-scan-before-API-key-check ordering.
- Smoke test additionally asserts the token-usage diagnostic is present so SDK drift on `response.usage` is caught.
- `self-review.yml` removed — daily smoke covers SDK contract drift and the dogfood demo wasn't load-bearing.

## [0.3.0] - 2026-05-07

### Removed
- **Breaking:** the bundled hygiene rules `TESTS_001`, `INSTR_001`, `COMMITS_001`, `COMMITS_002`, `SIZE_001`, and `SECRETS_001` are gone. The action now ships only `INSTRUCTIONS_COMPLIANCE_001`. The pre-flight secret scan inside `INSTRUCTIONS_COMPLIANCE_001` is unchanged.

### Migration
- Drop any of the removed rule ids from `.github/instruction-rules.json`. They no longer have implementations and the CLI will surface them as `::warning::` lines about unknown rule ids.
- To keep an existing entry around as a placeholder (e.g. while you migrate other repos), set `"enabled": false` — disabled unknown ids do not warn.
- `default-rules.json` now contains a single entry, so user overrides merge against a much smaller surface. Behavioral defaults for `INSTRUCTIONS_COMPLIANCE_001` (`severity: medium`, `fail_open: true`, `fail_open_severity: low`, model `claude-sonnet-4-6`) are unchanged from 0.2.x.

## [0.2.1] - 2026-05-07

### Fixed
- `action.yml` no longer references `${{ secrets.GITHUB_TOKEN }}` inside the `github-token` input description. The `secrets` context is not available in action manifests, which prevented the Action from loading at all on consumer repos pinned to `v0.2.0` / `v0`.

### Changed
- The markdown report header now reads `Violations: N` instead of `Findings: N`, so the headline count matches what is actually counted (only `kind == "violation"`). Skipped and diagnostic entries are still surfaced in the LLM status line and the by-severity table.

## [0.2.0] - 2026-05-06

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
