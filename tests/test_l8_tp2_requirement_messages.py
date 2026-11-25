"""L8: the messages of the TP2 requirements are translated in every language of the mod."""

import logging
from pathlib import Path

import pytest

from core.ComponentRequirement import ComponentRequirement
from core.WeiDUTp2Parser import MucComponent, WeiDUTp2, WeiDUTp2Parser

LANGUAGES = (
    "LANGUAGE ~English~ ~english~ ~demo/english/setup.tra~\n"
    "LANGUAGE ~Francais~ ~french~ ~demo/french/setup.tra~\n"
)


def write_tra(base_dir: Path, folder: str, entries: dict[str, str]) -> None:
    tra_dir = base_dir / "demo" / folder
    tra_dir.mkdir(parents=True)
    content = "\n".join(f"@{ref} = ~{text}~" for ref, text in entries.items())
    (tra_dir / "setup.tra").write_text(content, encoding="utf-8")


def parse_components(tmp_path: Path, components: str, languages: str = LANGUAGES) -> WeiDUTp2:
    content = f"BACKUP ~demo/backup~\nAUTHOR ~author~\n{languages}\n{components}"
    return WeiDUTp2Parser(tmp_path).parse_string(content, "demo", None, tmp_path)


def requirements(tp2: WeiDUTp2, designated: str) -> list[ComponentRequirement]:
    for item in tp2.installable_components():
        if item.designated == designated:
            return item.requirements
    raise AssertionError(f"No component {designated}")


@pytest.fixture
def translated_mod(tmp_path: Path) -> Path:
    write_tra(tmp_path, "english", {"1": "Main", "2": "Needs other", "3": "Not with third"})
    write_tra(tmp_path, "french", {"1": "Principal", "2": "Requiert other"})
    return tmp_path


def test_header_messages_are_translated_in_each_language(translated_mod: Path) -> None:
    tp2 = parse_components(
        translated_mod,
        "BEGIN @1 DESIGNATED 10\n"
        "REQUIRE_COMPONENT ~setup-other.tp2~ ~0~ @2\n"
        "FORBID_COMPONENT ~setup-third.tp2~ ~0~ @3\n",
    )

    assert [requirement.messages for requirement in requirements(tp2, "10")] == [
        (("en_US", "Needs other"), ("fr_FR", "Requiert other")),
        (("en_US", "Not with third"), ("fr_FR", "Not with third")),
    ]


def test_predicate_message_is_translated(translated_mod: Path) -> None:
    tp2 = parse_components(
        translated_mod,
        "BEGIN @1 DESIGNATED 10\nREQUIRE_PREDICATE MOD_IS_INSTALLED ~setup-other.tp2~ ~0~ @2\n",
    )

    assert requirements(tp2, "10")[0].messages == (
        ("en_US", "Needs other"),
        ("fr_FR", "Requiert other"),
    )


def test_message_of_a_choice_is_translated(translated_mod: Path) -> None:
    tp2 = parse_components(
        translated_mod,
        "BEGIN @1 DESIGNATED 10 SUBCOMPONENT @1\nREQUIRE_COMPONENT ~setup-other.tp2~ ~0~ @2\n"
        "BEGIN @1 DESIGNATED 11 SUBCOMPONENT @1\n",
    )

    assert isinstance(tp2.components[0], MucComponent)
    assert requirements(tp2, "10")[0].messages[0] == ("en_US", "Needs other")


def test_literal_message_is_used_in_every_language(translated_mod: Path) -> None:
    tp2 = parse_components(
        translated_mod,
        "BEGIN @1 DESIGNATED 10\nFORBID_COMPONENT ~setup-other.tp2~ ~0~ ~Not with other~\n",
    )

    assert requirements(tp2, "10")[0].messages == (
        ("en_US", "Not with other"),
        ("fr_FR", "Not with other"),
    )


def test_literal_message_without_language_keeps_its_text(tmp_path: Path) -> None:
    tp2 = parse_components(
        tmp_path,
        "BEGIN ~Main~ DESIGNATED 10\nFORBID_COMPONENT ~setup-other.tp2~ ~0~ ~Not with other~\n",
        languages="",
    )

    requirement = requirements(tp2, "10")[0]
    assert requirement.messages == ()
    assert requirement.text == "Not with other"


def test_untranslated_message_leaves_the_requirement_without_message(
    translated_mod: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        tp2 = parse_components(
            translated_mod,
            "BEGIN @1 DESIGNATED 10\nREQUIRE_COMPONENT ~setup-other.tp2~ ~0~ @99\n",
        )

    assert requirements(tp2, "10")[0].messages == ()
    assert "demo:10: No translation of the message @99 of a required requirement" in caplog.text


def test_optional_requirement_has_no_message(translated_mod: Path) -> None:
    tp2 = parse_components(
        translated_mod,
        "BEGIN @1 DESIGNATED 10\n"
        "ACTION_IF MOD_IS_INSTALLED ~setup-other.tp2~ ~0~ BEGIN PRINT @2 END\n",
    )

    assert [requirement.messages for requirement in requirements(tp2, "10")] == [()]
