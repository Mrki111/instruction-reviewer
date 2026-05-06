from __future__ import annotations

import json

from reviewer.checks import Finding
from reviewer.diff import Commit, Diff
from reviewer.instructions import InstructionFile


def render_json(
    findings: list[Finding],
    diff: Diff,
    commits: list[Commit],
    instructions: list[InstructionFile],
    threshold: str,
) -> str:
    payload = {
        "base": diff.base,
        "head": diff.head,
        "merge_base": diff.merge_base,
        "fail_on": threshold,
        "instructions": [f.path for f in instructions],
        "findings": [
            {
                "rule_id": f.rule_id,
                "severity": f.severity,
                "message": f.message,
                "path": f.path,
                "line": f.line,
                "kind": f.kind,
                "metadata": f.metadata,
            }
            for f in findings
        ],
        "files_changed": [
            {
                "path": f.path,
                "status": f.status,
                "additions": f.additions,
                "deletions": f.deletions,
            }
            for f in diff.files
        ],
        "commits": [
            {
                "sha": c.sha,
                "subject": c.subject,
                "body": c.body,
                "files": c.files,
            }
            for c in commits
        ],
    }
    return json.dumps(payload, indent=2)
