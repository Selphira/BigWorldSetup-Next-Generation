"""L8 - TP2 parser: LANGUAGE declarations and TRA file encodings."""

from pathlib import Path

import pytest

from core.WeiDUTp2Parser import WeiDUTp2Parser

LANGUAGES_TP2 = """BACKUP ~demo/backup~
AUTHOR ~author~
LANGUAGE~Chinese~
    ~SimChinese~
    ~demo/schinese/setup.tra~
LANGUAGE ~English~ ~English~ ~demo/english/setup.tra~

BEGIN @1
DESIGNATED 0
"""


def _write_tra(base_dir: Path, folder: str, text: str, encoding: str) -> None:
    tra_dir = base_dir / "demo" / folder
    tra_dir.mkdir(parents=True)
    (tra_dir / "setup.tra").write_text(f"@1 = ~{text}~", encoding=encoding)


def test_language_keyword_without_space_is_parsed(tmp_path: Path) -> None:
    _write_tra(tmp_path, "schinese", "Demo", "utf-8")
    _write_tra(tmp_path, "english", "Demo", "utf-8")

    tp2 = WeiDUTp2Parser(tmp_path).parse_string(LANGUAGES_TP2, "demo", None, tmp_path)

    assert [(lang.display_name, lang.language_code, lang.tra_files) for lang in tp2.languages] == [
        ("Chinese", "zh_CN", ["demo/schinese/setup.tra"]),
        ("English", "en_US", ["demo/english/setup.tra"]),
    ]


@pytest.mark.parametrize(
    ("encoding", "label"),
    [("gbk", "智代与地下城"), ("utf-8", "智代与地下城")],
    ids=["legacy GBK", "UTF-8"],
)
def test_chinese_tra_file_is_decoded(tmp_path: Path, encoding: str, label: str) -> None:
    _write_tra(tmp_path, "schinese", label, encoding)
    _write_tra(tmp_path, "english", "Demo", "cp1252")

    tp2 = WeiDUTp2Parser(tmp_path).parse_string(LANGUAGES_TP2, "demo", None, tmp_path)

    assert tp2.component_translations["zh_CN"] == {"0": label}


def _languages_tp2(*declarations: tuple[str, str]) -> str:
    language_lines = "\n".join(
        f"LANGUAGE ~{label}~ ~{code}~ ~demo/{code}/setup.tra~" for label, code in declarations
    )
    return f"BACKUP ~demo/backup~\nAUTHOR ~author~\n{language_lines}\n\nBEGIN @1\nDESIGNATED 0\n"


@pytest.mark.parametrize(
    ("declarations", "expected"),
    [
        ([("Francais pour oBG2", "french"), ("Francais pour BG2EE", "frenchEE")], 1),
        ([("Francais pour les editions EE", "frenchEE"), ("Francais", "french")], 0),
        ([("Francais", "frenchbg2"), ("Francais pour l'edition Enhanced", "french")], 1),
        ([("English (olimikrig's revision)", "English_revision"), ("English (original text)", "English_original")], 0),
    ],
    ids=["EE declared last", "EE declared first", "Enhanced label", "first declared without EE"],
)
def test_one_translation_is_kept_per_language(
    tmp_path: Path, declarations: list[tuple[str, str]], expected: int
) -> None:
    for index, (_, code) in enumerate(declarations):
        _write_tra(tmp_path, code, f"Label {index}", "utf-8")

    tp2 = WeiDUTp2Parser(tmp_path).parse_string(_languages_tp2(*declarations), "demo", None, tmp_path)
    language_code = tp2.languages[0].language_code

    assert [lang.index for lang in tp2.preferred_languages()] == [expected]
    assert tp2.component_translations == {language_code: {"0": f"Label {expected}"}}
    assert len(tp2.languages) == len(declarations)


def test_translations_of_other_languages_keep_declaration_order(tmp_path: Path) -> None:
    declarations = [("Polski", "polski"), ("Francais", "french"), ("English", "english"), ("Francais EE", "frenchEE")]
    for _, code in declarations:
        _write_tra(tmp_path, code, code, "utf-8")

    tp2 = WeiDUTp2Parser(tmp_path).parse_string(_languages_tp2(*declarations), "demo", None, tmp_path)

    assert [(lang.language_code, lang.index) for lang in tp2.preferred_languages()] == [
        ("pl_PL", 0),
        ("en_US", 2),
        ("fr_FR", 3),
    ]
