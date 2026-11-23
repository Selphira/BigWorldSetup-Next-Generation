"""Consistency of the translation keys used by the code with the language files."""

import ast
from collections.abc import Iterable, Iterator
import json
from pathlib import Path
import re
import string

import pytest

from core.ArchiveExtractor import ExtractionFailure, ExtractionStatus
from core.BackupInfo import BackupStatus
from core.DownloadManager import ArchiveStatus
from core.enums.CategoryEnum import CategoryEnum
from core.InstallationJournal import RunOutcome
from core.Rules import RuleType
from core.weidu_types import ComponentStatus
from validation.catalogue import ROOT

LOCALES_DIR = ROOT / "i18n"
REFERENCE_LANGUAGE = "en_US"
COMPLETE_LANGUAGES = ("en_US", "fr_FR")
SOURCE_PATHS = (ROOT / "core", ROOT / "ui", ROOT / "main.py")

# Every key built at runtime, written as the f-string template with each
# interpolated value replaced by "{}", mapped to the values it can receive.
# A new dynamic tr() call must be declared here so its keys get checked.
DYNAMIC_FAMILIES: dict[str, Iterable[str]] = {
    # ExtractionPipeline: refusal reason of the archive extractor
    "page.extraction.failure.{}": [failure.value for failure in ExtractionFailure],
    # DownloadPage and ExtractionPipeline: archive verification status
    "page.download.status.{}": [status.value for status in ArchiveStatus],
    # DownloadPage final summary: counters, then titles of the detailed lists
    "page.download.summary.{}": ["valid", "warnings", "errors"],
    "page.download.summary.{}_title": ["warnings", "errors"],
    # ExtractionPage: extraction status column
    "page.extraction.status.{}": [status.value for status in ExtractionStatus],
    # BackupPage: backup status column
    "page.backup.status.{}": [status.value for status in BackupStatus],
    # InstallationPage: operation, DEBUG and result statuses
    "page.installation.status.{}": [status.value for status in ComponentStatus],
    # InstallationPage: outcome of the whole run
    "page.installation.{}": [outcome.value for outcome in RunOutcome],
    # InstallationPage: last line of the plan summary, new run or resume
    "page.installation.summary.{}": ["ready", "ready_to_resume"],
    # Rules: order constraint direction
    "rule.message_order_{}": ["before", "after"],
    # ViolationPanel and OrderViolationPanel: rule type column
    "page.selection.violation.type_{}": [rule_type.value for rule_type in RuleType],
    # PauseDescriptionDialog: confirmation button of the add and edit modes
    "button.{}": ["add", "edit"],
    # CategoryEnum: category display name
    "category.{}": [category.value for category in CategoryEnum],
}

KEY_PATTERN = re.compile(r"[a-z_]+(?:\.[a-z0-9_]+)+")


# ========================================
# HELPERS
# ========================================


def _flatten(node: dict, prefix: str = "") -> Iterator[tuple[str, object]]:
    for name, value in node.items():
        if isinstance(value, dict):
            yield from _flatten(value, f"{prefix}{name}.")
        else:
            yield f"{prefix}{name}", value


def _load_texts(language: str) -> dict[str, str]:
    path = LOCALES_DIR / f"{language}.json"
    entries = _flatten(json.loads(path.read_text(encoding="utf-8")))
    return {key: value for key, value in entries if not key.startswith("_metadata.")}


def _placeholders(text: str) -> set[str]:
    return {field for _, field, _, _ in string.Formatter().parse(text) if field}


def _source_files() -> Iterator[Path]:
    for path in SOURCE_PATHS:
        yield from sorted(path.rglob("*.py")) if path.is_dir() else [path]


def _parsed_sources() -> Iterator[tuple[str, ast.AST]]:
    for path in _source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        yield path.relative_to(ROOT).as_posix(), tree


def _tr_calls(tree: ast.AST) -> Iterator[ast.Call]:
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "tr"
            and node.args
        ):
            yield node


def _template(key: ast.JoinedStr) -> str:
    return "".join(
        part.value if isinstance(part, ast.Constant) else "{}" for part in key.values
    )


def _missing_languages(key: str) -> list[str]:
    return [language for language in COMPLETE_LANGUAGES if key not in TEXTS[language]]


TEXTS = {language: _load_texts(language) for language in COMPLETE_LANGUAGES}
SOURCES = list(_parsed_sources())


def _is_namespace(candidate: str) -> bool:
    prefix = f"{candidate}."
    return any(key.startswith(prefix) for key in TEXTS[REFERENCE_LANGUAGE])


def _literal_keys() -> Iterator[tuple[str, int, str]]:
    """Yield the key-shaped literals of the code, passed to tr() directly or not.

    Keys are also stored in attributes or passed to helpers (FolderSelector,
    dialog button labels), so every literal of a known translation section is
    checked; namespaces of existing keys and fragments of f-strings, covered
    by DYNAMIC_FAMILIES, are left out.
    """
    sections = {key.split(".")[0] for key in TEXTS[REFERENCE_LANGUAGE]}
    for source, tree in SOURCES:
        fragments = {
            id(part)
            for node in ast.walk(tree)
            if isinstance(node, ast.JoinedStr)
            for part in node.values
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or id(node) in fragments:
                continue
            value = node.value
            if not isinstance(value, str):
                continue
            if (
                KEY_PATTERN.fullmatch(value)
                and value.split(".")[0] in sections
                and not _is_namespace(value)
            ):
                yield source, node.lineno, value


# ========================================
# KEYS USED BY THE CODE
# ========================================


def test_literal_keys_exist_in_french_and_english() -> None:
    missing = [
        f"{source}:{line} {key} (missing in {', '.join(languages)})"
        for source, line, key in _literal_keys()
        if (languages := _missing_languages(key))
    ]

    assert missing == []


def test_dynamic_keys_are_declared_families() -> None:
    undeclared = [
        f"{source}:{call.lineno} {_template(call.args[0])}"
        for source, tree in SOURCES
        for call in _tr_calls(tree)
        if isinstance(call.args[0], ast.JoinedStr)
        and _template(call.args[0]) not in DYNAMIC_FAMILIES
    ]

    assert undeclared == []


@pytest.mark.parametrize("template", sorted(DYNAMIC_FAMILIES))
def test_dynamic_families_are_complete(template: str) -> None:
    missing = [
        f"{key} (missing in {', '.join(languages)})"
        for value in DYNAMIC_FAMILIES[template]
        if (languages := _missing_languages(key := template.format(value)))
    ]

    assert missing == []


def test_literal_calls_provide_every_placeholder() -> None:
    reference = TEXTS[REFERENCE_LANGUAGE]
    unformatted = []
    for source, tree in SOURCES:
        for call in _tr_calls(tree):
            key = call.args[0]
            if not isinstance(key, ast.Constant) or key.value not in reference:
                continue
            if any(keyword.arg is None for keyword in call.keywords):
                continue  # **kwargs: arguments unknown statically

            provided = {keyword.arg for keyword in call.keywords}
            if absent := _placeholders(reference[key.value]) - provided:
                unformatted.append(f"{source}:{call.lineno} {key.value} {sorted(absent)}")

    assert unformatted == []


# ========================================
# LANGUAGE FILES
# ========================================


def test_french_and_english_define_the_same_keys() -> None:
    english, french = (set(TEXTS[language]) for language in COMPLETE_LANGUAGES)

    assert sorted(english - french) == []
    assert sorted(french - english) == []


@pytest.mark.parametrize("language", sorted(path.stem for path in LOCALES_DIR.glob("*.json")))
def test_translations_use_the_english_placeholders(language: str) -> None:
    reference = TEXTS[REFERENCE_LANGUAGE]
    texts = _load_texts(language)

    different = [
        f"{key}: {sorted(_placeholders(text))} instead of {sorted(_placeholders(reference[key]))}"
        for key, text in texts.items()
        if key in reference and _placeholders(text) != _placeholders(reference[key])
    ]

    assert different == []
