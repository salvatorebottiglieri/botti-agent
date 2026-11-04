"""PyDatalog-backed symbolic validation of concept derivations.

The proof chain is untrusted LLM output: it is parsed by a strict Datalog
grammar and re-serialised before it reaches ``pyDatalog``, which otherwise
compiles and executes the string as Python.
"""

from __future__ import annotations

import builtins
import keyword
import re
import threading
from dataclasses import dataclass
from typing import Final

from pyDatalog import pyDatalog, pyParser
from pyDatalog.util import DatalogError

from cortex.memory.models import ConceptDerivation, Fact
from cortex.validation.models import ValidationResult

FUNCTIONAL_PREDICATES: frozenset[str] = frozenset({"location", "activity"})

_MAX_CLAUSES: Final = 20
_MAX_CLAUSE_LENGTH: Final = 500
_SUBJECT: Final = "user"
# ``exit``/``quit`` raise SystemExit from the Python builtins that
# ``pyParser.load`` leaves callable; the grammar must reject them too.
_UNSAFE_PREDICATES: Final = frozenset({"exit", "quit"})

_PREDICATE_RE = re.compile(r"[a-z_][a-zA-Z0-9_]*")
_VARIABLE_RE = re.compile(r"[A-Z][a-zA-Z0-9_]*")
_BARE_CONSTANT_RE = re.compile(r"[a-z][a-zA-Z0-9_]*")
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")
_PREDICATE_SANITIZE_RE = re.compile(r"[^a-z0-9_]")
_VALUE_SANITIZE_RE = re.compile(r"[^a-zA-Z0-9_.-]")

# pyDatalog's database is process-global and is not re-entrant.
_ENGINE_LOCK = threading.RLock()


class _ProofError(ValueError):
    """A proof string does not conform to the accepted Datalog grammar."""


@dataclass(frozen=True)
class _Term:
    """A parsed atom argument, already serialised for PyDatalog."""

    serialized: str
    ground_value: str | None


@dataclass(frozen=True)
class _Atom:
    """A parsed Datalog atom."""

    predicate: str
    arguments: tuple[_Term, ...]

    def to_datalog(self) -> str:
        """Serialise the atom in PyDatalog syntax."""
        return f"{self.predicate}({','.join(term.serialized for term in self.arguments)})"


@dataclass(frozen=True)
class _Clause:
    """A parsed Datalog clause: a fact, or a rule with a body."""

    head: _Atom
    body: tuple[_Atom, ...]

    def to_datalog(self) -> str:
        """Serialise the clause in PyDatalog syntax."""
        if not self.body:
            return self.head.to_datalog()
        body = " & ".join(atom.to_datalog() for atom in self.body)
        return f"{self.head.to_datalog()} <= {body}"


class _ClauseParser:
    """Recursive-descent parser for the accepted Datalog subset."""

    def __init__(self, text: str) -> None:
        self._text = text
        self._pos = 0

    def parse(self) -> _Clause:
        """Parse one fact or rule and reject trailing input."""
        head = self._parse_atom()
        self._skip_whitespace()
        if self._at_end():
            return _Clause(head, ())

        if self._text.startswith(":-", self._pos) or self._text.startswith("<=", self._pos):
            self._pos += 2
        else:
            raise _ProofError(f"unexpected input at position {self._pos}")

        body = [self._parse_atom()]
        while self._accept("&"):
            body.append(self._parse_atom())

        self._skip_whitespace()
        if not self._at_end():
            raise _ProofError(f"unexpected input at position {self._pos}")
        return _Clause(head, tuple(body))

    def _parse_atom(self) -> _Atom:
        self._skip_whitespace()
        match = _PREDICATE_RE.match(self._text, self._pos)
        if match is None:
            raise _ProofError(f"expected a predicate at position {self._pos}")
        predicate = match.group(0)
        self._pos = match.end()
        if _is_unsafe_predicate(predicate):
            raise _ProofError(f"predicate {predicate!r} is not allowed")

        self._expect("(")
        arguments = [self._parse_argument()]
        while self._accept(","):
            arguments.append(self._parse_argument())
        self._expect(")")
        return _Atom(predicate, tuple(arguments))

    def _parse_argument(self) -> _Term:
        self._skip_whitespace()
        if self._at_end():
            raise _ProofError("expected a constant or variable")

        char = self._text[self._pos]
        if char in {"'", '"'}:
            return self._parse_quoted_constant()
        if char == "-" or char.isdigit():
            return self._parse_number()
        if char.isascii() and (char.isalpha() or char == "_"):
            return self._parse_identifier_argument()
        raise _ProofError(f"expected a constant or variable at position {self._pos}")

    def _parse_quoted_constant(self) -> _Term:
        quote = self._text[self._pos]
        self._pos += 1
        start = self._pos
        while not self._at_end():
            char = self._text[self._pos]
            if char == quote:
                value = self._text[start : self._pos]
                self._pos += 1
                sanitized = _sanitize_value(value)
                return _Term(f"'{sanitized}'", sanitized)
            if char == "\\":
                raise _ProofError("backslashes are not allowed in quoted strings")
            if char in {"'", '"'}:
                raise _ProofError("quotes are not allowed inside quoted strings")
            self._pos += 1
        raise _ProofError("unterminated quoted string")

    def _parse_number(self) -> _Term:
        match = _NUMBER_RE.match(self._text, self._pos)
        if match is None:
            raise _ProofError(f"invalid number at position {self._pos}")
        end = match.end()
        if end < len(self._text) and (
            self._text[end].isalnum() or self._text[end] in {"_", "."}
        ):
            raise _ProofError(f"invalid number at position {self._pos}")
        number = match.group(0)
        self._pos = end
        sanitized = _sanitize_value(number)
        # Facts are asserted as quoted strings; quote numbers so they can unify.
        return _Term(f"'{sanitized}'", sanitized)

    def _parse_identifier_argument(self) -> _Term:
        variable = _VARIABLE_RE.match(self._text, self._pos)
        if variable is not None:
            self._pos = variable.end()
            return _Term(variable.group(0), None)
        constant = _BARE_CONSTANT_RE.match(self._text, self._pos)
        if constant is not None:
            self._pos = constant.end()
            return _Term(constant.group(0), _sanitize_value(constant.group(0)))
        raise _ProofError(f"invalid constant or variable at position {self._pos}")

    def _expect(self, token: str) -> None:
        self._skip_whitespace()
        if not self._text.startswith(token, self._pos):
            raise _ProofError(f"expected {token!r} at position {self._pos}")
        self._pos += len(token)

    def _accept(self, token: str) -> bool:
        self._skip_whitespace()
        if not self._text.startswith(token, self._pos):
            return False
        self._pos += len(token)
        return True

    def _skip_whitespace(self) -> None:
        while not self._at_end() and self._text[self._pos].isspace():
            self._pos += 1

    def _at_end(self) -> bool:
        return self._pos >= len(self._text)


def _is_unsafe_predicate(predicate: str) -> bool:
    """Whether a predicate could call Python code or break the parser."""
    return (
        predicate.startswith("_")
        or predicate in _UNSAFE_PREDICATES
        or keyword.iskeyword(predicate)
        or hasattr(builtins, predicate)
    )


def _sanitize_predicate(predicate: object) -> str:
    """Coerce a fact predicate into the character set PyDatalog transports safely."""
    sanitized = _PREDICATE_SANITIZE_RE.sub("_", str(predicate).lower())
    if sanitized and not sanitized[0].isalpha():
        sanitized = f"p_{sanitized}"
    return sanitized


def _sanitize_value(value: object) -> str:
    """Coerce a fact value into the character set PyDatalog transports safely."""
    return _VALUE_SANITIZE_RE.sub("_", str(value).strip())


class LogicEngine:
    """Checks a concept derivation against facts loaded as Datalog axioms."""

    def __init__(self) -> None:
        self._facts: list[tuple[Fact, str, str]] = []
        self._by_predicate: dict[str, set[str]] = {}

    def load_facts(self, facts: list[Fact]) -> None:
        """Replace the loaded axioms with the active facts in ``facts``."""
        with _ENGINE_LOCK:
            self._facts = []
            self._by_predicate = {}
            for fact in facts:
                if not fact.is_active():
                    continue
                atom = self._fact_atom(fact)
                if atom is None:
                    continue
                predicate, value = atom
                self._facts.append((fact, predicate, value))
                self._by_predicate.setdefault(predicate, set()).add(value)
            self._reset_engine()

    def validate_derivation(self, derivation: ConceptDerivation) -> ValidationResult:
        """Return whether the derivation follows from the loaded facts."""
        with _ENGINE_LOCK:
            try:
                clauses = self._parse_proof(derivation.proof_chain)
                required = self._required_values(clauses)
                self._reset_engine()
                self._load_supporting_clauses(clauses[:-1])
                answer = self._ask(clauses[-1].head)
            except (_ProofError, SyntaxError, DatalogError, SystemExit) as exc:
                return ValidationResult(
                    valid=False, suggested_fix=f"Invalid proof chain: {exc}"
                )
            except Exception as exc:  # validation must never crash
                return ValidationResult(valid=False, suggested_fix=f"Validation failed: {exc}")

            conflicts = self._find_conflicts(required)
            if conflicts:
                conflicting_facts = [fact for fact, _ in conflicts]
                predicates = ", ".join(sorted({predicate for _, predicate in conflicts}))
                return ValidationResult(
                    valid=False,
                    conflicting_facts=conflicting_facts,
                    suggested_fix=(
                        f"Conflict with loaded fact(s) for functional predicate(s) "
                        f"{predicates}: the loaded facts are mutually exclusive or "
                        "contradict the derivation. Retract the conflicting facts or "
                        "revise the proof chain."
                    ),
                )

            if not answer:
                return ValidationResult(
                    valid=False,
                    suggested_fix=(
                        "The conclusion does not follow from the loaded facts. "
                        "Add supporting facts or revise the proof chain."
                    ),
                )

            return ValidationResult(valid=True)

    def _parse_proof(self, proof_chain: list[str]) -> list[_Clause]:
        if not proof_chain:
            raise _ProofError("proof chain is empty")
        if len(proof_chain) > _MAX_CLAUSES:
            raise _ProofError(f"proof chain has more than {_MAX_CLAUSES} clauses")

        clauses: list[_Clause] = []
        for index, raw_clause in enumerate(proof_chain, start=1):
            if not isinstance(raw_clause, str):
                raise _ProofError(f"clause {index} is not a string")
            if len(raw_clause) > _MAX_CLAUSE_LENGTH:
                raise _ProofError(
                    f"clause {index} is longer than {_MAX_CLAUSE_LENGTH} characters"
                )
            clause = raw_clause.strip()
            if not clause:
                raise _ProofError(f"clause {index} is empty")
            try:
                clauses.append(_ClauseParser(clause).parse())
            except _ProofError as exc:
                raise _ProofError(f"clause {index}: {exc}") from exc

        conclusion = clauses[-1]
        if conclusion.body:
            raise _ProofError("the conclusion must be a fact, not a rule")
        if any(term.ground_value is None for term in conclusion.head.arguments):
            raise _ProofError("the conclusion must be ground")
        return clauses

    def _required_values(self, clauses: list[_Clause]) -> dict[str, set[str]]:
        """Collect ground functional-predicate values required by the proof."""
        required: dict[str, set[str]] = {}
        for clause in clauses[:-1]:
            for atom in clause.body:
                self._collect_required(atom, required)
        self._collect_required(clauses[-1].head, required)
        return required

    @staticmethod
    def _collect_required(atom: _Atom, required: dict[str, set[str]]) -> None:
        if atom.predicate not in FUNCTIONAL_PREDICATES or len(atom.arguments) < 2:
            return
        value = atom.arguments[1].ground_value
        if value is None:
            return
        required.setdefault(atom.predicate, set()).add(value)

    def _find_conflicts(
        self, required: dict[str, set[str]]
    ) -> list[tuple[Fact, str]]:
        """Return loaded facts that contradict the proof, structurally."""
        conflicts: list[tuple[Fact, str]] = []
        for fact, predicate, value in self._facts:
            if predicate not in FUNCTIONAL_PREDICATES:
                continue
            if len(self._by_predicate[predicate]) > 1:
                conflicts.append((fact, predicate))
                continue
            required_values = required.get(predicate)
            if required_values and value not in required_values:
                conflicts.append((fact, predicate))
        return conflicts

    def _reset_engine(self) -> None:
        """Clear the process-global engine and re-assert the stored facts."""
        pyDatalog.clear()
        for _, predicate, value in self._facts:
            self._assert_atom(f"{predicate}('{_SUBJECT}','{value}')")

    def _load_supporting_clauses(self, clauses: list[_Clause]) -> None:
        for clause in clauses:
            if clause.body:
                pyParser.load(clause.to_datalog(), newglobals={})
            else:
                self._assert_atom(clause.head.to_datalog())

    def _assert_atom(self, atom: str) -> None:
        pyParser.load(f"+ {atom}", newglobals={})

    @staticmethod
    def _ask(atom: _Atom) -> object | None:
        try:
            answer: object = pyDatalog.ask(atom.to_datalog())
        except AttributeError:
            # PyDatalog raises this for an undefined predicate after clear().
            return None
        return answer

    def _fact_atom(self, fact: Fact) -> tuple[str, str] | None:
        """Resolve a fact to ``(predicate, sanitised value)`` or None if empty."""
        symbolic = fact.symbolic_repr.strip()

        if symbolic:
            parsed = self._symbolic_atom(symbolic)
            if parsed is not None:
                predicate, value = parsed
                return self._finish_fact_atom(predicate, value)

        if "." in symbolic:
            value = symbolic.partition(".")[2]
            return self._finish_fact_atom(fact.type.value, value)

        for key in ("place", "activity", "value"):
            if key in fact.payload and fact.payload[key] not in (None, ""):
                return self._finish_fact_atom(fact.type.value, fact.payload[key])
        if fact.natural_lang_repr:
            return self._finish_fact_atom(fact.type.value, fact.natural_lang_repr)
        if symbolic:
            return self._finish_fact_atom(fact.type.value, symbolic)
        return None

    @staticmethod
    def _finish_fact_atom(predicate: object, value: object) -> tuple[str, str] | None:
        sanitized_predicate = _sanitize_predicate(predicate)
        sanitized_value = _sanitize_value(value)
        if not sanitized_predicate or not sanitized_value:
            return None
        return sanitized_predicate, sanitized_value

    @staticmethod
    def _symbolic_atom(symbolic: str) -> tuple[str, str] | None:
        """Parse a ``predicate(arg, ...)`` symbolic repr, if it is one."""
        try:
            clause = _ClauseParser(symbolic).parse()
        except _ProofError:
            return None
        if clause.body or not clause.head.arguments:
            return None
        predicate = clause.head.predicate
        if _is_unsafe_predicate(predicate):
            return None
        for term in reversed(clause.head.arguments):
            if term.ground_value is not None:
                return clause.head.predicate, term.ground_value
        return None
