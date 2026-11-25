"""Remove from data/rules the dependencies and incompatibilities the mod definitions already state.

A rule is expanded into atoms:
- dependency: (source component, alternatives of one target group)
- incompatibility: unordered pair of components

An atom is covered by a definition rule with the same atom, the same severity
and, for a dependency, not optional. A fully covered rule is removed; a
partially covered rule is rewritten with its uncovered part only, when it can
be written exactly. Run from the project root after a synchronization:

    python -m util.dedupe_rules [--dry-run]

The report of removed, reduced and kept rules is written to var/dev/.
"""

from __future__ import annotations

import collections
import copy
import glob
import json
from pathlib import Path
import sys

from core.RuleManager import GroupOperator, RuleExpression, SideExpression

DEFAULT_SEVERITY = "error"
REPORT_PATH = Path("var/dev/dedupe-rules-report.txt")


# ---------------------------------------------------------------- notation


def side_refs(side: SideExpression) -> list[tuple[str, str]]:
    """(mod as written, component key) of an OR side, '*' for (-)."""
    return [
        (cs.mod_id, key)
        for cs in side.component_sets
        for key in (cs.component_keys if cs.component_keys is not None else ("*",))
    ]


def ref_id(ref: tuple[str, str]) -> str:
    return f"{ref[0].lower()}:{ref[1]}"


def write_side(refs: list[tuple[str, str]]) -> str:
    by_mod: dict[str, list[str]] = {}
    for mod, key in refs:
        by_mod.setdefault(mod, []).append(key)
    return "|".join(
        f"{mod}({'-' if keys == ['*'] else ','.join(keys)})" for mod, keys in by_mod.items()
    )


def is_or_side(side: SideExpression) -> bool:
    return side.operator == GroupOperator.OR or len(side.component_sets) == 1


# ---------------------------------------------------------------- atoms


def dependency_target_groups(rule: dict, target: SideExpression) -> list[frozenset[str]]:
    if is_or_side(target):
        groups = [frozenset(ref_id(r) for r in side_refs(target))]
    else:
        groups = [
            frozenset(str(ref) for ref in cs.get_references()) for cs in target.component_sets
        ]
    if rule.get("mode", "any") == "all":
        groups = [frozenset((ref,)) for group in groups for ref in group]
    return groups


def dependency_atoms(
    rule: dict,
) -> dict[tuple[str, str], list[tuple[str, frozenset[str]]]] | None:
    """Atoms of each source ref, or None when the source side is an AND."""
    source, target = RuleExpression.parse(rule["rule"]).sides
    if not is_or_side(source):
        return None
    groups = dependency_target_groups(rule, target)
    return {ref: [(ref_id(ref), group) for group in groups] for ref in side_refs(source)}


def incompatibility_pairs(rule: dict) -> list[list[tuple[str, str]]] | None:
    sides = RuleExpression.parse(rule["rule"]).sides
    if not all(is_or_side(side) for side in sides):
        return None
    return [side_refs(side) for side in sides]


def pair(a: tuple[str, str], b: tuple[str, str]) -> frozenset[str]:
    return frozenset((ref_id(a), ref_id(b)))


# ---------------------------------------------------------------- definition index


def index_definitions() -> tuple[dict, dict]:
    dependencies: dict = collections.defaultdict(list)
    incompatibilities: dict = collections.defaultdict(list)
    for path in sorted(glob.glob("data/mods/*.json")):
        rules = json.loads(Path(path).read_text(encoding="utf-8")).get("rules") or {}
        for rule in rules.get("dependencies", []):
            atoms = dependency_atoms(rule)
            for ref_atoms in (atoms or {}).values():
                for atom in ref_atoms:
                    dependencies[atom].append((Path(path).name, rule))
        for rule in rules.get("incompatibilities", []):
            sides = incompatibility_pairs(rule)
            if sides is None:
                continue
            for i in range(len(sides)):
                for j in range(i + 1, len(sides)):
                    for a in sides[i]:
                        for b in sides[j]:
                            incompatibilities[pair(a, b)].append((Path(path).name, rule))
    return dependencies, incompatibilities


def covered(index: dict, atom, severity: str) -> bool:
    return any(
        rule.get("severity", DEFAULT_SEVERITY) == severity and not rule.get("optional")
        for _, rule in index.get(atom, [])
    )


def mismatched(index: dict, atom, severity: str) -> bool:
    return bool(index.get(atom)) and not covered(index, atom, severity)


# ---------------------------------------------------------------- reduction


def reduce_dependency(rule: dict, index: dict, report: list[str]) -> list[dict]:
    atoms = dependency_atoms(rule)
    if atoms is None:
        return [rule]
    severity = rule.get("severity", DEFAULT_SEVERITY)
    source, target = rule["rule"].split(":", 1)

    kept_sources = []
    partially = []
    for ref, ref_atoms in atoms.items():
        states = [covered(index, atom, severity) for atom in ref_atoms]
        if not all(states):
            kept_sources.append(ref)
            if any(states):
                partially.append(ref_id(ref))
    if any(
        mismatched(index, atom, severity) for ref_atoms in atoms.values() for atom in ref_atoms
    ):
        report.append(f"  severity/optional differs, kept: {rule['rule']}")
    if partially:
        report.append(f"  target groups partly covered, kept for {partially}: {rule['rule']}")

    if len(kept_sources) == len(atoms):
        return [rule]
    if not kept_sources:
        report.append(f"- removed: {rule['rule']}")
        return []

    reduced = copy.deepcopy(rule)
    reduced["rule"] = f"{write_side(kept_sources)}:{target}"
    report.append(f"~ reduced: {rule['rule']}\n        -> {reduced['rule']}")
    return [reduced]


def reduce_incompatibility(rule: dict, index: dict, report: list[str]) -> list[dict]:
    sides = incompatibility_pairs(rule)
    if sides is None:
        return [rule]
    severity = rule.get("severity", DEFAULT_SEVERITY)

    all_pairs = [
        (a, b)
        for i in range(len(sides))
        for j in range(i + 1, len(sides))
        for a in sides[i]
        for b in sides[j]
    ]
    if any(mismatched(index, pair(a, b), severity) for a, b in all_pairs):
        report.append(f"  severity differs, those pairs kept: {rule['rule']}")
    uncovered = [(a, b) for a, b in all_pairs if not covered(index, pair(a, b), severity)]

    if len(uncovered) == len(all_pairs):
        return [rule]
    if not uncovered:
        report.append(f"- removed: {rule['rule']}")
        return []
    if len(sides) != 2:
        report.append(f"  partly covered, more than 2 sides, kept: {rule['rule']}")
        return [rule]

    # Exact rewrite: group the components of one side by their uncovered partners
    decompositions = []
    for flip in (False, True):
        partners: dict[tuple[str, str], list[tuple[str, str]]] = {}
        for a, b in uncovered:
            key, other = (b, a) if flip else (a, b)
            partners.setdefault(key, []).append(other)
        groups: dict[tuple, list[tuple[str, str]]] = {}
        for key, others in partners.items():
            groups.setdefault(tuple(others), []).append(key)
        decompositions.append(
            [
                (list(others), keys) if flip else (keys, list(others))
                for others, keys in groups.items()
            ]
        )
    decomposition = min(decompositions, key=len)

    reduced_rules = []
    for left, right in decomposition:
        reduced = copy.deepcopy(rule)
        reduced["rule"] = f"{write_side(left)}:{write_side(right)}"
        reduced_rules.append(reduced)
    written = "\n        -> ".join(r["rule"] for r in reduced_rules)
    report.append(f"~ reduced: {rule['rule']}\n        -> {written}")
    return reduced_rules


# ---------------------------------------------------------------- files


def process(name: str, reducer, index: dict, dry_run: bool, report: list[str]) -> None:
    path = Path(f"data/rules/{name}.json")
    document = json.loads(path.read_text(encoding="utf-8"))
    report.append(f"===== {name} ({len(document['rules'])} rules)")

    rules = []
    for rule in document["rules"]:
        rules.extend(reducer(rule, index, report))
    removed = len(document["rules"]) - len(rules)
    report.append(
        f"===== {name}: {len(document['rules'])} -> {len(rules)} rules ({removed} fewer)\n"
    )

    if not dry_run:
        document["rules"] = rules
        text = json.dumps(document, indent=2, ensure_ascii=False).replace("\n", "\r\n")
        path.write_bytes(text.encode("utf-8"))


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    dependencies, incompatibilities = index_definitions()
    report: list[str] = []
    process("dependencies", reduce_dependency, dependencies, dry_run, report)
    process("incompatibilities", reduce_incompatibility, incompatibilities, dry_run, report)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\n".join(line for line in report if line.startswith("=====")))
    print(f"Report: {REPORT_PATH}")


if __name__ == "__main__":
    main()
