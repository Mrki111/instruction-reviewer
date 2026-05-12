from __future__ import annotations

import html

from reviewer.checks import Finding
from reviewer.diff import Commit, Diff
from reviewer.instructions import InstructionFile
from reviewer.rules import Rule

STICKY_MARKER = "<!-- instruction-reviewer:v1 -->"

LLM_RULE_ID = "INSTRUCTIONS_COMPLIANCE_001"


def render_report(
    findings: list[Finding],
    diff: Diff,
    commits: list[Commit],
    instructions: list[InstructionFile],
    threshold: str,
    rules: list[Rule] | None = None,
) -> str:
    lines: list[str] = []
    lines.append("# Instruction Reviewer report")
    lines.append("")
    lines.append(f"- Base: `{_short(diff.base)}`")
    lines.append(f"- Head: `{_short(diff.head)}`")
    lines.append(f"- Files changed: {len(diff.files)}")
    lines.append(f"- Commits: {len(commits)}")
    visible_findings = [f for f in findings if f.kind == "violation"]
    lines.append(
        f"- Violations: {len(visible_findings)} "
        f"(fail-on threshold: `{threshold}`)"
    )
    llm_status = _llm_status_line(findings, rules, instructions)
    if llm_status:
        lines.append(f"- LLM compliance: {llm_status}")
    llm_tokens = _llm_tokens_line(findings)
    if llm_tokens:
        lines.append(f"- LLM tokens: {llm_tokens}")
    lines.append("")

    countable = [f for f in findings if f.kind != "diagnostic"]
    by_sev: dict[str, list[Finding]] = {"high": [], "medium": [], "low": []}
    for f in countable:
        by_sev.setdefault(f.severity, []).append(f)

    lines.append("| Severity | Count |")
    lines.append("|---|---|")
    for sev in ("high", "medium", "low"):
        lines.append(f"| {sev} | {len(by_sev.get(sev, []))} |")
    lines.append("")

    if countable:
        lines.append("## Findings")
        lines.append("")
        for sev in ("high", "medium", "low"):
            sev_findings = by_sev.get(sev, [])
            if not sev_findings:
                continue
            lines.append(f"### {sev}")
            for f in sev_findings:
                loc = _format_location(f)
                lines.append(f"- **{f.rule_id}**{loc} — {f.message}")
            lines.append("")
    else:
        lines.append("No findings. ✅")
        lines.append("")

    if instructions:
        lines.append("## Instructions referenced")
        for f in instructions:
            lines.append(f"- `{f.path}`")
        lines.append("")
    else:
        lines.append("## Instructions referenced")
        lines.append("")
        lines.append("No `AGENTS.md` or `CLAUDE.md` files were found.")
        lines.append("")

    if commits:
        lines.append("<details><summary>Commits in this diff</summary>")
        lines.append("")
        for c in commits:
            # Subjects are PR-author-controlled and embedded inside a <details>
            # block — HTML-escape so a crafted </details> or stray tag can't
            # break out and mangle the rendered comment.
            lines.append(f"- `{c.sha[:7]}` {html.escape(c.subject, quote=False)}")
        lines.append("")
        lines.append("</details>")
        lines.append("")

    lines.append(STICKY_MARKER)
    return "\n".join(lines)


def _llm_tokens_line(findings: list[Finding]) -> str | None:
    diag = next(
        (
            f
            for f in findings
            if f.kind == "diagnostic"
            and f.rule_id == LLM_RULE_ID
            and f.metadata
            and "usage" in f.metadata
        ),
        None,
    )
    if diag is None:
        return None
    usage = diag.metadata["usage"] if diag.metadata else {}
    fresh_in = int(usage.get("input_tokens") or 0)
    cached = int(usage.get("cache_read_input_tokens") or 0)
    cache_create = int(usage.get("cache_creation_input_tokens") or 0)
    out = int(usage.get("output_tokens") or 0)
    total_in = fresh_in + cached + cache_create
    if total_in <= 0 and out <= 0:
        return None
    if cached + cache_create > 0:
        hit = (cached / (cached + cache_create)) * 100
        return (
            f"{total_in:,} in ({cached:,} cached, {hit:.0f}% hit rate) "
            f"/ {out:,} out"
        )
    return f"{total_in:,} in / {out:,} out"


def _llm_status_line(
    findings: list[Finding],
    rules: list[Rule] | None,
    instructions: list[InstructionFile],
) -> str | None:
    if rules is None:
        return None
    rule = next((r for r in rules if r.id == LLM_RULE_ID), None)
    if rule is None or not rule.enabled:
        return "disabled"
    if not instructions:
        return "not applicable (no instruction files at base)"
    rule_findings = [f for f in findings if f.rule_id == LLM_RULE_ID]
    skipped = [f for f in rule_findings if f.kind == "skipped"]
    violations = [f for f in rule_findings if f.kind == "violation"]
    if skipped and not violations:
        return f"skipped — {skipped[0].message}"
    if skipped and violations:
        return (
            f"ran with {len(violations)} violation(s); "
            f"some scope(s) skipped — {skipped[0].message}"
        )
    if violations:
        return f"ran ({len(violations)} violation(s) reported)"
    return "ran (no violations reported)"


def _format_location(f: Finding) -> str:
    if f.path and f.line:
        return f" (`{f.path}:{f.line}`)"
    if f.path:
        return f" (`{f.path}`)"
    return ""


def _short(ref: str) -> str:
    if len(ref) >= 40 and all(c in "0123456789abcdef" for c in ref.lower()):
        return ref[:7]
    return ref
