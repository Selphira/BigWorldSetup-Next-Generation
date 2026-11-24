"""L8: MOD_IS_INSTALLED in a REQUIRE_PREDICATE becomes component requirements."""

import logging
from pathlib import Path

import pytest

from constants import TP2_PREDICATE_MAX_CLAUSES
from core.ComponentRequirement import (
    ComponentRequirement,
    ComponentTarget,
    RequirementKind,
    RequirementSource,
)
from core.WeiDUTp2Parser import Component, WeiDUTp2Parser

REQUIRED = RequirementKind.REQUIRED
FORBIDDEN = RequirementKind.FORBIDDEN


def parse_component(tmp_path: Path, predicate: str) -> Component:
    content = (
        "BACKUP ~demo/backup~\nAUTHOR ~author~\n\n"
        f"BEGIN @1 DESIGNATED 10\nREQUIRE_PREDICATE {predicate}\n"
        "COPY_EXISTING ~sw1h01.itm~ ~override~\n"
    )
    tp2 = WeiDUTp2Parser(tmp_path).parse_string(content, "demo", None, tmp_path)
    return tp2.components[0]


def summary(component: Component) -> list[tuple[RequirementKind, list[str]]]:
    return [
        (item.kind, [f"{target.tp2}({target.component})" for target in item.alternatives])
        for item in component.requirements
    ]


def mod(tp2: str, number: int) -> str:
    return f"MOD_IS_INSTALLED ~setup-{tp2}.tp2~ ~{number}~"


def test_installed_mod_becomes_a_required_predicate_requirement(tmp_path: Path) -> None:
    component = parse_component(tmp_path, f"{mod('other', 0)} @20")

    assert component.requirements == [
        ComponentRequirement(
            kind=REQUIRED,
            alternatives=(ComponentTarget("other", "0"),),
            source=RequirementSource.PREDICATE,
            text_ref="20",
        )
    ]


def test_mods_joined_by_or_are_alternatives_of_one_requirement(tmp_path: Path) -> None:
    component = parse_component(tmp_path, f"{mod('a', 0)} OR {mod('b', 1)} || {mod('c', 2)}")

    assert summary(component) == [(REQUIRED, ["a(0)", "b(1)", "c(2)"])]


def test_literal_message_after_the_last_mod_is_kept(tmp_path: Path) -> None:
    component = parse_component(tmp_path, f"{mod('a', 0)} AND {mod('b', 1)} ~Needs a and b~")

    assert summary(component) == [(REQUIRED, ["a(0)"]), (REQUIRED, ["b(1)"])]
    assert {item.text for item in component.requirements} == {"Needs a and b"}


@pytest.mark.parametrize(
    "predicate",
    [
        f"NOT {mod('a', 0)} AND !{mod('b', 1)}",
        f"!({mod('a', 0)} OR {mod('b', 1)})",
        f"NOT ({mod('a', 0)} || {mod('b', 1)}) && NOT {mod('a', 0)}",
    ],
)
def test_absent_mods_become_one_forbidden_requirement_each(
    tmp_path: Path, predicate: str
) -> None:
    component = parse_component(tmp_path, f"{predicate} @3")

    assert summary(component) == [(FORBIDDEN, ["a(0)"]), (FORBIDDEN, ["b(1)"])]


def test_nested_expression_is_distributed_into_clauses(tmp_path: Path) -> None:
    component = parse_component(
        tmp_path, f"{mod('a', 0)} AND ({mod('b', 1)} OR ({mod('c', 2)} AND {mod('d', 3)}))"
    )

    assert summary(component) == [
        (REQUIRED, ["a(0)"]),
        (REQUIRED, ["b(1)", "c(2)"]),
        (REQUIRED, ["b(1)", "d(3)"]),
    ]


def test_game_restriction_keeps_the_mod_requirement(tmp_path: Path) -> None:
    component = parse_component(tmp_path, f"GAME_IS ~bg2ee eet~ AND {mod('a', 0)} @3")

    assert component.games == ["bg2ee", "eet"]
    assert summary(component) == [(REQUIRED, ["a(0)"])]


def test_mod_alternative_to_a_game_the_component_excludes_is_kept(tmp_path: Path) -> None:
    component = parse_component(
        tmp_path, f"GAME_IS ~bgee~ AND (GAME_IS ~eet~ OR {mod('bgt', 0)}) @3"
    )

    assert summary(component) == [(REQUIRED, ["bgt(0)"])]


@pytest.mark.parametrize(
    ("predicate", "reason"),
    [
        (f"GAME_IS ~eet~ OR {mod('bgt', 0)}", "requirement depending on the game"),
        (f"GAME_INCLUDES ~sod~ OR {mod('bgt', 0)}", "requirement depending on the game"),
        (
            f"FILE_EXISTS_IN_GAME ~sw1h01.itm~ OR {mod('bgt', 0)}",
            "requirement depending on another condition",
        ),
        (f"NOT ({mod('a', 0)} AND {mod('b', 1)})", "alternative involving an absent mod"),
        (f"{mod('a', 0)} OR NOT {mod('b', 1)}", "alternative involving an absent mod"),
        ("MOD_IS_INSTALLED ~%tp2%~ ~0~", "unresolved MOD_IS_INSTALLED"),
        ("MOD_IS_INSTALLED ~setup-a.tp2~ %component%", "unresolved MOD_IS_INSTALLED"),
        (f"{mod('a', 0)} = 0", "unresolved MOD_IS_INSTALLED"),
    ],
)
def test_conditional_or_untranslatable_clause_is_skipped_and_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, predicate: str, reason: str
) -> None:
    with caplog.at_level(logging.WARNING):
        component = parse_component(tmp_path, f"{predicate} @3")

    assert component.requirements == []
    assert f"demo:10: Skipping {reason}: REQUIRE_PREDICATE" in caplog.text


def test_untranslatable_clause_leaves_the_other_clauses(tmp_path: Path) -> None:
    component = parse_component(
        tmp_path,
        f"MOD_IS_INSTALLED ~%tp2%~ ~0~ AND FILE_EXISTS ~x.2da~ AND {mod('a', 0)} @3",
    )

    assert summary(component) == [(REQUIRED, ["a(0)"])]


def test_predicate_expanding_past_the_clause_limit_is_skipped(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # Each "(x AND y)" doubles the clauses of the disjunction.
    pair_count = TP2_PREDICATE_MAX_CLAUSES.bit_length()
    predicate = " OR ".join(
        f"({mod('a', index)} AND {mod('b', index)})" for index in range(pair_count)
    )

    with caplog.at_level(logging.WARNING):
        component = parse_component(tmp_path, predicate)

    assert component.requirements == []
    assert f"more than {TP2_PREDICATE_MAX_CLAUSES} clauses" in caplog.text


def test_predicate_without_mod_gives_no_requirement_nor_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        component = parse_component(tmp_path, "GAME_IS ~bg2ee eet~ ~Only BG2~")

    assert component.games == ["bg2ee", "eet"]
    assert component.requirements == []
    assert "Skipping" not in caplog.text


def test_predicate_requirements_follow_header_flags_in_order(tmp_path: Path) -> None:
    content = (
        "BACKUP ~demo/backup~\nAUTHOR ~author~\n\n"
        "BEGIN @1 DESIGNATED 10\n"
        "REQUIRE_COMPONENT ~setup-first.tp2~ ~0~ @2\n"
        f"REQUIRE_PREDICATE {mod('second', 1)} @3\n"
        "FORBID_COMPONENT ~setup-third.tp2~ ~2~ @4\n"
    )
    tp2 = WeiDUTp2Parser(tmp_path).parse_string(content, "demo", None, tmp_path)

    requirements = tp2.components[0].requirements

    assert [item.source for item in requirements] == [
        RequirementSource.HEADER,
        RequirementSource.PREDICATE,
        RequirementSource.HEADER,
    ]
    assert summary(tp2.components[0]) == [
        (REQUIRED, ["first(0)"]),
        (REQUIRED, ["second(1)"]),
        (FORBIDDEN, ["third(2)"]),
    ]


def parse_component_with_predicates(tmp_path: Path, *predicates: str) -> Component:
    flags = "".join(f"REQUIRE_PREDICATE {predicate}\n" for predicate in predicates)
    content = f"BEGIN @1 DESIGNATED 10\n{flags}COPY_EXISTING ~sw1h01.itm~ ~override~\n"
    tp2 = WeiDUTp2Parser(tmp_path).parse_string(content, "demo", None, tmp_path)
    return tp2.components[0]


@pytest.mark.parametrize(
    "predicates",
    [
        ("GAME_IS ~bg2ee~ @1", "GAME_IS ~bg2ee eet~ @2"),
        ("GAME_IS ~bg2ee eet~ @1", "GAME_IS ~bg2ee~ @2"),
    ],
    ids=["narrow first", "narrow last"],
)
def test_successive_predicates_restrict_the_component_to_their_common_games(
    tmp_path: Path, predicates: tuple[str, str]
) -> None:
    assert parse_component_with_predicates(tmp_path, *predicates).games == ["bg2ee"]


def test_mod_alternative_to_a_game_another_predicate_excludes_is_kept(tmp_path: Path) -> None:
    component = parse_component_with_predicates(
        tmp_path, "GAME_IS ~bgee~ @1", f"{mod('a', 0)} OR GAME_IS ~bg2ee~ @2"
    )

    assert component.games == ["bgee", "sod"]
    assert summary(component) == [(REQUIRED, ["a(0)"])]
