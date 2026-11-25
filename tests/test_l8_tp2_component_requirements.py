"""L8: REQUIRE_COMPONENT and FORBID_COMPONENT become component requirements."""

import logging
from pathlib import Path

import pytest

from core.ComponentRequirement import (
    ComponentRequirement,
    ComponentTarget,
    RequirementKind,
    RequirementSource,
    normalize_tp2_name,
)
from core.WeiDUTp2Parser import Component, MucComponent, WeiDUTp2, WeiDUTp2Parser


def parse_components(tmp_path: Path, components: str) -> WeiDUTp2:
    content = f"BACKUP ~demo/backup~\nAUTHOR ~author~\n\n{components}"
    return WeiDUTp2Parser(tmp_path).parse_string(content, "demo", None, tmp_path)


def component(tp2: WeiDUTp2, designated: str) -> Component:
    for item in tp2.components:
        for leaf in item.components if isinstance(item, MucComponent) else [item]:
            if leaf.designated == designated:
                return leaf
    raise AssertionError(f"No component {designated}")


@pytest.mark.parametrize(
    "name",
    ["setup-demo.tp2", "Setup-Demo.TP2", "demo/setup-demo.tp2", "Demo\\SETUP-DEMO.tp2", "demo"],
)
def test_tp2_names_are_normalized(name: str) -> None:
    assert normalize_tp2_name(name) == "demo"


def test_require_component_becomes_a_required_header_requirement(tmp_path: Path) -> None:
    tp2 = parse_components(
        tmp_path,
        "BEGIN @1 DESIGNATED 10\n"
        "REQUIRE_COMPONENT ~Other/Setup-Other.tp2~ ~0~ @20\n"
        "COPY_EXISTING ~sw1h01.itm~ ~override~\n",
    )

    assert component(tp2, "10").requirements == [
        ComponentRequirement(
            kind=RequirementKind.REQUIRED,
            alternatives=(ComponentTarget("other", "0"),),
            source=RequirementSource.HEADER,
            text_ref="20",
        )
    ]


def test_forbid_component_keeps_a_literal_message(tmp_path: Path) -> None:
    tp2 = parse_components(
        tmp_path,
        "BEGIN @1 DESIGNATED 10\nFORBID_COMPONENT setup-other.tp2 5 ~Not with other~\n",
    )

    assert component(tp2, "10").requirements == [
        ComponentRequirement(
            kind=RequirementKind.FORBIDDEN,
            alternatives=(ComponentTarget("other", "5"),),
            source=RequirementSource.HEADER,
            text="Not with other",
        )
    ]


def test_every_flag_gives_a_requirement_in_declaration_order(tmp_path: Path) -> None:
    tp2 = parse_components(
        tmp_path,
        "BEGIN @1 DESIGNATED 10\n"
        "REQUIRE_COMPONENT ~setup-demo.tp2~ ~0~ @2\n"
        "FORBID_COMPONENT ~setup-other.tp2~ ~3~ @3\n"
        "REQUIRE_COMPONENT ~setup-third.tp2~ ~7~ @4\n"
        "NO_LOG_RECORD\n",
    )

    requirements = component(tp2, "10").requirements

    assert [(item.kind, item.alternatives) for item in requirements] == [
        (RequirementKind.REQUIRED, (ComponentTarget("demo", "0"),)),
        (RequirementKind.FORBIDDEN, (ComponentTarget("other", "3"),)),
        (RequirementKind.REQUIRED, (ComponentTarget("third", "7"),)),
    ]
    assert tp2.unlogged_components() == {"10"}


def test_requirements_belong_to_their_own_component(tmp_path: Path) -> None:
    tp2 = parse_components(
        tmp_path,
        "BEGIN @1 DESIGNATED 10\nREQUIRE_COMPONENT ~setup-demo.tp2~ ~0~ @2\n"
        "BEGIN @3 DESIGNATED 11\n",
    )

    assert len(component(tp2, "10").requirements) == 1
    assert component(tp2, "11").requirements == []


def test_subcomponent_choices_keep_their_requirements(tmp_path: Path) -> None:
    tp2 = parse_components(
        tmp_path,
        "BEGIN @1 DESIGNATED 10 SUBCOMPONENT @9\nREQUIRE_COMPONENT ~setup-demo.tp2~ ~0~ @2\n"
        "BEGIN @3 DESIGNATED 11 SUBCOMPONENT @9\n",
    )

    assert [item.alternatives for item in component(tp2, "10").requirements] == [
        (ComponentTarget("demo", "0"),)
    ]
    assert component(tp2, "11").requirements == []
    assert tp2.components[0].requirements == []


def test_requirement_without_message_is_kept(tmp_path: Path) -> None:
    tp2 = parse_components(
        tmp_path, "BEGIN @1 DESIGNATED 10\nREQUIRE_COMPONENT ~setup-demo.tp2~ ~0~\n"
    )

    requirement = component(tp2, "10").requirements[0]

    assert requirement.alternatives == (ComponentTarget("demo", "0"),)
    assert requirement.text_ref is None
    assert requirement.text is None


@pytest.mark.parametrize(
    "arguments",
    [
        "~%unknown_folder%/setup-demo.tp2~ ~0~ @2",
        "%demo_tp2% ~0~ @2",
        "~setup-demo.tp2~ ~%demo_component%~ @2",
        "~setup-demo.tp2~ %demo_component% @2",
        "~setup-demo.tp2~ ~first~ @2",
    ],
)
def test_unresolved_target_is_skipped_and_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, arguments: str
) -> None:
    with caplog.at_level(logging.WARNING):
        tp2 = parse_components(
            tmp_path,
            f"BEGIN @1 DESIGNATED 10\nREQUIRE_COMPONENT {arguments}\nNO_LOG_RECORD\n",
        )

    assert component(tp2, "10").requirements == []
    assert tp2.unlogged_components() == {"10"}
    assert f"demo:10: Skipping unresolved target: REQUIRE_COMPONENT {arguments}" in caplog.text


def test_bare_tp2_name_starting_with_digits_is_one_target(tmp_path: Path) -> None:
    tp2 = parse_components(
        tmp_path,
        "BEGIN @1 DESIGNATED 10\n"
        "REQUIRE_COMPONENT 1PP.TP2 7 @20\n"
        "REQUIRE_PREDICATE MOD_IS_INSTALLED 1pp_thieves.tp2 ~2~ @21\n"
        "PATCH_IF MOD_IS_INSTALLED 1PP.tp2 208 BEGIN END\n",
    )

    assert [(item.source, item.alternatives) for item in component(tp2, "10").requirements] == [
        (RequirementSource.HEADER, (ComponentTarget("1pp", "7"),)),
        (RequirementSource.PREDICATE, (ComponentTarget("1pp_thieves", "2"),)),
        (RequirementSource.BODY, (ComponentTarget("1pp", "208"),)),
    ]


@pytest.mark.parametrize("value", ["10", "0010"])
def test_numbers_stay_numbers(tmp_path: Path, value: str) -> None:
    tp2 = parse_components(
        tmp_path,
        f"BEGIN @1 DESIGNATED {value}\nREQUIRE_COMPONENT ~other.tp2~ {value}-1\n",
    )

    assert [item.alternatives for item in component(tp2, value).requirements] == [
        (ComponentTarget("other", value),)
    ]
