"""Tests for the PyDatalog-backed LogicEngine."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cortex.memory.models import Concept, ConceptDerivation, Fact, FactType
from cortex.validation.logic_engine import (
    FUNCTIONAL_PREDICATES,
    LogicEngine,
    _is_unsafe_predicate,
)
from cortex.validation.models import ValidationResult


def make_fact(
    fact_type: FactType,
    symbolic_repr: str = "",
    *,
    payload: dict[str, object] | None = None,
    natural_lang_repr: str = "",
    retracted: bool = False,
) -> Fact:
    """Build a Fact with only the fields the engine reads."""
    return Fact(
        type=fact_type,
        symbolic_repr=symbolic_repr,
        natural_lang_repr=natural_lang_repr,
        payload=payload or {},
        retracted_at=datetime.now(UTC) if retracted else None,
    )


def load_and_validate(facts: list[Fact], proof_chain: list[str]) -> ValidationResult:
    """Run one engine through load + validate for a compact test body."""
    engine = LogicEngine()
    engine.load_facts(facts)
    return engine.validate_derivation(
        ConceptDerivation(concept=Concept(), proof_chain=proof_chain)
    )


class TestFactsAsAxioms:
    """load_facts turns active facts into Datalog axioms."""

    def test_location_fact_supports_matching_conclusion(self):
        """Witness: fact location.home, proof location(user, home) -> valid.

        A fact-id subject (the rejected ticket encoding) would not answer this.
        """
        fact = make_fact(FactType.LOCATION, "location.home")

        result = load_and_validate([fact], ["location(user, home)"])

        assert result == ValidationResult(valid=True)

    def test_activity_fact_supports_matching_conclusion(self):
        """Witness: fact activity.working, proof activity(user, working) -> valid."""
        fact = make_fact(FactType.ACTIVITY, "activity.working")

        result = load_and_validate([fact], ["activity(user, working)"])

        assert result.valid is True

    def test_fact_with_atom_symbolic_repr_uses_last_ground_argument(self):
        """Witness: symbolic_repr location(user, home) -> value home, not 'user'."""
        fact = make_fact(FactType.LOCATION, "location(user, home)")

        result = load_and_validate([fact], ["location(user, home)"])

        assert result.valid is True

    def test_atom_symbolic_repr_supplies_the_predicate(self):
        """Witness: a CUSTOM fact with location(user, home) loads as location/2."""
        fact = make_fact(FactType.CUSTOM, "location(user, home)")

        result = load_and_validate([fact], ["location(user, home)"])

        assert result.valid is True

    def test_dot_symbolic_repr_uses_the_fact_type_as_predicate(self):
        """Witness: an ACTIVITY fact with location.home loads as activity/2."""
        fact = make_fact(FactType.ACTIVITY, "location.home")

        result = load_and_validate([fact], ["activity(user, home)"])

        assert result.valid is True

    def test_payload_place_is_a_fallback_for_value(self):
        """Witness: empty symbolic_repr + payload place=home -> location(user, home)."""
        fact = make_fact(FactType.LOCATION, payload={"place": "home"})

        result = load_and_validate([fact], ["location(user, home)"])

        assert result.valid is True

    def test_payload_activity_is_a_fallback_for_value(self):
        """Witness: empty symbolic_repr + payload activity=working -> activity(user, working)."""
        fact = make_fact(FactType.ACTIVITY, payload={"activity": "working"})

        result = load_and_validate([fact], ["activity(user, working)"])

        assert result.valid is True

    def test_natural_language_is_the_last_fallback_for_value(self):
        """Witness: empty symbolic_repr/payload, natural language 'home' -> home."""
        fact = make_fact(FactType.LOCATION, natural_lang_repr="home")

        result = load_and_validate([fact], ["location(user, home)"])

        assert result.valid is True

    def test_quoted_constant_is_sanitized_like_a_fact_value(self):
        """Witness: fact value New_York must match proof 'New York' after sanitisation."""
        fact = make_fact(FactType.LOCATION, "location.New York")

        result = load_and_validate([fact], ["location(user, 'New York')"])

        assert result.valid is True

    def test_numeric_constant_matches_a_string_fact_value(self):
        """Witness: numeric 42 must match the fact value '42' asserted as a string."""
        fact = make_fact(FactType.LOCATION, "location.42")

        result = load_and_validate([fact], ["location(user, 42)"])

        assert result.valid is True

    @pytest.mark.parametrize(
        "proof_chain",
        [["custom(user, anything)"], ["custom(user, '')"]],
        ids=["no-matching-value", "would-match-a-loaded-empty-constant"],
    )
    def test_fact_without_any_value_is_skipped(self, proof_chain):
        """Witness: a CUSTOM fact with no content cannot support a conclusion.

        The quoted empty constant discriminates a mutant that loads the empty
        fact as custom('user','').
        """
        fact = make_fact(FactType.CUSTOM)

        result = load_and_validate([fact], proof_chain)

        assert result.valid is False
        assert result.conflicting_facts == []


class TestValidDerivations:
    """Derivations that follow from the loaded facts are valid."""

    @pytest.mark.parametrize("separator", [":-", "<="])
    def test_rule_conclusion_derived_from_a_fact(self, separator):
        """Witness: both forms must be accepted and normalised for PyDatalog."""
        fact = make_fact(FactType.ACTIVITY, "activity.working")

        result = load_and_validate(
            [fact],
            [f"at_work(X) {separator} activity(X, working)", "at_work(user)"],
        )

        assert result == ValidationResult(valid=True)

    def test_conjunction_in_a_rule_body_is_loaded(self):
        """Witness: dropping the second body atom would make this derivation valid."""
        fact = make_fact(FactType.ACTIVITY, "activity.working")

        result = load_and_validate(
            [fact],
            [
                "productive(X) :- activity(X, working) & location(X, home)",
                "productive(user)",
            ],
        )

        assert result.valid is False
        assert result.conflicting_facts == []

    def test_non_functional_predicate_never_conflicts(self):
        """Witness: payments at starbucks + amazon coexist; no false conflict."""
        facts = [
            make_fact(FactType.PAYMENT, "payment.starbucks"),
            make_fact(FactType.PAYMENT, "payment.amazon"),
        ]

        result = load_and_validate(facts, ["payment(user, starbucks)"])

        assert result == ValidationResult(valid=True)


class TestConflicts:
    """A functional predicate with a different value is a contradiction."""

    def test_ground_conclusion_conflicts_with_different_fact_value(self):
        """Witness: fact location.home vs conclusion location(user, office)."""
        home = make_fact(FactType.LOCATION, "location.home")

        result = load_and_validate([home], ["location(user, office)"])

        assert result.valid is False
        assert result.conflicting_facts == [home]
        assert result.suggested_fix is not None
        assert "conflict" in result.suggested_fix.lower()
        assert "location" in result.suggested_fix

    def test_rule_body_value_conflicts_with_different_fact_value(self):
        """Witness: rule body location(X, office) vs fact location.home."""
        home = make_fact(FactType.LOCATION, "location.home")

        result = load_and_validate(
            [home],
            ["at_office(X) :- location(X, office)", "at_office(user)"],
        )

        assert result.valid is False
        assert result.conflicting_facts == [home]
        assert result.suggested_fix is not None
        assert "conflict" in result.suggested_fix.lower()

    def test_two_functional_values_without_a_ground_proof_value_are_all_returned(self):
        """Witness: loaded home+office, proof grounds no location value -> both."""
        home = make_fact(FactType.LOCATION, "location.home")
        office = make_fact(FactType.LOCATION, "location.office")
        payment = make_fact(FactType.PAYMENT, "payment.starbucks")

        result = load_and_validate([home, office, payment], ["payment(user, starbucks)"])

        assert result.valid is False
        assert result.conflicting_facts == [home, office]

    def test_two_functional_values_contradict_even_when_the_proof_names_both(self):
        """Witness: loaded home+office are a cross-fact contradiction regardless of the proof."""
        home = make_fact(FactType.LOCATION, "location.home")
        office = make_fact(FactType.LOCATION, "location.office")

        result = load_and_validate(
            [home, office],
            [
                "at_office(X) :- location(X, home) & location(X, office)",
                "at_office(user)",
            ],
        )

        assert result.valid is False
        assert result.conflicting_facts == [home, office]


class TestUnsupportedDerivations:
    """A conclusion that does not follow from the facts is invalid, without conflicts."""

    def test_no_loaded_facts(self):
        """Witness: an undefined predicate raises AttributeError, not a crash."""
        result = load_and_validate([], ["location(user, office)"])

        assert result.valid is False
        assert result.conflicting_facts == []
        assert result.suggested_fix is not None
        assert "support" in result.suggested_fix.lower()

    def test_unrelated_fact_does_not_support_conclusion(self):
        """Witness: activity.working cannot support location(user, home)."""
        fact = make_fact(FactType.ACTIVITY, "activity.working")

        result = load_and_validate([fact], ["location(user, home)"])

        assert result.valid is False
        assert result.conflicting_facts == []
        assert result.suggested_fix is not None
        assert "support" in result.suggested_fix.lower()


class TestRetractedFacts:
    """Retracted facts are not axioms."""

    def test_retracted_fact_is_ignored(self):
        """Witness: retracted location.home cannot support location(user, home)."""
        fact = make_fact(FactType.LOCATION, "location.home", retracted=True)

        result = load_and_validate([fact], ["location(user, home)"])

        assert result.valid is False
        assert result.conflicting_facts == []

    def test_loading_only_retracted_facts_clears_previous_axioms(self):
        """Witness: an engine that saw home must forget it after a retracted load."""
        engine = LogicEngine()
        engine.load_facts([make_fact(FactType.LOCATION, "location.home")])
        engine.load_facts([make_fact(FactType.LOCATION, "location.home", retracted=True)])

        result = engine.validate_derivation(
            ConceptDerivation(concept=Concept(), proof_chain=["location(user, home)"])
        )

        assert result.valid is False


class TestMalformedProofs:
    """Untrusted proof strings are rejected before PyDatalog executes them."""

    @pytest.mark.parametrize(
        "proof_chain",
        [
            [],
            [""],
            ["   "],
            ["location(user, office"],
            ["location()"],
            ["Location(user, office)"],
            ["_hidden(user, office)"],
            ["location(user, office) <- true"],
            ["location(X, office)"],
            ["location(user, office) :- location(user, 'off\\ice')"],
            ["location(user, office) & activity(user, working)"],
            ["location(user, office) | activity(user, working)"],
            ["exec(user, office)"],
            ["exit(user)"],
            ["print(user, home)"],
            ["at_office(X) :- quit(X)", "at_office(user)"],
            ["at_office(X) :- input(X)", "at_office(user)"],
        ],
        ids=[
            "empty-chain",
            "empty-clause",
            "blank-clause",
            "unclosed-atom",
            "zero-arguments",
            "uppercase-predicate",
            "underscore-predicate",
            "invalid-separator",
            "non-ground-conclusion",
            "backslash-in-string",
            "bare-conjunction",
            "disjunction",
            "denylisted-predicate",
            "process-exit-predicate",
            "builtin-print-predicate",
            "process-quit-predicate",
            "builtin-input-predicate",
        ],
    )
    def test_malformed_proof_is_invalid_without_raising(self, proof_chain):
        """Witness: every string above is outside the grammar; none may raise."""
        result = load_and_validate([], proof_chain)

        assert result.valid is False
        assert result.suggested_fix is not None
        assert result.suggested_fix.startswith("Invalid proof chain:")

    def test_injection_does_not_create_a_file(self, tmp_path):
        """Witness: open('<tmp>/pwn','w') must never reach Python's open."""
        target = tmp_path / "pwn"
        proof = f"open('{target}','w')"

        result = load_and_validate([], [proof])

        assert result.valid is False
        assert not target.exists()

    def test_injection_in_rule_body_does_not_create_a_file(self, tmp_path):
        """Witness: an injected body must be rejected too, not only a conclusion."""
        target = tmp_path / "pwn-body"
        proof = f"location(user, office) :- open('{target}','w')"

        result = load_and_validate([], [proof])

        assert result.valid is False
        assert not target.exists()

    def test_process_exit_in_a_fact_symbolic_repr_is_not_executed(self):
        """Witness: fact symbolic_repr exit(user) must not terminate the process."""
        fact = make_fact(FactType.CUSTOM, "exit(user)")
        engine = LogicEngine()

        engine.load_facts([fact])
        result = engine.validate_derivation(
            ConceptDerivation(concept=Concept(), proof_chain=["custom(user, anything)"])
        )

        assert result.valid is False

    @pytest.mark.parametrize(
        "symbolic_repr",
        ["quit(user)", "type(user, home)", "and(user, home)", "print(user, home)"],
        ids=["quit", "builtin-call", "python-keyword", "builtin-print"],
    )
    def test_unsafe_fact_symbolic_atom_is_not_executed(self, symbolic_repr):
        """Witness: a fact atom must never call a Python builtin or keyword."""
        fact = make_fact(FactType.CUSTOM, symbolic_repr)
        engine = LogicEngine()

        engine.load_facts([fact])
        result = engine.validate_derivation(
            ConceptDerivation(concept=Concept(), proof_chain=["custom(user, anything)"])
        )

        assert result.valid is False

    def test_clause_longer_than_500_characters_is_rejected(self):
        """Witness: a 501-character clause exceeds the grammar limit."""
        padding = "a" * 501
        proof = f"location(user, {padding})"

        result = load_and_validate([], [proof])

        assert result.valid is False
        assert result.suggested_fix is not None
        assert result.suggested_fix.startswith("Invalid proof chain:")

    def test_more_than_20_clauses_is_rejected(self):
        """Witness: 21 clauses exceed the grammar limit."""
        proof = [f"fact_{index}(user, value_{index})" for index in range(21)]

        result = load_and_validate([], proof)

        assert result.valid is False
        assert result.suggested_fix is not None
        assert result.suggested_fix.startswith("Invalid proof chain:")


class TestEngineIsolation:
    """The process-global PyDatalog database does not leak between runs."""

    def test_repeated_validation_is_stable(self):
        """Witness: validating the same derivation three times stays valid."""
        fact = make_fact(FactType.LOCATION, "location.home")
        engine = LogicEngine()
        engine.load_facts([fact])
        derivation = ConceptDerivation(
            concept=Concept(), proof_chain=["location(user, home)"]
        )

        results = [engine.validate_derivation(derivation) for _ in range(3)]

        assert results == [ValidationResult(valid=True)] * 3

    def test_two_engines_do_not_leak_facts(self):
        """Witness: B loads office after A loads home; A must still see only home."""
        home_engine = LogicEngine()
        office_engine = LogicEngine()
        home_engine.load_facts([make_fact(FactType.LOCATION, "location.home")])
        office_engine.load_facts([make_fact(FactType.LOCATION, "location.office")])
        home_derivation = ConceptDerivation(
            concept=Concept(), proof_chain=["location(user, home)"]
        )
        office_derivation = ConceptDerivation(
            concept=Concept(), proof_chain=["location(user, office)"]
        )

        home_result = home_engine.validate_derivation(home_derivation)
        office_result = office_engine.validate_derivation(office_derivation)

        assert home_result.valid is True
        assert office_result.valid is True

    def test_engine_with_no_facts_does_not_see_another_engines_facts(self):
        """Witness: without re-assertion, B's office would answer A's query."""
        empty_engine = LogicEngine()
        office_engine = LogicEngine()
        office_engine.load_facts([make_fact(FactType.LOCATION, "location.office")])

        result = empty_engine.validate_derivation(
            ConceptDerivation(concept=Concept(), proof_chain=["location(user, office)"])
        )

        assert result.valid is False
        assert result.conflicting_facts == []


class TestModels:
    """The dataclass shapes used by the engine."""

    def test_concept_derivation_defaults_source_fact_ids(self):
        """Witness: source_fact_ids is empty unless supplied."""
        concept = Concept(symbolic_repr="location.office")

        derivation = ConceptDerivation(
            concept=concept, proof_chain=["location(user, office)"]
        )

        assert derivation.concept is concept
        assert derivation.proof_chain == ["location(user, office)"]
        assert derivation.source_fact_ids == []

    def test_validation_result_defaults(self):
        """Witness: a valid result carries no facts and no suggested fix."""
        result = ValidationResult(valid=True)

        assert result.conflicting_facts == []
        assert result.suggested_fix is None


class TestFunctionalPredicates:
    """The functional-predicate registry is the contract for conflicts."""

    def test_registry_contains_location_and_activity(self):
        """Witness: both documented functional predicates are present."""
        assert FUNCTIONAL_PREDICATES == frozenset({"location", "activity"})


class TestUnsafePredicateGuard:
    """The pre-PyDatalog guard rejects names that could call Python code."""

    @pytest.mark.parametrize(
        "predicate",
        [
            "open",
            "exec",
            "eval",
            "compile",
            "globals",
            "locals",
            "print",
            "input",
            "exit",
            "quit",
            "__import__",
            "_hidden",
            "and",
            "class",
            "lambda",
        ],
    )
    def test_unsafe_predicates_are_rejected(self, predicate):
        """Witness: each name is unsafe on its own, so the guard must say True."""
        assert _is_unsafe_predicate(predicate) is True

    @pytest.mark.parametrize(
        "predicate", ["location", "activity", "payment", "user_preference"]
    )
    def test_normal_predicates_are_allowed(self, predicate):
        """Witness: ordinary domain predicates stay usable."""
        assert _is_unsafe_predicate(predicate) is False
