"""Sequence and game filters: mod identifiers are matched regardless of their case."""

import pytest

from core.GameModels import GameDefinition, GameSequence


def _sequence(**filters: object) -> GameSequence:
    return GameSequence.from_dict({"name": "Test", "game": "bg2ee", **filters})


def _game(forced_components: dict[str, list[str]]) -> GameDefinition:
    return GameDefinition.from_dict(
        {
            "id": "test",
            "name": "Test",
            "sequences": [{"game": "bg2ee"}],
            "forced_components": forced_components,
        }
    )


@pytest.mark.parametrize("mod_id", ["DlcMerger", "dlcmerger", "DLCMERGER"])
def test_whitelisted_mod_is_allowed_whatever_its_case(mod_id: str) -> None:
    sequence = _sequence(allowed_mods=["DlcMerger"])

    assert sequence.is_mod_allowed(mod_id)
    assert not sequence.is_mod_allowed("OtherMod")


@pytest.mark.parametrize("mod_id", ["DlcMerger", "dlcmerger", "DLCMERGER"])
def test_blacklisted_mod_is_refused_whatever_its_case(mod_id: str) -> None:
    sequence = _sequence(blocked_mods=["DlcMerger"])

    assert not sequence.is_mod_allowed(mod_id)
    assert sequence.is_mod_allowed("OtherMod")


@pytest.mark.parametrize("mod_id", ["BWS-FR-Fixpack", "bws-fr-fixpack", "BWS-FR-FIXPACK"])
def test_component_filter_applies_whatever_the_mod_case(mod_id: str) -> None:
    sequence = _sequence(allowed_components={"BWS-FR-Fixpack": ["0"]})

    assert sequence.is_component_allowed(mod_id, "0")
    assert not sequence.is_component_allowed(mod_id, "1")
    assert sequence.is_component_allowed("OtherMod", "1")


@pytest.mark.parametrize("mod_id", ["DlcMerger", "dlcmerger", "DLCMERGER"])
def test_forced_component_is_found_whatever_the_mod_case(mod_id: str) -> None:
    game = _game({"DlcMerger": ["1"]})

    assert game.is_component_forced(mod_id, "1")
    assert not game.is_component_forced(mod_id, "0")
    assert not game.is_component_forced("OtherMod", "1")


def test_public_filters_keep_their_order_with_lowercase_mod_ids() -> None:
    sequence = _sequence(
        allowed_mods=["WeiDU", "DlcMerger", "EET"],
        blocked_mods=["Soa", "Bg1UB"],
        allowed_components={"EET": ["2", "0"], "DlcMerger": ["1"]},
    )
    game = _game({"WeiDU64": ["0"], "DlcMerger": ["1"]})

    assert sequence.allowed_mods == ("weidu", "dlcmerger", "eet")
    assert sequence.blocked_mods == ("soa", "bg1ub")
    assert list(sequence.allowed_components.items()) == [
        ("eet", ("2", "0")),
        ("dlcmerger", ("1",)),
    ]
    assert game.get_forced_components() == ["weidu64:0", "dlcmerger:1"]


def test_mod_ids_differing_only_by_case_merge_their_components() -> None:
    sequence = _sequence(allowed_components={"DlcMerger": ["1", "0"], "dlcmerger": ["0", "2"]})
    game = _game({"EET": ["0"], "eet": ["0", "1"]})

    assert sequence.allowed_components == {"dlcmerger": ("1", "0", "2")}
    assert game.forced_components == {"eet": ("0", "1")}


def test_missing_filters_allow_everything() -> None:
    sequence = _sequence()

    assert sequence.allowed_mods is None
    assert sequence.blocked_mods is None
    assert sequence.is_mod_allowed("AnyMod")
    assert sequence.is_component_allowed("AnyMod", "7")


def test_empty_mod_lists_mean_no_restriction() -> None:
    sequence = _sequence(allowed_mods=[], blocked_mods=[])

    assert sequence.allowed_mods is None
    assert sequence.blocked_mods is None
    assert sequence.is_mod_allowed("AnyMod")
