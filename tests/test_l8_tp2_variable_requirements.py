"""L8: a requirement whose tp2 is given by a known text variable names that tp2."""

import logging
from pathlib import Path

import pytest

from core.ComponentRequirement import ComponentTarget, RequirementKind
from core.WeiDUTp2Parser import Component, WeiDUTp2, WeiDUTp2Parser

REQUIRED = RequirementKind.REQUIRED
FORBIDDEN = RequirementKind.FORBIDDEN
OPTIONAL = RequirementKind.OPTIONAL

MAIN_COMPONENT = (
    "BEGIN @1 DESIGNATED 0 LABEL ~demo-main~\nCOPY_EXISTING ~sw1h01.itm~ ~override~\n"
)

ADAPTED = " BEGIN\n  PRINT ~adapted~\nEND\n"


def parse(tmp_path: Path, prelude: str, components: str) -> WeiDUTp2:
    """Parse the TP2 of a mod folder tmp_path/demo, its files relative to tmp_path."""
    mod_dir = tmp_path / "demo"
    mod_dir.mkdir(exist_ok=True)
    content = f"BACKUP ~demo/backup~\nAUTHOR ~author~\n{prelude}\n\n{components}"
    return WeiDUTp2Parser(tmp_path).parse_string(content, "setup-demo", None, mod_dir)


def write_file(tmp_path: Path, relative: str, content: str) -> None:
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def summary(
    tp2: WeiDUTp2, designated: str
) -> list[tuple[RequirementKind, list[ComponentTarget]]]:
    component: Component = next(
        item for item in tp2.installable_components() if item.designated == designated
    )
    return [(item.kind, list(item.alternatives)) for item in component.requirements]


def test_mod_folder_names_the_tp2_of_a_predicate(tmp_path: Path) -> None:
    tp2 = parse(
        tmp_path,
        "",
        MAIN_COMPONENT
        + "BEGIN @2 DESIGNATED 5\n"
        + 'REQUIRE_PREDICATE MOD_IS_INSTALLED "%MOD_FOLDER%.tp2"'
        + ' (ID_OF_LABEL "%MOD_FOLDER%.tp2" demo-main) @10\n',
    )

    assert summary(tp2, "5") == [(REQUIRED, [ComponentTarget("demo", "0")])]


def test_variable_of_an_included_prelude_file_names_the_tp2_of_the_body(tmp_path: Path) -> None:
    write_file(tmp_path, "demo/lib/init.tpa", "OUTER_SPRINT tp2_name ~demo.tp2~\n")

    tp2 = parse(
        tmp_path,
        "ALWAYS\n  OUTER_SPRINT lib_dir ~demo/lib~\n  INCLUDE ~%lib_dir%/init.tpa~\nEND",
        MAIN_COMPONENT
        + "BEGIN @2 DESIGNATED 5\n"
        + "ACTION_IF MOD_IS_INSTALLED ~%tp2_name%~ (ID_OF_LABEL ~%tp2_name%~ ~demo-main~)"
        + ADAPTED,
    )

    assert summary(tp2, "5") == [(OPTIONAL, [ComponentTarget("demo", "0")])]


def test_variable_of_the_prelude_names_the_tp2_of_a_header_flag(tmp_path: Path) -> None:
    tp2 = parse(
        tmp_path,
        "OUTER_SPRINT other_tp2 ~other/setup-other.tp2~",
        "BEGIN @2 DESIGNATED 5\n"
        "REQUIRE_COMPONENT ~%other_tp2%~ ~3~ @10\n"
        "REQUIRE_PREDICATE !MOD_IS_INSTALLED ~%other_tp2%~ ~4~ @11\n",
    )

    assert summary(tp2, "5") == [
        (REQUIRED, [ComponentTarget("other", "3")]),
        (FORBIDDEN, [ComponentTarget("other", "4")]),
    ]


def test_variable_the_body_defines_before_a_condition_names_its_tp2(tmp_path: Path) -> None:
    tp2 = parse(
        tmp_path,
        "",
        "BEGIN @2 DESIGNATED 5\n"
        "OUTER_SPRINT other_tp2 ~other.tp2~\n"
        "ACTION_IF MOD_IS_INSTALLED ~%other_tp2%~ ~3~" + ADAPTED,
    )

    assert summary(tp2, "5") == [(OPTIONAL, [ComponentTarget("other", "3")])]


def test_conditions_of_the_prelude_give_no_requirement(tmp_path: Path) -> None:
    write_file(
        tmp_path,
        "demo/lib/init.tpa",
        "ACTION_IF MOD_IS_INSTALLED ~other.tp2~ ~3~ BEGIN\n  OUTER_SPRINT flavour ~other~\nEND\n",
    )

    tp2 = parse(
        tmp_path,
        "ALWAYS\n  INCLUDE ~demo/lib/init.tpa~\nEND",
        "BEGIN @2 DESIGNATED 5\nCOPY_EXISTING ~sw1h01.itm~ ~override~\n",
    )

    assert summary(tp2, "5") == []


@pytest.mark.parametrize(
    "condition",
    [
        "MOD_IS_INSTALLED ~%unknown_tp2%~ ~3~",
        "MOD_IS_INSTALLED ~other.tp2~ ~%component%~",
        "MOD_IS_INSTALLED ~other.tp2~ (ID_OF_LABEL ~other.tp2~ ~%label%~)",
    ],
)
def test_unknown_tp2_variable_and_variable_component_are_skipped(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, condition: str
) -> None:
    caplog.set_level(logging.WARNING)

    tp2 = parse(
        tmp_path,
        "OUTER_SPRINT component ~3~\nOUTER_SPRINT label ~other-main~",
        f"BEGIN @2 DESIGNATED 5\nACTION_IF {condition}" + ADAPTED,
    )

    assert summary(tp2, "5") == []
    assert "unresolved MOD_IS_INSTALLED" in caplog.text
