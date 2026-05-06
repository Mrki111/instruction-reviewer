from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

KNOWN_FIELDS = {"id", "enabled", "severity", "description"}
VALID_SEVERITIES = ("low", "medium", "high")


@dataclass
class Rule:
    id: str
    enabled: bool = True
    severity: str = "medium"
    description: str = ""
    config: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Rule":
        if not isinstance(d, dict):
            raise ValueError(f"Rule entries must be objects, got {type(d).__name__}.")
        if "id" not in d:
            raise ValueError(f"Rule missing required 'id' field: {d!r}")
        rid = d["id"]
        if not isinstance(rid, str) or not rid.strip():
            raise ValueError(f"Rule id must be a non-empty string: {d!r}")
        severity = d.get("severity", "medium")
        if severity not in VALID_SEVERITIES:
            raise ValueError(
                f"Rule {rid} has invalid severity {severity!r}; "
                f"expected one of {VALID_SEVERITIES}."
            )
        enabled = _parse_bool(d.get("enabled", True), f"Rule {rid}.enabled")
        description = d.get("description", "")
        if not isinstance(description, str):
            raise ValueError(f"Rule {rid}.description must be a string.")
        return cls(
            id=rid,
            enabled=enabled,
            severity=severity,
            description=description,
            config={k: v for k, v in d.items() if k not in KNOWN_FIELDS},
        )


def _parse_bool(value: Any, label: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    raise ValueError(f"{label} must be a boolean.")


def _read_rules_text(text: str, label: str) -> list[dict[str, Any]]:
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"{label}: expected a top-level object.")
    rules = data.get("rules")
    if not isinstance(rules, list):
        raise ValueError(f"{label}: expected a top-level 'rules' array.")
    for rule in rules:
        if not isinstance(rule, dict):
            raise ValueError(f"{label}: every rule entry must be an object.")
    return rules


def _read_rules_file(path: Path) -> list[dict[str, Any]]:
    return _read_rules_text(path.read_text(encoding="utf-8"), str(path))


def load_rules(default_path: Path, user_path: Path | None) -> list[Rule]:
    user_rules = _read_rules_file(user_path) if user_path is not None else None
    return load_rules_from_entries(_read_rules_file(default_path), user_rules)


def load_rules_from_entries(
    default_rules: list[dict[str, Any]],
    user_rules: list[dict[str, Any]] | None = None,
) -> list[Rule]:
    """Load default rules and (optionally) merge user rules on top by id.

    User-provided fields override default fields for matching ids.
    Unknown ids in the user file are appended as new rules.
    """
    merged: dict[str, dict[str, Any]] = {}
    for r in default_rules:
        rule = Rule.from_dict(r)
        merged[rule.id] = dict(r)

    if user_rules is not None:
        for r in user_rules:
            rule = Rule.from_dict(r)
            rid = rule.id
            if rid in merged:
                merged[rid] = {**merged[rid], **r}
            else:
                merged[rid] = dict(r)

    return [Rule.from_dict(d) for d in merged.values()]


def load_rules_from_texts(
    default_path: Path, user_text: str | None, user_label: str
) -> list[Rule]:
    user_rules = (
        _read_rules_text(user_text, user_label)
        if user_text is not None
        else None
    )
    return load_rules_from_entries(_read_rules_file(default_path), user_rules)
