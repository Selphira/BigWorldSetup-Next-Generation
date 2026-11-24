"""L8: MOD_IS_INSTALLED in the files a component includes becomes an optional requirement."""

import logging
from pathlib import Path

import pytest

from constants import TP2_INCLUDE_MAX_DEPTH
from core.ComponentRequirement import RequirementKind
from core.WeiDUTp2Parser import Component, WeiDUTp2Parser

REQUIRED = RequirementKind.REQUIRED
OPTIONAL = RequirementKind.OPTIONAL

TP2_HEADER = "BACKUP ~demo/backup~\nAUTHOR ~author~\n"


def mod(tp2: str, number: int) -> str:
    return f"MOD_IS_INSTALLED ~setup-{tp2}.tp2~ ~{number}~"


def condition_on(tp2: str, number: int) -> str:
    return f"ACTION_IF {mod(tp2, number)} THEN BEGIN PRINT ~adapted~ END\n"


def write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def parse_components(tp2_path: Path, components: str, prelude: str = "") -> list[Component]:
    write(tp2_path, f"{TP2_HEADER}{prelude}\n{components}")
    return WeiDUTp2Parser(tp2_path.parent).parse_file(tp2_path).components


def summary(component: Component) -> list[tuple[RequirementKind, list[str]]]:
    return [
        (item.kind, [f"{target.tp2}({target.component})" for target in item.alternatives])
        for item in component.requirements
    ]


@pytest.fixture
def game_dir(tmp_path: Path) -> Path:
    return tmp_path / "game"


@pytest.fixture
def mod_dir(game_dir: Path) -> Path:
    return game_dir / "demo"


@pytest.fixture
def tp2_path(mod_dir: Path) -> Path:
    """A TP2 inside its mod folder, the usual layout."""
    return mod_dir / "setup-demo.tp2"


@pytest.mark.parametrize(
    "include",
    [
        "INCLUDE ~%MOD_FOLDER%/lib/adapt.tpa~",
        "INCLUDE ~demo/lib/adapt.tpa~",
        'INCLUDE "demo/lib/adapt.tpa"',
        "INCLUDE ~DEMO/Lib/Adapt.TPA~",
        "COPY_EXISTING ~sw1h01.itm~ ~override~\n  PATCH_INCLUDE ~%MOD_FOLDER%/lib/adapt.tpa~",
    ],
)
def test_condition_of_an_included_file_becomes_an_optional_requirement(
    tp2_path: Path, mod_dir: Path, include: str
) -> None:
    write(mod_dir / "lib" / "adapt.tpa", condition_on("other", 0))

    components = parse_components(tp2_path, f"BEGIN @1 DESIGNATED 10\n{include}\n")

    assert summary(components[0]) == [(OPTIONAL, ["other(0)"])]


def test_tp2_beside_its_mod_folder_includes_from_that_folder(game_dir: Path) -> None:
    write(game_dir / "demo" / "lib" / "adapt.tpa", condition_on("other", 0))

    components = parse_components(
        game_dir / "setup-demo.tp2",
        "BEGIN @1 DESIGNATED 10\nINCLUDE ~%MOD_FOLDER%/lib/adapt.tpa~\n",
    )

    assert summary(components[0]) == [(OPTIONAL, ["other(0)"])]


def test_included_file_is_scanned_only_for_the_component_including_it(
    tp2_path: Path, mod_dir: Path
) -> None:
    write(mod_dir / "lib" / "adapt.tpa", condition_on("other", 0))

    components = parse_components(
        tp2_path,
        "BEGIN @1 DESIGNATED 10\nINCLUDE ~%MOD_FOLDER%/lib/adapt.tpa~\n"
        "BEGIN @2 DESIGNATED 20\nPRINT ~nothing~\n"
        "BEGIN @3 DESIGNATED 30\nINCLUDE ~%MOD_FOLDER%/lib/adapt.tpa~\n",
    )

    assert [summary(component) for component in components] == [
        [(OPTIONAL, ["other(0)"])],
        [],
        [(OPTIONAL, ["other(0)"])],
    ]


def test_conditions_are_read_in_order_through_nested_includes(
    tp2_path: Path, mod_dir: Path
) -> None:
    write(
        mod_dir / "lib" / "outer.tpa",
        condition_on("a", 0) + "INCLUDE ~%MOD_FOLDER%/lib/inner.tph~\n" + condition_on("c", 2),
    )
    write(mod_dir / "lib" / "inner.tph", condition_on("b", 1))

    components = parse_components(
        tp2_path,
        "BEGIN @1 DESIGNATED 10\nINCLUDE ~%MOD_FOLDER%/lib/outer.tpa~\n" + condition_on("d", 3),
    )

    assert summary(components[0]) == [
        (OPTIONAL, ["a(0)"]),
        (OPTIONAL, ["b(1)"]),
        (OPTIONAL, ["c(2)"]),
        (OPTIONAL, ["d(3)"]),
    ]


def test_every_file_of_an_include_list_is_scanned(tp2_path: Path, mod_dir: Path) -> None:
    write(mod_dir / "lib" / "a.tpa", condition_on("a", 0))
    write(mod_dir / "lib" / "b.tpa", condition_on("b", 1))

    components = parse_components(
        tp2_path,
        "BEGIN @1 DESIGNATED 10\nINCLUDE ~%MOD_FOLDER%/lib/a.tpa~ ~%MOD_FOLDER%/lib/b.tpa~\n",
    )

    assert summary(components[0]) == [(OPTIONAL, ["a(0)"]), (OPTIONAL, ["b(1)"])]


def test_comments_of_an_included_file_are_ignored(tp2_path: Path, mod_dir: Path) -> None:
    write(
        mod_dir / "lib" / "adapt.tpa",
        f"// {condition_on('a', 0)}/* {condition_on('b', 1)} */\n{condition_on('c', 2)}",
    )

    components = parse_components(
        tp2_path, "BEGIN @1 DESIGNATED 10\nINCLUDE ~%MOD_FOLDER%/lib/adapt.tpa~\n"
    )

    assert summary(components[0]) == [(OPTIONAL, ["c(2)"])]


@pytest.mark.parametrize(
    ("prelude", "body"),
    [
        (
            "ALWAYS\n  OUTER_SPRINT lib_dir ~%MOD_FOLDER%/lib~\nEND\n",
            "INCLUDE ~%lib_dir%/adapt.tpa~",
        ),
        (
            "",
            "OUTER_TEXT_SPRINT ~lib_dir~ ~%MOD_FOLDER%/lib~\n"
            "OUTER_SPRINT file EVAL ~%lib_dir%/adapt.tpa~\n"
            "INCLUDE ~%file%~",
        ),
        (
            "",
            "OUTER_SPRINT file ~%MOD_FOLDER%/lib/adapt.tpa~\nINCLUDE %file%",
        ),
        (
            "",
            "OUTER_SPRINT label ~%MOD_FOLDER%/lib/adapt.tpa~\nINCLUDE ~%label%~",
        ),
    ],
    ids=["always_block", "component_body", "bare_variable", "lowercase_keyword_name"],
)
def test_include_path_is_expanded_with_the_text_variables_defined_before(
    tp2_path: Path, mod_dir: Path, prelude: str, body: str
) -> None:
    write(mod_dir / "lib" / "adapt.tpa", condition_on("other", 0))

    components = parse_components(tp2_path, f"BEGIN @1 DESIGNATED 10\n{body}\n", prelude)

    assert summary(components[0]) == [(OPTIONAL, ["other(0)"])]


def test_variable_defined_by_an_included_file_serves_the_next_include(
    tp2_path: Path, mod_dir: Path
) -> None:
    write(mod_dir / "lib" / "paths.tph", "OUTER_SPRINT adapt_dir ~%MOD_FOLDER%/adapt~\n")
    write(mod_dir / "adapt" / "main.tpa", condition_on("other", 0))

    components = parse_components(
        tp2_path,
        "BEGIN @1 DESIGNATED 10\n"
        "INCLUDE ~%MOD_FOLDER%/lib/paths.tph~\nINCLUDE ~%adapt_dir%/main.tpa~\n",
    )

    assert summary(components[0]) == [(OPTIONAL, ["other(0)"])]


def test_variables_of_a_component_body_do_not_reach_the_next_component(
    tp2_path: Path, mod_dir: Path
) -> None:
    write(mod_dir / "lib" / "adapt.tpa", condition_on("other", 0))

    components = parse_components(
        tp2_path,
        "BEGIN @1 DESIGNATED 10\nOUTER_SPRINT lib_dir ~%MOD_FOLDER%/lib~\n"
        "BEGIN @2 DESIGNATED 20\nINCLUDE ~%lib_dir%/adapt.tpa~\n",
    )

    assert components[1].requirements == []


def test_target_named_by_the_header_is_not_optional_in_an_included_file(
    tp2_path: Path, mod_dir: Path
) -> None:
    write(mod_dir / "lib" / "adapt.tpa", condition_on("a", 0) + condition_on("b", 1))

    components = parse_components(
        tp2_path,
        "BEGIN @1 DESIGNATED 10\nREQUIRE_COMPONENT ~setup-a.tp2~ ~0~ @5\n"
        "INCLUDE ~%MOD_FOLDER%/lib/adapt.tpa~\n",
    )

    assert summary(components[0]) == [(REQUIRED, ["a(0)"]), (OPTIONAL, ["b(1)"])]


@pytest.mark.parametrize(
    ("include", "reason"),
    [
        ("INCLUDE ~%unknown%/adapt.tpa~", "unresolved INCLUDE: ~%unknown%/adapt.tpa~"),
        (
            "INCLUDE ~%MOD_FOLDER%/lib/missing.tpa~",
            "unresolved INCLUDE: ~%MOD_FOLDER%/lib/missing.tpa~",
        ),
        ("INCLUDE ~%MOD_FOLDER%/lib~", "unresolved INCLUDE: ~%MOD_FOLDER%/lib~"),
        ("INCLUDE EVAL ~%MOD_FOLDER%/lib/adapt.tpa~", "unresolved INCLUDE: EVAL"),
    ],
)
def test_unresolved_include_is_skipped_and_logged(
    tp2_path: Path,
    mod_dir: Path,
    caplog: pytest.LogCaptureFixture,
    include: str,
    reason: str,
) -> None:
    write(mod_dir / "lib" / "adapt.tpa", condition_on("other", 0))

    with caplog.at_level(logging.WARNING):
        components = parse_components(
            tp2_path, f"BEGIN @1 DESIGNATED 10\n{include}\n{condition_on('kept', 1)}"
        )

    assert summary(components[0]) == [(OPTIONAL, ["kept(1)"])]
    assert f"setup-demo:10: Skipping {reason}" in caplog.text


def test_unresolved_include_of_an_included_file_names_that_file(
    tp2_path: Path, mod_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    write(mod_dir / "lib" / "adapt.tpa", "INCLUDE ~%MOD_FOLDER%/lib/missing.tph~\n")

    with caplog.at_level(logging.WARNING):
        parse_components(tp2_path, "BEGIN @1 DESIGNATED 10\nINCLUDE ~demo/lib/adapt.tpa~\n")

    assert (
        "Skipping unresolved INCLUDE in demo/lib/adapt.tpa: ~%MOD_FOLDER%/lib/missing.tph~"
        in caplog.text
    )


def test_include_cycle_is_scanned_once_and_logged(
    tp2_path: Path, mod_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    write(
        mod_dir / "lib" / "a.tpa", condition_on("a", 0) + "INCLUDE ~%MOD_FOLDER%/lib/b.tpa~\n"
    )
    write(
        mod_dir / "lib" / "b.tpa", condition_on("b", 1) + "INCLUDE ~%MOD_FOLDER%/lib/a.tpa~\n"
    )

    with caplog.at_level(logging.WARNING):
        components = parse_components(
            tp2_path, "BEGIN @1 DESIGNATED 10\nINCLUDE ~%MOD_FOLDER%/lib/a.tpa~\n"
        )

    assert summary(components[0]) == [(OPTIONAL, ["a(0)"]), (OPTIONAL, ["b(1)"])]
    assert "Skipping cyclic INCLUDE in demo/lib/b.tpa: ~%MOD_FOLDER%/lib/a.tpa~" in caplog.text


def test_includes_beyond_the_depth_limit_are_skipped_and_logged(
    tp2_path: Path, mod_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    depth = TP2_INCLUDE_MAX_DEPTH + 1
    for level in range(depth):
        write(
            mod_dir / "lib" / f"level{level}.tpa",
            condition_on("level", level) + f"INCLUDE ~%MOD_FOLDER%/lib/level{level + 1}.tpa~\n",
        )

    with caplog.at_level(logging.WARNING):
        components = parse_components(
            tp2_path, "BEGIN @1 DESIGNATED 10\nINCLUDE ~%MOD_FOLDER%/lib/level0.tpa~\n"
        )

    assert [targets for _, targets in summary(components[0])] == [
        [f"level({level})"] for level in range(TP2_INCLUDE_MAX_DEPTH)
    ]
    assert "Skipping too deeply nested INCLUDE" in caplog.text


def test_tp2_parsed_from_a_string_without_folder_logs_its_includes(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    content = f"{TP2_HEADER}BEGIN @1 DESIGNATED 10\nINCLUDE ~demo/lib/adapt.tpa~\n"

    with caplog.at_level(logging.WARNING):
        tp2 = WeiDUTp2Parser(tmp_path).parse_string(content, "demo")

    assert tp2.components[0].requirements == []
    assert "demo:10: Skipping unresolved INCLUDE: ~demo/lib/adapt.tpa~" in caplog.text


def test_include_path_the_system_rejects_leaves_the_component(tp2_path: Path) -> None:
    components = parse_components(
        tp2_path, "BEGIN @1 DESIGNATED 10\nINCLUDE ~.../copy_portraits.tpa~\n"
    )

    assert [component.designated for component in components] == ["10"]


def test_included_file_extracted_after_a_first_parse_is_found(
    tp2_path: Path, mod_dir: Path
) -> None:
    components = "BEGIN @1 DESIGNATED 10\nINCLUDE ~%MOD_FOLDER%/lib/adapt.tpa~\n"
    write(mod_dir / "lib" / "other.tpa", "")
    assert summary(parse_components(tp2_path, components)[0]) == []

    write(mod_dir / "lib" / "ADAPT.tpa", condition_on("a", 0))

    assert summary(parse_components(tp2_path, components)[0]) == [(OPTIONAL, ["a(0)"])]
