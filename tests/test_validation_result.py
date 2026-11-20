from constants import ICON_ERROR, ICON_WARNING
from core.ComponentReference import ComponentReference
from ui.pages.InstallOrder import ValidationResult


def ref(value: str) -> ComponentReference:
    return ComponentReference.from_string(value)


def test_error_takes_precedence_without_hiding_the_warning() -> None:
    result = ValidationResult()
    result.add_warning(ref("a:0"))
    result.add_error(ref("a:0"))

    assert result.get_component_indicator(ref("a:0")) == (ValidationResult.COLOR_ERROR, ICON_ERROR)
    assert result.has_errors and not result.is_valid
    assert result.has_warnings


def test_indicator_per_component() -> None:
    result = ValidationResult()
    result.add_warning(ref("b:1"))

    assert result.get_component_indicator(ref("b:1")) == (ValidationResult.COLOR_WARNING, ICON_WARNING)
    assert result.get_component_indicator(ref("c:2")) == (ValidationResult.COLOR_VALID, "")
    assert result.is_valid


def test_clear_forgets_every_issue() -> None:
    result = ValidationResult()
    result.add_error(ref("a:0"))
    result.add_warning(ref("b:1"))
    result.clear()

    assert not result.has_errors and not result.has_warnings
