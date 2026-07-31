"""Load and validate arm YAML specs from the arms/ directory.

Schema (agreed with the conductor, see PLAN-tools.md §3):

Required top-level keys: name, tier ("p" or "f"), hypothesis, primary_metric,
install (list of steps), fired_check (dict).

Each install step is a dict with a required `kind` in KNOWN_INSTALL_KINDS,
plus any number of free-form extra keys.

fired_check is a dict with a required `kind` in KNOWN_FIRED_CHECK_KINDS,
plus free-form extras.

Optional top-level keys: setup_cost_tracked (bool), notes (str), hint (str).

baseline.yaml is special-cased: it only needs `name: baseline` and `notes`;
everything else is skipped.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

KNOWN_TIERS = {"p", "f"}
KNOWN_INSTALL_KINDS = {
    "cfg_skill",
    "repo_artifacts",
    "path_binary",
    "mcp",
    "hook",
    "claude_md_hint",
}
KNOWN_FIRED_CHECK_KINDS = {
    "otel_skill_activated",
    "tool_result_path",
    "bash_calls_match",
    "command_delta",
    "make_check",
}

REQUIRED_KEYS = ("name", "tier", "hypothesis", "primary_metric", "install", "fired_check")


@dataclass
class Arm:
    name: str
    tier: str
    primary_metric: str
    path: Path


def _errors_for_install_step(prefix: str, step: object) -> list[str]:
    if not isinstance(step, dict):
        return [f"{prefix}: install step must be a mapping, got {type(step).__name__}"]
    kind = step.get("kind")
    if kind is None:
        return [f"{prefix}: install step missing 'kind'"]
    if kind not in KNOWN_INSTALL_KINDS:
        return [f"{prefix}: install step has unknown kind '{kind}' (expected one of {sorted(KNOWN_INSTALL_KINDS)})"]
    return []


def _errors_for_fired_check(prefix: str, fired_check: object) -> list[str]:
    if not isinstance(fired_check, dict):
        return [f"{prefix}: fired_check must be a mapping, got {type(fired_check).__name__}"]
    kind = fired_check.get("kind")
    if kind is None:
        return [f"{prefix}: fired_check missing 'kind'"]
    if kind not in KNOWN_FIRED_CHECK_KINDS:
        return [f"{prefix}: fired_check has unknown kind '{kind}' (expected one of {sorted(KNOWN_FIRED_CHECK_KINDS)})"]
    return []


def validate_arm_doc(stem: str, doc: object) -> list[str]:
    """Return a list of validation error strings for a single parsed arm doc."""
    prefix = f"{stem}.yaml"
    if not isinstance(doc, dict):
        return [f"{prefix}: top-level content must be a mapping, got {type(doc).__name__}"]

    if stem == "baseline":
        errors = []
        if doc.get("name") != "baseline":
            errors.append(f"{prefix}: baseline.yaml must have name: baseline")
        if "notes" not in doc:
            errors.append(f"{prefix}: baseline.yaml must have a notes key")
        return errors

    errors = []
    for key in REQUIRED_KEYS:
        if key not in doc:
            errors.append(f"{prefix}: missing required key '{key}'")

    if "name" in doc and doc["name"] != stem:
        errors.append(f"{prefix}: name '{doc['name']}' must equal filename stem '{stem}'")

    if "tier" in doc and doc["tier"] not in KNOWN_TIERS:
        errors.append(f"{prefix}: tier '{doc['tier']}' must be one of {sorted(KNOWN_TIERS)}")

    if "install" in doc:
        install = doc["install"]
        if not isinstance(install, list):
            errors.append(f"{prefix}: install must be a list, got {type(install).__name__}")
        else:
            for i, step in enumerate(install):
                errors.extend(_errors_for_install_step(f"{prefix} install[{i}]", step))

    if "fired_check" in doc:
        errors.extend(_errors_for_fired_check(prefix, doc["fired_check"]))

    if "setup_cost_tracked" in doc and not isinstance(doc["setup_cost_tracked"], bool):
        errors.append(f"{prefix}: setup_cost_tracked must be a bool")

    return errors


def _arm_files(arms_dir: Path) -> list[Path]:
    if not arms_dir.is_dir():
        return []
    return sorted(arms_dir.glob("*.yaml"))


def load_all(arms_dir: Path) -> tuple[list[Arm], list[str]]:
    """Parse and validate every arms/*.yaml file.

    Returns (arms, errors). arms only contains successfully-parsed,
    valid (non-baseline) entries; errors is a flat list of
    "arms/<file>: <problem>" strings.
    """
    arms: list[Arm] = []
    errors: list[str] = []

    for path in _arm_files(arms_dir):
        stem = path.stem
        try:
            doc = yaml.safe_load(path.read_text())
        except yaml.YAMLError as exc:
            errors.append(f"arms/{path.name}: invalid YAML ({exc})")
            continue

        doc_errors = validate_arm_doc(stem, doc)
        if doc_errors:
            errors.extend(f"arms/{e}" if not e.startswith("arms/") else e for e in doc_errors)
            continue

        if stem == "baseline":
            continue

        arms.append(
            Arm(
                name=doc["name"],
                tier=doc["tier"],
                primary_metric=doc["primary_metric"],
                path=path,
            )
        )

    return arms, errors


def cmd_validate(arms_dir: Path) -> int:
    _arms, errors = load_all(arms_dir)
    if errors:
        for e in errors:
            print(e)
        return 1
    print(f"ok: {len(_arms)} arm(s) valid")
    return 0


def cmd_list(arms_dir: Path) -> int:
    arms, errors = load_all(arms_dir)
    for e in errors:
        print(e, file=sys.stderr)

    if not arms:
        print("no arms found")
        return 0

    name_w = max(len("name"), *(len(a.name) for a in arms))
    tier_w = max(len("tier"), *(len(a.tier) for a in arms))
    header = f"{'name':<{name_w}}  {'tier':<{tier_w}}  primary_metric"
    print(header)
    for a in sorted(arms, key=lambda a: a.name):
        print(f"{a.name:<{name_w}}  {a.tier:<{tier_w}}  {a.primary_metric}")
    return 0
