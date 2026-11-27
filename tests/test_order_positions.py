"""What precedes a position of an install order, as order validation reads it (plan 5.2)."""

from core.ComponentReference import ComponentReference
from core.OrderPositions import OrderPositions

ref = ComponentReference.from_string


def positions_of(order: list[str], earlier: frozenset[str] = frozenset()) -> OrderPositions:
    """Index an order in which b:10 selects its SUB answer b:10.1.2."""
    install_order = [ref(entry) for entry in order]
    answers = {ref("b:10"): {ref("b:10.1.2")}}
    earlier_references = {ref(entry) for entry in earlier} | {
        answer for base in map(ref, earlier) for answer in answers.get(base, ())
    }
    return OrderPositions.of(install_order, earlier_references, answers)


def test_prefix_holds_only_components_placed_strictly_before() -> None:
    positions = positions_of(["a:0", "b:10", "c:5"])

    prefix = positions.preceding(2)

    assert ref("a:0").is_selected_in(prefix)
    assert ref("b:10").is_selected_in(prefix)
    assert not ref("c:5").is_selected_in(prefix)
    assert not ref("z:1").is_selected_in(prefix)


def test_sub_answer_is_provided_at_the_position_of_its_component() -> None:
    positions = positions_of(["a:0", "b:10"])

    assert not ref("b:10.1.2").is_selected_in(positions.preceding(1))
    assert ref("b:10.1.2").is_selected_in(positions.preceding(2))


def test_mod_wildcard_is_satisfied_from_the_first_component_of_the_mod() -> None:
    positions = positions_of(["a:0", "b:20", "c:5", "b:10"])

    assert not ComponentReference.for_mod("b").is_selected_in(positions.preceding(1))
    assert ComponentReference.for_mod("b").is_selected_in(positions.preceding(2))


def test_earlier_references_precede_every_position() -> None:
    positions = positions_of(["a:0"], earlier=frozenset({"b:10"}))

    first = positions.preceding(0)

    assert ref("b:10").is_selected_in(first)
    assert ref("b:10.1.2").is_selected_in(first)
    assert ComponentReference.for_mod("b").is_selected_in(first)
    assert not ref("a:0").is_selected_in(first)
