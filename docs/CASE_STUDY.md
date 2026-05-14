# Instruction Reviewer: case study

A GitHub Action and Python CLI that uses Claude to verify whether a pull request's diff actually followed the rules written in `AGENTS.md` / `CLAUDE.md`. Built to turn instruction files from passive guidance into an enforceable CI signal, primarily for teams using AI-assisted development, where the agent itself wrote the diff and may have ignored the rules it was given.

- **Status:** v0.4.1 (pre-1.0)
- **Source:** [Mrki111/instruction-reviewer](https://github.com/Mrki111/instruction-reviewer)
- **Install:** `uses: Mrki111/instruction-reviewer@v0` in a workflow file (no Marketplace listing yet)
- **Stack:** Python 3.11+, Anthropic SDK, GitHub Actions composite action, no other runtime deps

---

## Problem

`CLAUDE.md` and `AGENTS.md` files have become the standard way to give coding assistants project-specific rules ("don't import this at module top level", "all instruction-file content sent to the LLM must be HTML-escaped", "default `fail-on` is `medium`; do not change without a major-version bump"). The files are read by the assistant when it generates code, but nothing in CI verifies that the generated code actually obeyed them. Human reviewers can't reliably hold dozens of repo-specific rules in their head while reviewing a 30-file PR.

The result is silent drift: the rules are written down, the assistant claims to follow them, the human reviewer doesn't catch the deviation, and the repo slowly diverges from its own documented standards.

## Solution

On every PR, a single bundled rule (`INSTRUCTIONS_COMPLIANCE_001`) sends three things to Claude Sonnet 4.6:

1. The instruction files (`AGENTS.md`, `CLAUDE.md`, and any matching globs), **loaded from the PR base SHA, not the head**.
2. The unified diff of the PR, scoped to files under each instruction's directory.
3. The PR's commit messages.

Claude returns a JSON list of violations validated against a `json_schema`. Each violation has a rule excerpt, severity, file path, and line number. Line numbers are validated against the actual changed lines before any inline annotation is emitted, so the model cannot make up positions.

Output surfaces in three places without further configuration:

- Markdown report in the workflow run's Step Summary.
- Inline annotations in the PR's "Files changed" tab.
- A single sticky comment on the PR that updates on each push.

A `fail-on` threshold (default `medium`) makes the rule a real merge gate, not just a comment.

---

## Engineering decisions worth calling out

The interesting parts of the project are the trust boundaries and the cost/UX tradeoffs, not the LLM call itself.

### 1. Base-ref pinning: a PR cannot relax its own rules

The most important security property of the project. Instruction files, rule config (`.github/instruction-rules.json`), and custom check modules (`--checks-module`) are all read from the PR **base** ref via `git show <base-sha>:<path>`, never from the PR head checkout.

Without this, a malicious PR could simply rewrite `CLAUDE.md` to say "no rules" or delete the instruction-rules file, and the review would happily run against the relaxed config. With base-ref pinning, the rules that gate the merge are the rules from the branch being merged into, which is exactly what a reviewer expects.

This is enforced in code, not by convention: `read_file_at_ref` is the only path for repo-relative reads, and the CLAUDE.md in this repo explicitly forbids reintroducing head-ref reads.

### 2. Pre-flight secret scan before the API call

Every line of the outbound diff payload and every commit message is scanned for credential-shaped strings *before* the Anthropic API call. If the scan trips, the LLM call is skipped and a high-severity finding is emitted instead. The reviewed repo's contents never leave the runner in that case.

This caught a real test fixture in this very repo during measurement: an early run against the v0.4.1 release commit refused to send the diff because `tests/test_checks.py` contained a deliberate fake-PAT string used by the secret-scan tests themselves. The scan working on the project that built it is the cleanest possible regression test.

### 3. `pull_request_target` hardening

Custom check modules are arbitrary Python loaded from the checkout. When the Action runs under `pull_request_target`, the GitHub event that *does* expose secrets to fork PRs, the `--checks-module` flag is disabled outright. The bundled LLM check still runs, but a fork PR cannot ship a Python file that gets executed with access to the org's `ANTHROPIC_API_KEY` and `GITHUB_TOKEN`.

`action.yml` also passes every user input through an environment variable into the shell step, rather than interpolating `${{ inputs.x }}` directly into the `run:` block. Direct interpolation is a textbook shell-injection sink.

### 4. Prompt-injection escaping

All instruction-file content, commit text, and diff text is `html.escape`-d before being placed into the user message. The system prompt explicitly tells the model to treat all of this as untrusted data and not to follow instructions inside it. Without escaping, a crafted comment like `</instruction_files>` could break out of its wrapping tag and inject new rules at review time.

### 5. Severity as a ceiling, not a default

The rule's configured `severity: medium` is a **maximum** the LLM can emit. If the model proposes a `high`-severity finding, it gets clamped back down to `medium`. This keeps the `fail-on` gate predictable: a repo that sets `fail-on: high` cannot suddenly have a previously-passing PR fail because the model decided today's finding felt scarier.

### 6. Prompt cache for the instruction block

The instruction files are sent as a `cache_control: ephemeral` block. The diff and commits are sent separately. Result: subsequent PRs in the same repo within the 5-minute TTL pay roughly 10× less for the instruction tokens. This matters because instruction files are the largest, slowest-changing part of the payload.

The report header surfaces `LLM tokens: X in (Y cached, Z% hit rate) / N out` so the cache can be verified to actually be working. A regression that silently disables caching is otherwise invisible.

### 7. Fail-open by default, fail-closed by config

If the Anthropic call fails (missing API key, network error, oversize diff, malformed JSON response), the default is a **low**-severity skip finding rather than a hard CI failure. The fail-on gate still applies, so strict repos can set `fail_open_severity: high` to make any LLM unavailability block merges. Defaults are for the median repo; the gate is for the strict ones.

---

## Measured performance

All numbers from running the CLI against this repo, May 2026, model `claude-sonnet-4-6`.

| Metric                              | Cold run     | Warm run (within 5-min cache TTL) |
|-------------------------------------|--------------|-----------------------------------|
| Wall-clock                          | 2.60 s       | 2.26 s                            |
| Input tokens (non-cached)           | 676          | 3                                 |
| Cache-write tokens                  | 2,298        | 673                               |
| Cache-read tokens                   | 0            | 2,298                             |
| Output tokens                       | 10           | 9                                 |
| **Estimated cost** (Sonnet 4.6 list)| **~$0.011**  | **~$0.0034**                      |

Test PR: a single-file, 12-line change to `reviewer/llm_check.py` (commit `a21a8a1`, "upgrade the compliance prompt"), reviewed against a 4.9 KB `CLAUDE.md`.

The cache cuts review cost ~3× on this small PR, because the instruction block (cacheable) dominates the prompt relative to the tiny diff. For larger PRs the diff portion grows but the instruction block stays cached, so the per-PR cost approaches `(diff_tokens × input_price) + output` after the first run in the TTL window.

For a typical larger PR (50 KB scoped diff, ~5 KB instruction file) the README quotes "a few cents per run." This measurement is consistent with that ceiling. The dominant input cost above is the (now-cached) instruction file, not the diff.

### Test suite and coverage

- **84 tests pass, 1 skipped** (the live-API smoke test, which runs in the daily smoke workflow when `ANTHROPIC_API_KEY` is set).
- **79% line coverage** overall.
- Per-module coverage on the load-bearing files: `reviewer/llm_check.py` 91%, `reviewer/checks.py` 85%, `reviewer/rules.py` 92%, `reviewer/reporters/markdown.py` 96%, `reviewer/reporters/json_report.py` 100%, `reviewer/instructions.py` 100%.
- The Anthropic client is mocked in every unit test. The single live-API smoke test (`tests/test_anthropic_smoke.py`) runs daily against the real API to catch SDK/contract drift that the mocks cannot.

### CI

Three workflows in `.github/workflows/`:

- `test.yml`: pytest on every push to `master` and every PR.
- `smoke.yml`: daily cron + `workflow_dispatch`, runs the live Anthropic call.
- `release.yml`: on a `vX.Y.Z` tag push, verifies the tag matches `pyproject.toml`, creates a GitHub Release, and force-updates the `vX` major-version tag that consumers pin to.

All third-party Actions are pinned to commit SHAs (not floating tags) in both `action.yml` and the workflow files.

---

## How to try it

Drop this into any repo at `.github/workflows/instruction-review.yml`:

```yaml
name: Instruction Review
on:
  pull_request:
    types: [opened, synchronize, reopened]
permissions:
  contents: read
  pull-requests: write
jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0   # full history for base..head diff
      - uses: Mrki111/instruction-reviewer@v0
        with:
          fail-on: medium
          github-token: ${{ secrets.GITHUB_TOKEN }}
          anthropic-api-key: ${{ secrets.ANTHROPIC_API_KEY }}
```

Defaults pick up `**/AGENTS.md` and `**/CLAUDE.md` from the base ref. `.github/instruction-rules.json` is picked up automatically when present.

To run locally against a working tree:

```sh
pip install -e .
python -m reviewer \
  --base-ref main \
  --head-ref HEAD \
  --repo-root . \
  --report-path /tmp/review.md
```

The CLI exits non-zero when there is any finding at or above `--fail-on` (default `medium`).

---

## Demonstration

A self-demonstration PR planted a single deliberate violation in this very repo: a top-level `import anthropic` in `reviewer/cli.py`, which `CLAUDE.md` explicitly forbids ("Do not import `anthropic` at module top level. It must remain a local import inside `check_instructions_compliance` so non-LLM CLI runs do not require the SDK.").

**Demo PR:** [Mrki111/instruction-reviewer#7](https://github.com/Mrki111/instruction-reviewer/pull/7)

Result on the PR:

- The existing **Test** workflow passed (`18s` green). Unit tests do not enforce this convention, so they had nothing to say about it.
- The **Instruction Review** workflow failed (`18s`, `fail-on: medium` triggered).
- The reviewer posted a single sticky comment with one medium-severity finding pinned at `reviewer/cli.py:14`, quoting the exact rule excerpt from `CLAUDE.md`:

  > A top-level `import anthropic` is added in `reviewer/cli.py` at line 14, directly violating the rule that `anthropic` must only be imported locally inside `check_instructions_compliance`. (rule: Do not import `anthropic` at module top level. It must remain a local import inside `check_instructions_compliance` so non-LLM CLI runs do not require the SDK.)

- Token usage on the run (cold cache): **3,564 input / 120 output** on `claude-sonnet-4-6`, about $0.012.

The split between the two checks is the point: tests confirm code correctness, the reviewer confirms convention compliance. They catch different classes of regression and they should both be required for merge.

---

## What I'd build next

- Marketplace listing under a stable org so consumers can `uses: <org>/instruction-reviewer@v1` without pinning a personal namespace.
- A "rule confidence" pass that re-asks the model on findings it self-rated `medium`+, to reduce false positives at the merge gate.
- A standalone CLI distribution (`pipx install instruction-reviewer`) so the same checks can run in pre-commit and IDE save hooks, not just CI.
- An optional fail-open dashboard: if a repo's last N runs all fail-opened (missing key, oversize diff, malformed response), surface that as a degraded-state badge in the README the way Codecov does for coverage.
