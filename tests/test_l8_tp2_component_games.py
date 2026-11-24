"""L8: the games a REQUIRE_PREDICATE restricts a component to."""

import pytest

from core.WeiDUTp2Parser import GamePredicateParser, Tokenizer

MOD = "MOD_IS_INSTALLED ~setup-demo.tp2~ ~0~"


def predicate_games(predicate: str, mod_games: set[str] | None = None) -> list[str] | None:
    parser = GamePredicateParser(mod_games)
    games, _ = parser.parse_from_tokens(Tokenizer().tokenize(predicate), 0)
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


@pytest.mark.parametrize(
    ("predicate", "mod_games", "games"),
    [
        ("GAME_IS ~bgee~", {"bg2ee", "eet"}, ["bgee", "sod"]),
        ("GAME_IS ~bg2 tob~", {"bg2ee", "eet"}, []),
        ("GAME_IS ~bg2ee~ AND GAME_IS ~eet~", None, []),
    ],
    ids=["game the mod does not list", "classic game only", "contradiction"],
)
def test_component_outside_the_mod_games_is_not_unrestricted(
    predicate: str, mod_games: set[str] | None, games: list[str]
) -> None:
    assert predicate_games(predicate, mod_games) == games


@pytest.mark.parametrize(
    ("predicate", "mod_games", "games"),
    [
        ("GAME_INCLUDES ~tob~", None, ["bg2ee", "eet"]),
        ("GAME_INCLUDES ~soa~", None, ["bg2ee", "eet"]),
        ("GAME_INCLUDES ~sod~", None, ["eet", "sod"]),
        ("GAME_INCLUDES ~totsc~", None, ["bgee", "eet", "sod"]),
        ("GAME_INCLUDES ~how~", None, ["iwdee"]),
        ("GAME_INCLUDES ~pst~", {"pst", "pstee", "bg2ee"}, ["pst", "pstee"]),
        ("GAME_INCLUDES ~bg2~", {"bg2ee"}, None),
        ("GAME_INCLUDES ~bg1~", {"bg2ee", "eet"}, ["eet"]),
    ],
)
def test_game_includes_names_the_games_holding_that_content(
    predicate: str, mod_games: set[str] | None, games: list[str] | None
) -> None:
    assert predicate_games(predicate, mod_games) == games


def test_mod_games_given_to_the_parser_are_left_unchanged() -> None:
    mod_games = {"bg", "bg2"}

    GamePredicateParser(mod_games)

    assert mod_games == {"bg", "bg2"}


@pytest.mark.parametrize(
    ("predicate", "games"),
    [
        ("GAME_IS iwdee", ["iwdee"]),
        ("GAME_INCLUDES tob", ["bg2ee", "eet"]),
        ("GAME_IS %game%", None),
        ("GAME_IS", None),
    ],
    ids=["bare word", "bare word content", "variable", "no argument"],
)
def test_game_argument_is_read_as_a_word_or_left_unknown(
    predicate: str, games: list[str] | None
) -> None:
    assert predicate_games(predicate) == games
