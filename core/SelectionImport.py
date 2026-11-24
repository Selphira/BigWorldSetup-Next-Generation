"""Reconcile saved component identities against the active catalogue."""

from collections import defaultdict
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path

from core.atomic_io import write_json_atomic
from core.ComponentReference import ComponentReference, ReferenceIndexes
from core.ComponentRequirement import normalize_tp2_name
from core.Mod import Mod


@dataclass
class SelectionImport:
    """Resolved identities and intentions requiring a user decision."""

    kept: set[ComponentReference] = field(default_factory=set)
    remapped: dict[str, ComponentReference] = field(default_factory=dict)
    missing: set[str] = field(default_factory=set)
    ambiguous: set[str] = field(default_factory=set)
    unanswered: set[ComponentReference] = field(default_factory=set)

    @staticmethod
    def catalogue_revision(indexes: ReferenceIndexes) -> str:
        """Fingerprint the selectable identities, including SUB and MUC membership."""
        identities = sorted(
            f"{ref}|{indexes.get_parent(ref) or ''}"
            for ref in indexes.mod_component_index.keys() | indexes.parent_index.keys()
        )
        identities.extend(
            sorted(
                f"{mod.id}|{mod.tp2}|{mod.version}"
                for mod in indexes.mod_component_index.values()
                if isinstance(mod, Mod)
            )
        )
        return sha256("\n".join(identities).encode()).hexdigest()

    @classmethod
    def export(cls, path: Path, references: list[str], indexes: ReferenceIndexes) -> None:
        """Write a selection with the catalogue revision it was made against.

        Args:
            path: Destination JSON file
            references: Selected component references, unresolved ones included
            indexes: Active catalogue indexes
        """
        write_json_atomic(
            path,
            {
                "catalogue_revision": cls.catalogue_revision(indexes),
                "components": references,
            },
        )

    @classmethod
    def reconcile(cls, entries: list[str], indexes: ReferenceIndexes) -> "SelectionImport":
        if not isinstance(entries, list) or any(
            not isinstance(entry, str) for entry in entries
        ):
            raise ValueError("Expected a list of component references")
        report = cls()
        groups: dict[ComponentReference, set[ComponentReference]] = defaultdict(set)
        aliases: dict[str, list[str]] = defaultdict(list)
        for mod in indexes.mod_component_index.values():
            if isinstance(mod, Mod) and mod.tp2:
                alias = normalize_tp2_name(mod.tp2)
                aliases[alias].append(mod.id)
        for entry in entries:
            try:
                ref = ComponentReference.from_string(entry)
            except ValueError:
                report.missing.add(entry)
                continue
            if ref not in indexes.parent_index and ref not in indexes.mod_component_index:
                if ComponentReference.for_mod(ref.mod_id) in indexes.mod_component_index:
                    report.missing.add(entry)
                    continue
                alias = normalize_tp2_name(ref.mod_id)
                matches = [
                    ComponentReference(mod_id, ref.comp_key)
                    for mod_id in aliases.get(alias, ())
                ]
                matches = [
                    candidate
                    for candidate in matches
                    if candidate in indexes.mod_component_index
                    or candidate in indexes.parent_index
                ]
                if len(matches) > 1:
                    report.ambiguous.add(entry)
                    continue
                if not matches:
                    report.missing.add(entry)
                    continue
                ref = matches[0]
            if str(ref) != entry:
                report.remapped[entry] = ref
            report.kept.add(ref)
            parent = indexes.get_parent(ref)
            if parent and (parent.is_muc() or parent.is_sub()):
                groups[parent].add(ref)
        for choices in groups.values():
            if len(choices) > 1:
                report.ambiguous.update(map(str, choices))
                report.kept.difference_update(choices)
        for ref in report.kept:
            base = ref.get_base_component_reference()
            for prompt in indexes.get_children(base):
                if prompt.is_sub() and not any(
                    choice in report.kept for choice in indexes.get_children(prompt)
                ):
                    report.unanswered.add(prompt)
        return report
