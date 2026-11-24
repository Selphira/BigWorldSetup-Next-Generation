"""L8: MOD_IS_INSTALLED in an ACTION_IF or PATCH_IF becomes an optional requirement."""

import logging
from pathlib import Path

import pytest

from core.ComponentRequirement import (
    ComponentRequirement,
    ComponentTarget,
    RequirementKind,
    RequirementSource,
)
from core.WeiDUTp2Parser import Component, WeiDUTp2Parser

REQUIRED = RequirementKind.REQUIRED
FORBIDDEN = RequirementKind.FORBIDDEN
OPTIONAL = RequirementKind.OPTIONAL


def parse_component(tmp_path: Path, header: str, body: str) -> Component:
    content = (
        "BACKUP ~demo/backup~\nAUTHOR ~author~\n\n"
        f"BEGIN @1 DESIGNATED 10\n{header}\n{body}\n"
        "BEGIN @2 DESIGNATED 20\nCOPY_EXISTING ~sw1h02.itm~ ~override~\n"
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


def test_action_if_on_an_installed_mod_becomes_an_optional_body_requirement(
    tmp_path: Path,
) -> None:
    component = parse_component(
        tmp_path, "", f"ACTION_IF {mod('other', 0)} THEN BEGIN\n  PRINT ~adapted~\nEND"
    )

    assert component.requirements == [
        ComponentRequirement(
            kind=OPTIONAL,
            alternatives=(ComponentTarget("other", "0"),),
            source=RequirementSource.BODY,
        )
    ]


@pytest.mark.parametrize(
    "body",
    [
        f"ACTION_IF NOT {mod('a', 0)} THEN BEGIN PRINT ~x~ END",
        f"ACTION_IF !({mod('a', 0)} OR {mod('b', 1)}) BEGIN PRINT ~x~ END",
        f"ACTION_IF GAME_IS ~bg2ee~ AND {mod('a', 0)} = 0 THEN BEGIN END ELSE BEGIN END",
        "COPY_EXISTING ~sw1h01.itm~ ~override~\n"
        f"  PATCH_IF ({mod('a', 0)} && {mod('b', 1)}) BEGIN\n"
        "    WRITE_SHORT 0x20 1\n"
        "  END\n"
        "BUT_ONLY",
    ],
)
def test_every_tested_mod_is_optional_whatever_the_negation(tmp_path: Path, body: str) -> None:
    component = parse_component(tmp_path, "", body)

    assert {kind for kind, _ in summary(component)} == {OPTIONAL}
    assert [targets for _, targets in summary(component)][0] == ["a(0)"]


def test_nested_and_else_conditions_are_read_in_order(tmp_path: Path) -> None:
    component = parse_component(
        tmp_path,
        "",
        f"ACTION_IF {mod('a', 0)} THEN BEGIN\n"
        f"  ACTION_IF {mod('b', 1)} THEN BEGIN PRINT ~ab~ END\n"
        f"END ELSE ACTION_IF {mod('c', 2)} THEN BEGIN\n"
        "  PRINT ~c~\n"
        "END",
    )

    assert summary(component) == [
        (OPTIONAL, ["a(0)"]),
        (OPTIONAL, ["b(1)"]),
        (OPTIONAL, ["c(2)"]),
    ]


def test_target_tested_twice_gives_one_requirement(tmp_path: Path) -> None:
    component = parse_component(
        tmp_path,
        "",
        f"ACTION_IF {mod('a', 0)} THEN BEGIN END\nACTION_IF NOT {mod('A', 0)} THEN BEGIN END",
    )

    assert summary(component) == [(OPTIONAL, ["a(0)"])]


def test_mod_is_installed_outside_a_condition_is_ignored(tmp_path: Path) -> None:
    component = parse_component(
        tmp_path,
        "",
        f"OUTER_SET has_a = {mod('a', 0)}\nACTION_IF has_a THEN BEGIN PRINT ~a~ END",
    )

    assert component.requirements == []


def test_condition_is_read_only_up_to_then(tmp_path: Path) -> None:
    component = parse_component(
        tmp_path, "", f"ACTION_IF 1 THEN BEGIN\n  OUTER_SET x = {mod('a', 0)}\nEND"
    )

    assert component.requirements == []


def test_target_named_by_the_header_is_not_optional(tmp_path: Path) -> None:
    component = parse_component(
        tmp_path,
        f"REQUIRE_COMPONENT ~setup-a.tp2~ ~0~ @5\nREQUIRE_PREDICATE NOT {mod('b', 1)} @6",
        f"ACTION_IF {mod('a', 0)} OR {mod('b', 1)} OR {mod('c', 2)} THEN BEGIN END",
    )

    assert summary(component) == [
        (REQUIRED, ["a(0)"]),
        (FORBIDDEN, ["b(1)"]),
        (OPTIONAL, ["c(2)"]),
    ]


def test_body_of_the_next_component_is_not_read(tmp_path: Path) -> None:
    content = (
        "BACKUP ~demo/backup~\nAUTHOR ~author~\n\n"
        "BEGIN @1 DESIGNATED 10\nPRINT ~first~\n"
        f"BEGIN @2 DESIGNATED 20\nACTION_IF {mod('a', 0)} THEN BEGIN END\n"
    )

    tp2 = WeiDUTp2Parser(tmp_path).parse_string(content, "demo", None, tmp_path)

    assert tp2.components[0].requirements == []
    assert summary(tp2.components[1]) == [(OPTIONAL, ["a(0)"])]


def test_variable_target_is_skipped_and_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        component = parse_component(
            tmp_path,
            "",
            f"ACTION_IF MOD_IS_INSTALLED ~%tp2%~ ~0~ OR {mod('a', 0)} THEN BEGIN END",
        )

    assert summary(component) == [(OPTIONAL, ["a(0)"])]
    assert "demo:10: Skipping unresolved MOD_IS_INSTALLED: ACTION_IF" in caplog.text
