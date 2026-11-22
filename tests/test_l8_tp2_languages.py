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
