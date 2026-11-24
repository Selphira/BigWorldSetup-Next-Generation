"""L8: the header of a TP2 component block ends where its body starts."""

from pathlib import Path

import pytest

from core.WeiDUTp2Parser import ComponentParser, MucComponent, WeiDUTp2, WeiDUTp2Parser


def parse_components(tmp_path: Path, components: str) -> WeiDUTp2:
    content = f"BACKUP ~demo/backup~\nAUTHOR ~author~\n\n{components}"
    return WeiDUTp2Parser(tmp_path).parse_string(content, "demo", None, tmp_path)


def test_body_starts_at_the_first_token_that_is_not_a_flag() -> None:
    parser = ComponentParser()
    tokens = parser.tokenizer.tokenize(
        "BEGIN @1 DESIGNATED 10 GROUP @2 INSTALL_BY_DEFAULT\n"
        "REQUIRE_COMPONENT ~setup-demo.tp2~ ~0~ @3\n"
        "COPY_EXISTING ~sw1h01.itm~ ~override~\n"
    )

    header = parser.parse_header(tokens)

    assert header is not None
    assert header.designated == "10"
    assert tokens[header.body_start].value == "COPY_EXISTING"


def test_block_without_body_ends_on_eof() -> None:
    parser = ComponentParser()
    tokens = parser.tokenizer.tokenize("BEGIN @1 DESIGNATED 10 NO_LOG_RECORD")

    header = parser.parse_header(tokens)

    assert header is not None
    assert header.no_log_record
    assert header.body_start == len(tokens) - 1


@pytest.mark.parametrize(
    "predicate",
    [
        "MOD_IS_INSTALLED ~setup-demo.tp2~ ~0~",
        "MOD_IS_INSTALLED setup-demo.tp2 0 || IS_AN_INT demo_component_0_installed",
        "NOT ~%WEIDU_ARCH%~ STR_EQ ~x86~",
        "IDS_OF_SYMBOL(~kit~ ~DEMO_KIT~) > 0",
        "((GAME_INCLUDES ~bg2~) OR\n (GAME_IS ~iwdee~))",
    ],
)
def test_flags_after_a_require_predicate_are_read(tmp_path: Path, predicate: str) -> None:
    tp2 = parse_components(
        tmp_path, f"BEGIN @1\nREQUIRE_PREDICATE {predicate} @2\nDESIGNATED 7\nNO_LOG_RECORD\n"
    )

    assert [component.designated for component in tp2.components] == ["7"]
    assert tp2.unlogged_components() == {"7"}


def test_flags_after_a_subcomponent_predicate_are_read(tmp_path: Path) -> None:
    tp2 = parse_components(
        tmp_path,
        "BEGIN @1 SUBCOMPONENT @10 (GAME_IS ~bg2ee~) DESIGNATED 5\n"
        "BEGIN @2 SUBCOMPONENT @10 DESIGNATED 6\n",
    )

    [choice] = tp2.components
    assert isinstance(choice, MucComponent)
    assert [component.designated for component in choice.components] == ["5", "6"]


def test_no_log_record_after_a_bare_label_or_metadata_is_kept(tmp_path: Path) -> None:
    tp2 = parse_components(
        tmp_path,
        "BEGIN @1 DESIGNATED 1 LABEL c#demo-main NO_LOG_RECORD\n"
        "BEGIN @2 DESIGNATED 2 METADATA ~demo~ NO_LOG_RECORD\n",
    )

    assert tp2.unlogged_components() == {"1", "2"}


def test_bare_label_keeps_hash_and_dot() -> None:
    header = ComponentParser().parse_block("BEGIN @1 LABEL c#demo-v1.2\nCOPY ~a~ ~b~")

    assert header is not None
    assert header.label == "c#demo-v1.2"


def test_body_begin_does_not_replace_the_component_label(tmp_path: Path) -> None:
    tp2 = parse_components(
        tmp_path,
        "BEGIN ~Demo Core~ DESIGNATED 0\n"
        "ACTION_FOR_EACH file IN ~ar0602~ BEGIN\n"
        "  WHILE 0 BEGIN ~not a label~ END\n"
        "END\n",
    )

    assert tp2.untranslated_component_labels == {"0": "Demo Core"}


def test_body_words_are_not_read_as_header_flags() -> None:
    header = ComponentParser().parse_block(
        "BEGIN @1 DESIGNATED 3\nLAF demo STR_VAR group = ~cre~ label = ~x~ END\n"
    )

    assert header is not None
    assert header.group is None
    assert header.label is None


def test_second_string_after_the_label_is_skipped(tmp_path: Path) -> None:
    tp2 = parse_components(
        tmp_path, "BEGIN ~Demo Movies~ ~Not for the selected language~\nDESIGNATED 2\n"
    )

    assert tp2.untranslated_component_labels == {"2": "Demo Movies"}
