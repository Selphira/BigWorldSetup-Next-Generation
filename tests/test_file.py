from pathlib import Path

import pytest

from core.File import TEXT_ENCODINGS, safe_read


@pytest.mark.parametrize(
    "raw_content",
    [
        "Première ligne\r\nDeuxième ligne\r\n".encode("utf-8-sig"),
        "Épée\r\nBouclier – “runique”\r\n".encode("cp1252"),
        b"Lame \x81\x8d\r\nFin\rDernier\n",
    ],
    ids=["utf8_bom_crlf", "cp1252_fallback_crlf", "latin1_last_resort_mixed_newlines"],
)
def test_safe_read_matches_read_text_with_fallback_encoding(
    tmp_path: Path, raw_content: bytes
) -> None:
    text_file = tmp_path / "setup-mod.tra"
    text_file.write_bytes(raw_content)

    expected = _read_text_with_fallback(text_file)

    assert safe_read(text_file) == expected
    assert "\r" not in expected


def test_safe_read_crlf_cp1252_file_is_decoded_with_unix_newlines(tmp_path: Path) -> None:
    text_file = tmp_path / "readme.txt"
    text_file.write_bytes("Épée\r\nfin\r\n".encode("cp1252"))

    assert safe_read(text_file) == "Épée\nfin\n"


def test_safe_read_missing_file_raises_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="File not found"):
        safe_read(tmp_path / "absent.tra")


def _read_text_with_fallback(path: Path) -> str:
    """Reference behaviour: the former read_text()-based implementation."""
    *attempts, last_resort = TEXT_ENCODINGS
    for encoding in attempts:
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            pass
    return path.read_text(encoding=last_resort)
