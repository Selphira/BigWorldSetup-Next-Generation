"""Validate the complete catalogue before publishing an artifact."""

import argparse
from collections import Counter
from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator

from core.Mod import Mod
from core.RuleManager import RuleExpression
from core.Rules import DependencyRule, IncompatibilityRule, OrderRule

ROOT = Path(__file__).resolve().parents[1]
RULE_SCHEMAS = {
    "dependencies": "rule-dependencies-definition.json",
    "incompatibilities": "rule-incompatibilities-definition.json",
    "order": "rule-order-definition.json",
}


@dataclass(frozen=True)
class Diagnostic:
    """A stable, source-addressable catalogue defect."""

    file: str
    location: str
    code: str
    message: str


@dataclass
class CatalogueReport:
    """Complete validation results; historical defects remain blocking.

    Notices are legitimate situations a maintainer should keep in sight, such
    as an archive that can only be provided by the user; they never block.
    """

    counts: dict[str, int] = field(default_factory=dict)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    notices: list[Diagnostic] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.diagnostics

    def to_dict(self) -> dict[str, Any]:
        return {"valid": self.valid, **asdict(self)}


def validate_catalogue(data_dir: Path, schemas_dir: Path) -> CatalogueReport:
    """Check all files, schemas, rule references and game sequence references.

    Args:
        data_dir: Catalogue root containing mods, games and rules.
        schemas_dir: Directory containing the five authoritative schemas.
    Returns:
        Diagnostics without modifying any catalogue file.
    """
    report = CatalogueReport()
    documents: dict[str, dict[str, Any]] = {}
    mods: dict[str, Mod] = {}

    def add(file: str, location: str, code: str, message: str) -> None:
        report.diagnostics.append(Diagnostic(file, location, code, message))

    def check_archive_source(file: str, mod: Mod) -> None:
        """A mod declares its archive, names the mod providing it, or awaits the user."""
        if mod.provided_by is None:
            if not mod.files:
                report.notices.append(
                    Diagnostic(file, "files", "manual_archive", "Archive provided by the user")
                )
            return

        provider = mods.get(mod.provided_by)
        if provider is None:
            add(file, "provided_by", "missing_mod", mod.provided_by)
        elif provider.provided_by is not None or not provider.files:
            add(file, "provided_by", "provider_without_archive", mod.provided_by)

    for family in ("mods", "games", "rules"):
        files = sorted((data_dir / family).rglob("*.json"))
        report.counts[family] = len(files)
        if not files:
            add(family, "", "missing", "Required catalogue directory is missing or empty")
        for path in files:
            relative = path.relative_to(data_dir).as_posix()
            schema_name = (
                RULE_SCHEMAS.get(path.stem)
                if family == "rules"
                else {
                    "mods": "mod-definition.json",
                    "games": "game-definition.json",
                }[family]
            )
            if schema_name is None:
                add(relative, "", "schema", "No schema registered for this rule file")
                continue
            try:
                schema = json.loads((schemas_dir / schema_name).read_text(encoding="utf-8"))
                Draft7Validator.check_schema(schema)
                document = json.loads(path.read_text(encoding="utf-8"))
                for error in sorted(Draft7Validator(schema).iter_errors(document), key=str):
                    location = "/".join(map(str, error.absolute_path))
                    add(relative, location, "schema", error.message)
                if not isinstance(document, dict):
                    continue
                documents[relative] = document
                if family == "mods":
                    mod_id = path.stem.lower()
                    if mod_id in mods:
                        add(relative, "", "duplicate_mod", mod_id)
                    mods[mod_id] = Mod({**document, "id": mod_id})
            except (OSError, ValueError, TypeError, AttributeError, KeyError) as error:
                add(relative, "", "load", str(error))

    for mod_id, mod in sorted(mods.items()):
        check_archive_source(f"mods/{mod_id}.json", mod)

    for name in RULE_SCHEMAS:
        if f"rules/{name}.json" not in documents:
            add(f"rules/{name}.json", "", "missing", "Required rule file missing or unreadable")

    def check_reference(file: str, location: str, reference: str) -> None:
        mod_id, _, key = reference.partition(":")
        mod = mods.get(mod_id.lower())
        if mod is None:
            add(file, location, "missing_mod", reference)
        elif key not in ("", "*", "-") and mod.get_component(key) is None:
            # UI selections encode SUB answers as component.prompt.option.
            parts = key.split(".")
            component = mod.get_component(parts[0])
            if len(parts) == 3 and component is not None and component.is_sub():
                prompt = component.get_prompt(parts[1])
                if prompt is not None and prompt.has_option(parts[2]):
                    return
            add(file, location, "missing_component", reference)

    for file, document in documents.items():
        if file.startswith("rules/"):
            family = Path(file).stem
            for index, raw in enumerate(document.get("rules", [])):
                location = f"rules/{index}"
                try:
                    expanded = [raw]
                    if "rule" in raw:
                        expression = RuleExpression.parse(raw["rule"])
                        if family == "dependencies":
                            expanded = [expression.to_dependency_rule(raw.copy())]
                        elif family == "order":
                            expanded = [expression.to_order_rule(raw.copy())]
                        else:
                            expanded = expression.to_incompatibility_rules(raw)
                    rule_class = {
                        "dependencies": DependencyRule,
                        "order": OrderRule,
                        "incompatibilities": IncompatibilityRule,
                    }[family]
                    references = set()
                    for entry in expanded:
                        rule = rule_class.from_dict(entry)
                        references.update(str(ref) for ref in (*rule.sources, *rule.targets))
                    for ref in sorted(references):
                        check_reference(file, location, ref)
                except (ValueError, TypeError, AttributeError, KeyError) as error:
                    add(file, location, "rule", str(error))
        elif file.startswith("games/"):
            try:
                for mod_id, keys in document.get("forced_components", {}).items():
                    for key in keys:
                        check_reference(file, "forced_components", f"{mod_id}:{key}")
                for index, sequence in enumerate(document.get("sequences", [])):
                    location = f"sequences/{index}"
                    order = sequence.get("order", [])
                    for ref in order:
                        check_reference(file, f"{location}/order", ref)
                    for ref, count in Counter(order).items():
                        if count > 1:
                            add(file, f"{location}/order", "duplicate_order", ref)
                    for name in ("allowed_mods", "blocked_mods"):
                        for mod_id in sequence.get(name, []):
                            check_reference(file, f"{location}/{name}", mod_id)
                    for mod_id, keys in sequence.get("allowed_components", {}).items():
                        for key in keys:
                            check_reference(
                                file, f"{location}/allowed_components", f"{mod_id}:{key}"
                            )
            except (ValueError, TypeError, AttributeError) as error:
                add(file, "", "game", str(error))

    for diagnostics in (report.diagnostics, report.notices):
        diagnostics.sort(key=lambda item: (item.file, item.location, item.code, item.message))
    return report


def main() -> int:
    """Validate the catalogue and optionally write a machine-readable report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--schemas-dir", type=Path, default=ROOT / "validation" / "schemas")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = validate_catalogue(args.data_dir, args.schemas_dir)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(
        json.dumps(
            {
                "counts": report.counts,
                "errors": len(report.diagnostics),
                "notices": len(report.notices),
            }
        )
    )
    for diagnostic in report.diagnostics:
        print(
            f"{diagnostic.file}:{diagnostic.location}: {diagnostic.code}: {diagnostic.message}"
        )
    for notice in report.notices:
        print(f"notice: {notice.file}:{notice.location}: {notice.code}: {notice.message}")
    return 0 if report.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
