# Instruction Reviewer

A GitHub Action and Python CLI that checks whether PR changes follow the repository's Markdown guidelines. By default it reviews `AGENTS.md` and `CLAUDE.md`; other instruction files such as `REVIEW.md` or team-specific files can be included with the `instructions` input.

Instruction Reviewer turns repo instruction files from passive guidance into an enforceable CI signal by sending them, the PR diff, and commit messages to Claude and reporting violations. It is built for teams using AI-assisted development who want to verify that generated or human-written changes actually followed the rules documented in the repo.

Drop one workflow file into a repo and you get:

- A markdown report on the workflow run page (Step Summary).
- Inline annotations in the PR's "Files changed" tab for findings with a known location.
- A single sticky comment on the PR that updates on each push.
- A `fail-count` output and an exit code that fails the job above a configurable severity threshold.
- Optional project-level overrides from `.github/instruction-rules.json`.
- Optional custom checks loaded from a project Python module.

## Quick start

Add this to a consumer repo at `.github/workflows/instruction-review.yml`:

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
      - uses: infinum/instruction-reviewer@v0
        with:
          fail-on: medium
          github-token: ${{ secrets.GITHUB_TOKEN }}
          anthropic-api-key: ${{ secrets.ANTHROPIC_API_KEY }}   # enables the LLM check
```

That's the default adoption path. `@v0` tracks the current pre-1.0 major release; pin an exact release tag or commit SHA if your organization requires stricter reproducibility. Defaults are bundled, and `.github/instruction-rules.json` is picked up automatically when present.

> The `anthropic-api-key` input is what makes the load-bearing `INSTRUCTIONS_COMPLIANCE_001` LLM check run. When it is missing, the rule emits a low-severity skip finding by default instead of breaking every repo; set `fail_open: false` for strict repos that must fail when the LLM review cannot run.
> The default `fail-on: medium` means medium and high findings fail CI, including default LLM instruction-compliance violations.

## Inputs

| Name            | Default            | Description                                                                 |
|-----------------|--------------------|-----------------------------------------------------------------------------|
| `instructions`      | `**/AGENTS.md,**/CLAUDE.md` | Comma- or newline-separated globs for instruction files.            |
| `rules`             | `.github/instruction-rules.json` at base ref if present | Path to a user rules JSON. Repo-relative paths are read from `base-ref` and merged onto bundled defaults by `id`. |
| `checks-module`     | `""`               | Optional Python module name or `.py` file that registers custom checks with `reviewer.checks.register()`. |
| `fail-on`           | `medium`           | `low`, `medium`, or `high` — severity at or above this fails the job.       |
| `base-ref`          | PR base SHA        | Override the base ref. Auto-detected from the `pull_request` event.         |
| `comment-on-pr`     | `true`             | Whether to post the sticky PR comment.                                      |
| `github-token`      | `""`               | Required only when `comment-on-pr: true`.                                   |
| `anthropic-api-key` | `""`               | Required for the LLM check to call Anthropic. Missing keys skip by default unless `fail_open: false`. |

## Outputs

| Name          | Description                                                |
|---------------|------------------------------------------------------------|
| `report-path` | Path to the markdown report inside the runner.             |
| `fail-count`  | Number of findings at or above the `fail-on` threshold.    |

## Bundled rules

The action ships one rule, defined in [`reviewer/default-rules.json`](reviewer/default-rules.json):

| Id                            | Severity | What it checks                                                                                                                  |
|-------------------------------|----------|---------------------------------------------------------------------------------------------------------------------------------|
| `INSTRUCTIONS_COMPLIANCE_001` | medium   | **LLM check.** Sends scoped instructions + scoped diff + commits to Claude Sonnet 4.6 and reports any violations it identifies. |

### `INSTRUCTIONS_COMPLIANCE_001` — the LLM compliance check

This is the rule that verifies *"did the AI follow the rules in CLAUDE.md?"*. It does the following:

1. Loads instruction files from the PR base SHA, not from the PR head. Repo-relative rule overrides are also loaded from the base SHA.
2. Applies instruction files by directory scope. Root `AGENTS.md` / `CLAUDE.md` apply everywhere; nested files apply only to changed files under their directory.
3. Reviews each instruction scope separately, so nested rules are not applied to unrelated files.
4. Refuses to call Anthropic when any outbound diff payload line or commit message looks like a credential, so suspected secrets are not sent to the provider.
5. Sends the applicable instructions, scoped unified diff, and commit messages to `claude-sonnet-4-6` via the Anthropic API, with a system prompt that asks for *violations only* (not compliance, not unrelated rules).
6. Receives a structured JSON list of findings (rule excerpt, severity, file/line) via `output_config.format` with a `json_schema`.
7. Validates LLM-reported paths/lines against changed files in that scope before emitting annotations.
8. Returns those findings just like every other check — they appear in the report, the sticky comment, the annotations, and count toward the `fail-on` threshold.

The instruction-files block is sent with `cache_control: ephemeral` so subsequent PRs in the same repo hit the prompt cache (~10× cheaper input tokens).

### Data sent to Anthropic

When `INSTRUCTIONS_COMPLIANCE_001` runs with `ANTHROPIC_API_KEY` set, it sends the applicable base-ref instruction files, scoped unified diff hunks, and commit messages to Anthropic. It does not send the full repository checkout. Before any LLM call, the reviewer scans every outbound diff payload line and commit messages for likely credentials; if a suspected secret is found, the LLM check is skipped for that run so the payload is not sent to Anthropic.

When `ANTHROPIC_API_KEY` is missing, applicable base-ref instruction files exist, and the rule is enabled, the default `fail_open: true` behavior emits a low-severity skip finding. To opt out entirely, disable the rule:

```json
{ "rules": [{ "id": "INSTRUCTIONS_COMPLIANCE_001", "enabled": false }] }
```

Tunable knobs (defaults shown):

```json
{
  "id": "INSTRUCTIONS_COMPLIANCE_001",
  "model": "claude-sonnet-4-6",
  "max_tokens": 4096,
  "max_diff_chars": 200000,
  "timeout_seconds": 120,
  "max_retries": 2,
  "fail_open": true,
  "fail_open_severity": "low"
}
```

`max_diff_chars` is a soft circuit-breaker — diffs larger than this skip the LLM call and emit a single low-severity bail finding instead. Raise it for legitimately large refactors, lower it to keep costs predictable.

`fail_open: true` means missing API keys and Anthropic API/network/JSON response failures are reported as low-severity findings instead of aborting the whole run. Set it to `false` in strict repos if LLM review availability must block merges.

The rule's `severity` is a **ceiling**, not a default. The LLM may downgrade an individual finding (e.g. emit `low` under a `medium` rule) but cannot escalate past it. Configure the rule at the highest severity you ever want it to surface at.

For a real merge gate, use one of these strict configurations:

```json
{ "rules": [{ "id": "INSTRUCTIONS_COMPLIANCE_001", "fail_open": false }] }
```

or:

```json
{ "rules": [{ "id": "INSTRUCTIONS_COMPLIANCE_001", "fail_open_severity": "high" }] }
```

Skipped findings still pass through the `fail-on` gate by severity. The default `fail_open_severity: low` keeps ordinary fail-open skips advisory when `fail-on: medium`; setting it to `high` intentionally blocks merges when the LLM check cannot safely run.

The default `fail-on: medium` blocks default LLM instruction violations. Use `fail-on: high` only if you want instruction findings below high severity to remain advisory.

**Cost order of magnitude:** with caching, a typical PR (≤ 50K-char diff, ~5K-token instructions) costs roughly a few cents per run on Sonnet 4.6 — first run in the cache window pays full input price; subsequent runs within ~5 minutes hit the cache.

The report header includes an `LLM tokens:` line summing input/output/cache-read/cache-create across all per-scope calls, so you can verify both cost and that the prompt cache is actually working. The same numbers are emitted as a `kind: "diagnostic"` finding with `metadata.usage` in the JSON report (`--json-path`); diagnostics never count toward the `fail-on` threshold.

### JSON output (`--json-path`)

Findings in the `--json-path` file include a `kind` field that JSON consumers should branch on:

- `"violation"` — a real finding. Counts toward the `fail-on` gate by severity.
- `"skipped"` — the rule could not run (fail-open: missing API key, oversize diff, possible secret in the payload, Anthropic error). Surfaced so the run is not silently empty; counts toward the `fail-on` gate by severity, which lets strict repos use `fail_open_severity: high`.
- `"diagnostic"` — observability records such as LLM token usage. Filtered out of severity counts, the by-severity table, and the `fail-on` gate.

Filter on `kind == "violation"` if you only want real findings.

## Customizing rules

Rules merge **by id**. To tweak the bundled rule's config or register a custom rule, drop this file into the consumer repo at `.github/instruction-rules.json`:

```json
{
  "rules": [
    { "id": "INSTRUCTIONS_COMPLIANCE_001", "max_diff_chars": 400000, "fail_open": false },
    {
      "id": "TEAM_001",
      "enabled": true,
      "severity": "low",
      "description": "Custom rule implemented in .github/instruction_checks.py."
    }
  ]
}
```

Fields you don't set inherit from the matching bundled default — so `{ "id": "INSTRUCTIONS_COMPLIANCE_001", "max_diff_chars": 400000 }` keeps the bundled `severity`, `model`, etc. Unknown ids are appended as a fresh rule (no default to inherit from) and reported as warnings unless a custom checks module registers an implementation; an unknown id with only `{ "id": "TEAM_001" }` defaults to `enabled: true`, `severity: medium`.

You can also pass `rules:` explicitly if a project keeps rule overrides somewhere else.

### Custom checks

Custom checks are normal Python functions registered with `reviewer.checks.register()`. Put this in a consumer repo, for example at `.github/instruction_checks.py`:

```python
from reviewer.checks import Finding, register


@register("TEAM_001")
def check_team_rule(rule, diff, commits, instructions):
    if any(file.path == "danger.txt" for file in diff.files):
        return [Finding(rule.id, rule.severity, "danger.txt should not change.", "danger.txt")]
    return []
```

Then wire it into the workflow and rules:

```yaml
- uses: infinum/instruction-reviewer@v0
  with:
    checks-module: .github/instruction_checks.py
```

```json
{ "rules": [{ "id": "TEAM_001", "severity": "high" }] }
```

Repo-relative custom check files are loaded from the PR base ref, not from the PR head, so a PR cannot weaken its own configured custom checks. Absolute paths remain trusted local escape hatches for CLI usage, and importable module names are resolved outside the reviewed repo when a base ref is active.

## Rollout guidance

- Start with the default `fail-on: medium` when `anthropic-api-key` is configured and instruction compliance should block merges.
- Use `fail-on: high` during early rollout if you want only high-severity findings to fail builds.
- Keep low findings visible in the PR report while teams tune false positives.
- Watch the `LLM compliance:` line in the report header to confirm the load-bearing rule is actually running. `skipped — …` over multiple PRs in a row means a config/secret problem, not a clean run.

### Fork PRs and `pull_request_target`

The default `pull_request` event does **not** expose secrets to PRs from forks, so the LLM check fail-opens on those PRs. If you want the check to run on fork PRs, switch to `pull_request_target` — but be aware:

- `pull_request_target` runs in the context of the **base** repo, so `actions/checkout@v4` defaults to checking out the base ref. The diff would compute against itself and find nothing. You **must** check out `head.sha` explicitly:
  ```yaml
  - uses: actions/checkout@v4
    with:
      ref: ${{ github.event.pull_request.head.sha }}
      fetch-depth: 0
  ```
- Doing this means the workflow executes against a checkout from a fork PR with secret access. Treat the workflow file itself as the trust boundary: do not run untrusted scripts (test runners, build scripts, lint hooks the PR author can edit) in the same job. The reviewer disables `checks-module` on `pull_request_target` because custom checks are Python loaded from the checkout. The bundled LLM compliance rule still runs.

## Running locally

```sh
pip install -e .
python -m reviewer \
  --base-ref main \
  --head-ref HEAD \
  --repo-root . \
  --report-path /tmp/review.md
```

The CLI exits non-zero if any finding is at or above `--fail-on` (default `medium`).

## Repo layout

```
.
├── action.yml                       # the public action contract
├── reviewer/
│   ├── __init__.py
│   ├── __main__.py                  # `python -m reviewer`
│   ├── cli.py                       # argparse + orchestration
│   ├── checks.py                    # Finding, severity gate, secret-pattern helpers, registration
│   ├── llm_check.py                 # INSTRUCTIONS_COMPLIANCE_001 (the only registered rule)
│   ├── diff.py                      # build_pr_diff(base, head) via git
│   ├── rules.py                     # load + merge by id
│   ├── default-rules.json           # bundled defaults
│   └── reporters/
│       ├── markdown.py              # the report
│       ├── github.py                # step summary, annotations, sticky comment, $GITHUB_OUTPUT
│       └── json_report.py           # machine-readable output
├── tests/
└── pyproject.toml
```

## Versioning

- Pin to `@v0` while the project is pre-1.0. The `vX` major-version tag is updated automatically by the release workflow on every `vX.Y.Z` push.
- Default rules are versioned with the action. While the project is pre-1.0, breaking default-rule changes ship as minor releases and are documented in the changelog; after 1.0, they require a major-version bump.
