from __future__ import annotations

import json
from pathlib import Path

import pytest

from reviewer.rules import Rule, load_rules


def _write(path: Path, rules: list[dict]) -> None:
    path.write_text(json.dumps({"rules": rules}))


def test_user_overrides_by_id_and_appends_new(tmp_path: Path) -> None:
    default = tmp_path / "default.json"
    user = tmp_path / "user.json"
    _write(
        default,
        [
            {"id": "X", "enabled": True, "severity": "high", "description": "default-x", "max": 10},
            {"id": "Y", "enabled": True, "severity": "low", "description": "default-y"},
        ],
    )
    _write(
        user,
        [
            {"id": "X", "enabled": False},
            {"id": "Z", "enabled": True, "severity": "medium", "description": "z"},
        ],
    )
    rules = load_rules(default, user)
    by_id = {r.id: r for r in rules}

    assert by_id["X"].enabled is False
    assert by_id["X"].severity == "high"  # not overridden
    assert by_id["X"].description == "default-x"  # not overridden
    assert by_id["X"].config == {"max": 10}  # default config preserved

    assert by_id["Y"].enabled is True
    assert by_id["Z"].enabled is True
    assert by_id["Z"].severity == "medium"


def test_user_severity_overrides_default(tmp_path: Path) -> None:
    # The rule's severity acts as the ceiling for LLM findings, so a user
    # raising it from low to high must take effect — otherwise the
    # configured strictness is silently ignored.
    default = tmp_path / "d.json"
    user = tmp_path / "u.json"
    _write(default, [{"id": "X", "severity": "low", "description": "default-x"}])
    _write(user, [{"id": "X", "severity": "high"}])

    rules = load_rules(default, user)

    assert len(rules) == 1
    assert rules[0].severity == "high"
    assert rules[0].description == "default-x"


def test_user_can_replace_config_field(tmp_path: Path) -> None:
    default = tmp_path / "d.json"
    user = tmp_path / "u.json"
    _write(default, [{"id": "X", "patterns": ["a", "b"], "severity": "low"}])
    _write(user, [{"id": "X", "patterns": ["c"]}])
    rules = load_rules(default, user)
    assert rules[0].config["patterns"] == ["c"]
    assert rules[0].severity == "low"


def test_enabled_string_false_is_parsed_as_false(tmp_path: Path) -> None:
    default = tmp_path / "d.json"
    _write(default, [{"id": "X", "enabled": "false"}])
    rules = load_rules(default, None)
    assert rules[0].enabled is False


def test_invalid_enabled_rejected(tmp_path: Path) -> None:
    default = tmp_path / "d.json"
    _write(default, [{"id": "X", "enabled": "sometimes"}])
    with pytest.raises(ValueError, match="enabled"):
        load_rules(default, None)


def test_invalid_severity_rejected(tmp_path: Path) -> None:
    default = tmp_path / "d.json"
    _write(default, [{"id": "X", "severity": "critical"}])
    with pytest.raises(ValueError, match="severity"):
        load_rules(default, None)


def test_no_user_file_returns_defaults(tmp_path: Path) -> None:
    default = tmp_path / "d.json"
    _write(default, [{"id": "A"}, {"id": "B"}])
    rules = load_rules(default, None)
    assert {r.id for r in rules} == {"A", "B"}


def test_rule_from_dict_requires_id() -> None:
    with pytest.raises(ValueError, match="id"):
        Rule.from_dict({"severity": "high"})


def test_rules_file_requires_rule_objects(tmp_path: Path) -> None:
    default = tmp_path / "d.json"
    default.write_text(json.dumps({"rules": ["X"]}))
    with pytest.raises(ValueError, match="object"):
        load_rules(default, None)
