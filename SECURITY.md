# Security Policy

## Supported versions

This project is pre-1.0. Only the latest `v0.x` release receives security fixes; earlier `v0.x` tags will not be patched. Pin `@v0` (or a specific `vX.Y.Z` tag) in consumer workflows so security fixes propagate on the next release.

## Reporting a vulnerability

Please **do not** open a public GitHub issue for security reports. Email **lazar.mrkic@infinum.com** with:

- A description of the issue and the impact you believe it has.
- Steps to reproduce, including a minimal workflow or input that triggers the behavior.
- Any logs, payloads, or screenshots that help confirm the issue (with secrets redacted).

You should receive an acknowledgement within a few working days. If the report is confirmed, a fix or mitigation will be coordinated before public disclosure.

## In scope

Reports about any of the following are especially welcome, since they map directly to the project's trust boundaries:

- A path that lets a PR relax its own configured rules (e.g. instruction files, `.github/instruction-rules.json`, or a `--checks-module` being read from the PR head instead of the base ref).
- A path that lets `--checks-module` execute under `pull_request_target`.
- A path that lets repo content (diff text, commit messages, instruction files) reach Anthropic without HTML-escaping, enabling prompt injection of new rules.
- A path that allows the pre-flight secret scan to be bypassed so a credential-shaped string is sent to Anthropic.
- A shell-injection sink in `action.yml` or anywhere user inputs are interpolated into a shell context without going through an environment variable.
- A way for a malicious finding payload (rule excerpt, path, line) to break out of the sticky comment, Step Summary, or `--json-path` output.

## Out of scope

- Vulnerabilities that require an attacker to already have write access to the repository running the Action.
- Issues in the upstream Anthropic SDK or GitHub Actions runner — please report those to the respective projects.
- The behavior of the LLM itself (e.g. a false positive or false negative finding). These are quality issues; open a normal GitHub issue.
