"""L8: components designated by ID_OF_LABEL become requirements on a component number or label."""

import logging
from pathlib import Path

import pytest

from core.ComponentRequirement import ComponentTarget, RequirementKind
from core.WeiDUTp2Parser import Component, WeiDUTp2, WeiDUTp2Parser
from util.mod_sync.mergers.definition_merger import DefinitionMerger
from util.mod_sync.sources.tp2_source import TP2Source

REQUIRED = RequirementKind.REQUIRED
FORBIDDEN = RequirementKind.FORBIDDEN
OPTIONAL = RequirementKind.OPTIONAL

DEMO_HEADER = "BACKUP ~demo/backup~\nAUTHOR ~author~\n\n"

MAIN_COMPONENT = (
    "BEGIN @1 DESIGNATED 0 LABEL ~demo-main~\nCOPY_EXISTING ~sw1h01.itm~ ~override~\n"
)


def parse(tmp_path: Path, components: str) -> WeiDUTp2:
    content = DEMO_HEADER + components
    return WeiDUTp2Parser(tmp_path).parse_string(content, "setup-demo", None, tmp_path)


def component(tp2: WeiDUTp2, designated: str) -> Component:
    return next(item for item in tp2.installable_components() if item.designated == designated)


def summary(item: Component) -> list[tuple[RequirementKind, list[ComponentTarget]]]:
    return [
        (requirement.kind, list(requirement.alternatives)) for requirement in item.requirements
    ]


def label_of(tp2: str, label: str) -> str:
    return f"(ID_OF_LABEL ~{tp2}~ ~{label}~)"


@pytest.mark.parametrize(
    "predicate",
    [
        f"MOD_IS_INSTALLED ~setup-demo.tp2~ {label_of('setup-demo.tp2', 'demo-main')} @2",
        "MOD_IS_INSTALLED ~demo.tp2~ ( ID_OF_LABEL ~demo/setup-demo.tp2~ demo-main ) @2",
        "MOD_IS_INSTALLED ~demo.tp2~ ID_OF_LABEL ~demo.tp2~ ~demo-main~ @2",
    ],
)
def test_label_of_the_mod_in_a_predicate_names_the_component_that_declares_it(
    tmp_path: Path, predicate: str
) -> None:
    tp2 = parse(
        tmp_path, MAIN_COMPONENT + f"BEGIN @3 DESIGNATED 5\nREQUIRE_PREDICATE {predicate}\n"
    )

    requirement = component(tp2, "5").requirements[0]

    assert (requirement.kind, requirement.alternatives) == (
        REQUIRED,
        (ComponentTarget("demo", "0"),),
    )
    assert requirement.text_ref == "2"


def test_label_of_a_choice_of_a_muc_names_that_choice(tmp_path: Path) -> None:
    tp2 = parse(
        tmp_path,
        "BEGIN @1 DESIGNATED 1 LABEL ~demo-combat~ SUBCOMPONENT @10\n"
        "BEGIN @2 DESIGNATED 2 LABEL ~demo-no-combat~ SUBCOMPONENT @10\n"
        "BEGIN @3 DESIGNATED 5\n"
        f"REQUIRE_PREDICATE (MOD_IS_INSTALLED ~demo.tp2~ {label_of('demo.tp2', 'demo-no-combat')}\n"
        f"  OR MOD_IS_INSTALLED ~demo.tp2~ {label_of('demo.tp2', 'demo-combat')}) @4\n"
        f"REQUIRE_PREDICATE NOT MOD_IS_INSTALLED ~demo.tp2~ {label_of('demo.tp2', 'demo-main')}\n"
        + MAIN_COMPONENT.replace("DESIGNATED 0", "DESIGNATED 6"),
    )

    assert summary(component(tp2, "5")) == [
        (REQUIRED, [ComponentTarget("demo", "2"), ComponentTarget("demo", "1")]),
        (FORBIDDEN, [ComponentTarget("demo", "6")]),
    ]


def test_label_of_the_mod_in_the_body_names_the_component_that_declares_it(
    tmp_path: Path,
) -> None:
    tp2 = parse(
        tmp_path,
        MAIN_COMPONENT
        + "BEGIN @3 DESIGNATED 5\n"
        + f"ACTION_IF MOD_IS_INSTALLED ~demo.tp2~ {label_of('demo.tp2', 'demo-main')} BEGIN\n"
        + "  PRINT ~adapted~\nEND\n",
    )

    assert summary(component(tp2, "5")) == [(OPTIONAL, [ComponentTarget("demo", "0")])]


def test_body_label_on_a_component_the_header_requires_is_not_repeated(tmp_path: Path) -> None:
    tp2 = parse(
        tmp_path,
        MAIN_COMPONENT
        + "BEGIN @3 DESIGNATED 5 REQUIRE_COMPONENT ~demo.tp2~ ~0~ @2\n"
        + f"ACTION_IF MOD_IS_INSTALLED ~demo.tp2~ {label_of('demo.tp2', 'demo-main')} BEGIN\n"
        + "  PRINT ~adapted~\nEND\n",
    )

    assert summary(component(tp2, "5")) == [(REQUIRED, [ComponentTarget("demo", "0")])]


def test_label_no_component_declares_skips_the_requirement(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING)

    tp2 = parse(
        tmp_path,
        MAIN_COMPONENT
        + "BEGIN @3 DESIGNATED 5\n"
        + f"REQUIRE_PREDICATE MOD_IS_INSTALLED ~demo.tp2~ {label_of('demo.tp2', 'demo-lost')}\n"
        + "  OR MOD_IS_INSTALLED ~other.tp2~ ~3~\n",
    )

    assert component(tp2, "5").requirements == []
    assert "demo-lost" in caplog.text


def test_label_of_another_mod_is_kept_for_the_catalogue(tmp_path: Path) -> None:
    tp2 = parse(
        tmp_path,
        MAIN_COMPONENT
        + "BEGIN @3 DESIGNATED 5\n"
        + f"REQUIRE_PREDICATE MOD_IS_INSTALLED ~bgt.tp2~ {label_of('bgt.tp2', 'A6-BGT-Music')}\n"
        + f"ACTION_IF MOD_IS_INSTALLED ~tnt.tp2~ {label_of('tnt.tp2', 'g_tnt_core')} BEGIN\n"
        + "  PRINT ~adapted~\nEND\n",
    )

    assert summary(component(tp2, "5")) == [
        (REQUIRED, [ComponentTarget("bgt", label="A6-BGT-Music")]),
        (OPTIONAL, [ComponentTarget("tnt", label="g_tnt_core")]),
    ]


def test_label_of_a_tp2_other_than_the_installed_one_is_skipped(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING)

    tp2 = parse(
        tmp_path,
        MAIN_COMPONENT
        + "BEGIN @3 DESIGNATED 5\n"
        + f"REQUIRE_PREDICATE MOD_IS_INSTALLED ~bgt.tp2~ {label_of('demo.tp2', 'demo-main')}\n"
        + f"ACTION_IF MOD_IS_INSTALLED ~tnt.tp2~ {label_of('demo.tp2', 'demo-main')} BEGIN\n"
        + "  PRINT ~adapted~\nEND\n",
    )

    assert component(tp2, "5").requirements == []
    assert "REQUIRE_PREDICATE" in caplog.text
    assert "unresolved MOD_IS_INSTALLED" in caplog.text


def test_labels_reach_the_component_definitions(tmp_path: Path) -> None:
    (tmp_path / "demo").mkdir()
    (tmp_path / "demo" / "demo.tp2").write_text(
        DEMO_HEADER
        + MAIN_COMPONENT
        + "BEGIN @2 DESIGNATED 1 LABEL ~demo-combat~ SUBCOMPONENT @10\n"
        + "BEGIN @3 DESIGNATED 2 SUBCOMPONENT @10\n"
        + "BEGIN @4 DESIGNATED 3\n",
        encoding="utf-8",
    )

    extracted = TP2Source(tmp_path).extract("demo")

    assert extracted is not None
    assert extracted["components"] == {
        "0": {"type": "std", "label": "demo-main", "games": None},
        "choice_0": {
            "type": "muc",
            "components": ["1", "2"],
            "labels": {"1": "demo-combat"},
            "games": None,
        },
        "3": {"type": "std", "games": None},
    }


def test_stored_labels_follow_the_tp2() -> None:
    existing = {
        "components": {
            "0": {"type": "std", "label": "demo-old", "category": "tweak"},
            "3": {"type": "std", "label": "demo-removed"},
            "choice_0": {"type": "muc", "components": ["1", "2"], "labels": {"1": "demo-old"}},
        }
    }
    tp2 = {
        "components": {
            "0": {"type": "std", "label": "demo-main"},
            "3": {"type": "std"},
            "choice_0": {"type": "muc", "components": ["1", "2"], "labels": {"2": "demo-two"}},
        }
    }
    merger = DefinitionMerger("demo")
    merger.add_source("tp2", tp2)

    merged = merger.merge(existing)["components"]

    assert merged["0"] == {"type": "std", "label": "demo-main", "category": "tweak"}
    assert merged["3"] == {"type": "std"}
    assert merged["choice_0"]["labels"] == {"2": "demo-two"}
