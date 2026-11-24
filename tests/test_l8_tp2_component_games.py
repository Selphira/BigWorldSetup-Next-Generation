"""L8: the games a REQUIRE_PREDICATE restricts a component to."""

import pytest

from core.WeiDUTp2Parser import GamePredicateParser

MOD = "MOD_IS_INSTALLED ~setup-demo.tp2~ ~0~"


def predicate_games(predicate: str) -> list[str] | None:
    parser = GamePredicateParser()
    games, _ = parser.parse_from_tokens(parser.tokenizer.tokenize(predicate), 0)
    return games


@pytest.mark.parametrize(
    ("predicate", "games"),
    [
        ("GAME_IS ~bg2ee eet~", ["bg2ee", "eet"]),
        ("NOT GAME_IS ~bg2ee eet~", ["bgee", "iwdee", "pstee", "sod"]),
        (f"GAME_IS ~bg2ee~ AND {MOD}", ["bg2ee"]),
        (f"GAME_IS ~bg2ee~ AND NOT {MOD}", ["bg2ee"]),
        (f"GAME_IS ~bg2ee~ && !({MOD} OR FILE_EXISTS ~x.2da~)", ["bg2ee"]),
        (f"!(NOT GAME_IS ~bg2ee~ OR {MOD})", ["bg2ee"]),
    ],
)
def test_game_conditions_restrict_the_component(predicate: str, games: list[str]) -> None:
    assert predicate_games(predicate) == games


@pytest.mark.parametrize(
    "predicate",
    [
        f"NOT {MOD}",
        f"GAME_IS ~eet~ OR {MOD}",
        # NOT (eet AND mod) holds on every game when the mod is absent.
        f"NOT (GAME_IS ~eet~ AND {MOD})",
    ],
)
def test_negated_mod_condition_leaves_the_component_unrestricted(predicate: str) -> None:
    assert predicate_games(predicate) is None
