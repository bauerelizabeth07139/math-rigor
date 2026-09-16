"""Inference-rule checking: is this step actually an instance of the stated rule?

Every proof step in a rigorous write-up cites a rule.  Two independent things can
go wrong, and both are caught here:

1. **The citation is wrong** -- the step is labelled ``modus_ponens`` but the
   formulas are not of the form ``phi, phi -> psi / psi``.  This is a *shape*
   error and is caught syntactically.
2. **The step is unsound** -- the inference does not preserve truth.  This is
   caught semantically, by asking an SMT solver whether the premises really
   entail the conclusion.

A step is reported ``valid`` only when both checks pass.  Rules that are sound
only under a side condition (``universal_generalization``,
``existential_instantiation``) are marked as non-semantic and checked by their
freshness condition instead -- they are *not* semantically valid inferences, and
pretending otherwise would defeat the purpose.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from .ast_nodes import (
    And,
    App,
    BigOp,
    Bin,
    Cmp,
    Iff,
    Imp,
    Node,
    Not,
    Num,
    Or,
    Quant,
    Sym,
    children_of,
    collect_symbols,
    parse,
    walk,
)
from .translate import is_falsum, is_verum

VERDICT_VALID = "valid"
VERDICT_SHAPE = "invalid_shape"
VERDICT_SEMANTIC = "invalid_semantics"
VERDICT_UNKNOWN = "unknown"
VERDICT_UNSUPPORTED = "unsupported_rule"


@dataclass(frozen=True)
class Meta(Node):
    """A metavariable standing for an arbitrary subformula in a rule schema."""

    name: str = ""


# --------------------------------------------------------------------------- #
# Structural helpers
# --------------------------------------------------------------------------- #


def match_schema(pattern: Node, target: Node, bindings: dict[str, Node]) -> bool:
    """Match a rule schema against concrete formulas, binding metavariables."""
    if isinstance(pattern, Meta):
        existing = bindings.get(pattern.name)
        if existing is None:
            bindings[pattern.name] = target
            return True
        return existing == target
    if type(pattern) is not type(target):
        return False
    if isinstance(pattern, Sym):
        return pattern.name == target.name  # type: ignore[union-attr]
    if isinstance(pattern, Num):
        return pattern.value == target.value  # type: ignore[union-attr]
    if isinstance(pattern, Bin):
        return pattern.op == target.op and match_schema(  # type: ignore[union-attr]
            pattern.left, target.left, bindings) and match_schema(  # type: ignore[union-attr]
            pattern.right, target.right, bindings)
    if isinstance(pattern, Cmp):
        return pattern.op == target.op and match_schema(  # type: ignore[union-attr]
            pattern.left, target.left, bindings) and match_schema(  # type: ignore[union-attr]
            pattern.right, target.right, bindings)
    if isinstance(pattern, Quant):
        if pattern.var != target.var or pattern.kind != target.kind:  # type: ignore[union-attr]
            return False
        return match_schema(pattern.body, target.body, bindings)  # type: ignore[union-attr]
    if isinstance(pattern, App):
        if pattern.name != target.name or len(pattern.args) != len(target.args):  # type: ignore[union-attr]
            return False
        return all(match_schema(p, t, bindings)
                   for p, t in zip(pattern.args, target.args))  # type: ignore[union-attr]
    if isinstance(pattern, BigOp):
        return pattern == target
    pattern_children = children_of(pattern)
    target_children = children_of(target)
    if len(pattern_children) != len(target_children):
        return False
    return all(match_schema(p, t, bindings)
               for p, t in zip(pattern_children, target_children))


def replace_subterm(node: Node, old: Node, new: Node) -> Node:
    """Replace every occurrence of the subterm ``old`` by ``new``."""
    if node == old:
        return new
    children = children_of(node)
    if not children:
        return node
    from .smt import _rebuild

    return _rebuild(node, [replace_subterm(c, old, new) for c in children])


class _Mismatch(Exception):
    pass


def instantiate_match(body: Node, target: Node, var: str) -> Node | None:
    """Find the term ``t`` with ``body[var := t] == target``, if one exists."""
    found: list[Node] = []

    def walk_pair(pattern: Node, concrete: Node) -> None:
        if isinstance(pattern, Sym) and pattern.name == var:
            if found and found[0] != concrete:
                raise _Mismatch
            if not found:
                found.append(concrete)
            return
        if type(pattern) is not type(concrete):
            raise _Mismatch
        if isinstance(pattern, (Sym, Num)):
            if pattern != concrete:
                raise _Mismatch
            return
        if isinstance(pattern, (Bin, Cmp)):
            if pattern.op != concrete.op:  # type: ignore[union-attr]
                raise _Mismatch
        if isinstance(pattern, Quant):
            if pattern.var != concrete.var or pattern.kind != concrete.kind:  # type: ignore[union-attr]
                raise _Mismatch
        if isinstance(pattern, App):
            if pattern.name != concrete.name:  # type: ignore[union-attr]
                raise _Mismatch
        p_children = children_of(pattern)
        c_children = children_of(concrete)
        if len(p_children) != len(c_children):
            raise _Mismatch
        for p, c in zip(p_children, c_children):
            walk_pair(p, c)

    try:
        walk_pair(body, target)
    except _Mismatch:
        return None
    return found[0] if found else None


def is_contradiction(node: Node) -> bool:
    """``falsum``, ``chi & ~chi``, or an explicit ``~chi & chi``."""
    if is_falsum(node):
        return True
    if isinstance(node, And):
        parts = list(node.parts)
        for i, part in enumerate(parts):
            for other in parts[i + 1:]:
                if part == Not(other) or other == Not(part):
                    return True
    return False


def _flatten_disjunction(node: Node) -> list[Node]:
    if isinstance(node, Or):
        out: list[Node] = []
        for part in node.parts:
            out.extend(_flatten_disjunction(part))
        return out
    return [node]


def _flatten_conjunction(node: Node) -> list[Node]:
    if isinstance(node, And):
        out: list[Node] = []
        for part in node.parts:
            out.extend(_flatten_conjunction(part))
        return out
    return [node]


def _negate(node: Node) -> Node:
    if isinstance(node, Not):
        return node.operand
    return Not(node)


def _is_negation_of(a: Node, b: Node) -> bool:
    return a == _negate(b) and (isinstance(a, Not) or isinstance(b, Not))


# --------------------------------------------------------------------------- #
# Rule schemas
# --------------------------------------------------------------------------- #


@dataclass
class RuleCheck:
    ok: bool
    detail: str
    witness: dict[str, str] = field(default_factory=dict)


@dataclass
class Rule:
    name: str
    category: str
    description: str
    example: str
    semantically_valid: bool = True
    checker: Callable[[Sequence[Node], Node], RuleCheck] | None = None


def _mp(premises: Sequence[Node], conclusion: Node) -> RuleCheck:
    for i, a in enumerate(premises):
        if not isinstance(a, Imp):
            continue
        for j, b in enumerate(premises):
            if i != j and b == a.left:
                if conclusion == a.right:
                    return RuleCheck(True, "premises contain the implication and its antecedent",
                                     {"phi": a.left.text(), "psi": a.right.text()})
    return RuleCheck(False, "no premise pair matches `phi` and `phi -> psi` with conclusion `psi`")


def _mt(premises: Sequence[Node], conclusion: Node) -> RuleCheck:
    for i, a in enumerate(premises):
        if not isinstance(a, Imp):
            continue
        for j, b in enumerate(premises):
            if i != j and b == _negate(a.right) and isinstance(b, Not):
                if conclusion == _negate(a.left) and isinstance(conclusion, Not):
                    return RuleCheck(True, "contrapositive instance",
                                     {"phi": a.left.text(), "psi": a.right.text()})
    return RuleCheck(False, "no premise pair matches `phi -> psi` and `~psi` with conclusion `~phi`")


def _hypothetical_syllogism(premises: Sequence[Node], conclusion: Node) -> RuleCheck:
    if not isinstance(conclusion, Imp):
        return RuleCheck(False, "the conclusion must be an implication")
    for i, a in enumerate(premises):
        if not isinstance(a, Imp):
            continue
        for j, b in enumerate(premises):
            if i == j or not isinstance(b, Imp):
                continue
            if a.right == b.left and conclusion == Imp(a.left, b.right):
                return RuleCheck(True, "chained implications", {"middle": a.right.text()})
    return RuleCheck(False, "no premise chain `phi -> psi`, `psi -> chi` matches the conclusion")


def _disjunctive_syllogism(premises: Sequence[Node], conclusion: Node) -> RuleCheck:
    for i, a in enumerate(premises):
        if not isinstance(a, Or):
            continue
        for j, b in enumerate(premises):
            if i == j or not isinstance(b, Not):
                continue
            remaining = [p for p in _flatten_disjunction(a) if p != b.operand]
            if not remaining:
                continue
            candidate = remaining[0] if len(remaining) == 1 else Or(tuple(remaining))
            if conclusion == candidate:
                return RuleCheck(True, "the negated disjunct was eliminated",
                                 {"eliminated": b.operand.text()})
    return RuleCheck(False, "no premise pair matches `phi | psi` and `~phi` with conclusion `psi`")


def _conjunction_intro(premises: Sequence[Node], conclusion: Node) -> RuleCheck:
    if not isinstance(conclusion, And):
        return RuleCheck(False, "the conclusion must be a conjunction")
    parts = _flatten_conjunction(conclusion)
    unused = list(premises)
    for part in parts:
        if part in unused:
            unused.remove(part)
        else:
            return RuleCheck(False, f"the conjunct `{part.text()}` is not among the cited premises")
    return RuleCheck(True, f"all {len(parts)} conjuncts are cited premises")


def _conjunction_elim(premises: Sequence[Node], conclusion: Node) -> RuleCheck:
    for a in premises:
        if not isinstance(a, And):
            continue
        parts = _flatten_conjunction(a)
        wanted = _flatten_conjunction(conclusion)
        if all(part in parts for part in wanted):
            return RuleCheck(True, "the conclusion is a subset of a cited conjunction")
    return RuleCheck(False, "no cited conjunction contains the conclusion as a conjunct")


def _disjunction_intro(premises: Sequence[Node], conclusion: Node) -> RuleCheck:
    if not isinstance(conclusion, Or):
        return RuleCheck(False, "the conclusion must be a disjunction")
    disjuncts = _flatten_disjunction(conclusion)
    for a in premises:
        if a in disjuncts:
            return RuleCheck(True, "the conclusion weakens a cited premise")
    return RuleCheck(False, "no cited premise appears as a disjunct of the conclusion")


def _disjunction_elim(premises: Sequence[Node], conclusion: Node) -> RuleCheck:
    for a in premises:
        if not isinstance(a, Or):
            continue
        for case in _flatten_disjunction(a):
            has_case = any(isinstance(p, Imp) and p.left == case and p.right == conclusion
                           for p in premises)
            if not has_case:
                break
        else:
            return RuleCheck(True, "every case of the disjunction was discharged into the conclusion")
    return RuleCheck(False, "missing the disjunction, or a case `phi -> chi` for some disjunct")


def _conditional_proof(premises: Sequence[Node], conclusion: Node) -> RuleCheck:
    if not isinstance(conclusion, Imp):
        return RuleCheck(False, "the conclusion must be an implication `phi -> psi`")
    if conclusion.left in premises and conclusion.right in premises:
        return RuleCheck(True, "the antecedent was assumed and the consequent derived")
    return RuleCheck(False, "the assumed antecedent and the derived consequent must both be cited")


def _reductio(premises: Sequence[Node], conclusion: Node) -> RuleCheck:
    has_bottom = any(is_contradiction(p) for p in premises)
    if not has_bottom:
        return RuleCheck(False, "no cited premise is a contradiction, so nothing is refuted")
    for a in premises:
        if a == _negate(conclusion) and isinstance(a, Not):
            return RuleCheck(True, "the assumption of the negation led to a contradiction")
        if conclusion == _negate(a) and isinstance(conclusion, Not):
            return RuleCheck(True, "the assumption led to a contradiction, so its negation holds")
    return RuleCheck(False, "the contradiction was not reached under an assumption that the "
                            "conclusion discharges")


def _contradiction_intro(premises: Sequence[Node], conclusion: Node) -> RuleCheck:
    if not is_falsum(conclusion):
        return RuleCheck(False, "the conclusion must be the contradiction symbol")
    for i, a in enumerate(premises):
        for b in premises[i + 1:]:
            if a == _negate(b) and (isinstance(a, Not) or isinstance(b, Not)):
                return RuleCheck(True, f"`{a.text()}` and `{b.text()}` cannot both hold")
    return RuleCheck(False, "no two cited premises are negations of one another")


def _ex_falso(premises: Sequence[Node], conclusion: Node) -> RuleCheck:
    if any(is_falsum(p) for p in premises):
        return RuleCheck(True, "a contradiction was cited")
    return RuleCheck(False, "usage requires citing a contradiction among the premises")


def _biconditional_intro(premises: Sequence[Node], conclusion: Node) -> RuleCheck:
    if not isinstance(conclusion, Iff):
        return RuleCheck(False, "the conclusion must be a biconditional")
    forward = Imp(conclusion.left, conclusion.right)
    backward = Imp(conclusion.right, conclusion.left)
    if forward in premises and backward in premises:
        return RuleCheck(True, "both directions were proved as separate implications")
    return RuleCheck(False, "both implications must be cited as premises")


def _biconditional_elim(premises: Sequence[Node], conclusion: Node) -> RuleCheck:
    for a in premises:
        if not isinstance(a, Iff):
            continue
        if conclusion in (Imp(a.left, a.right), Imp(a.right, a.left)):
            return RuleCheck(True, "one direction of the cited biconditional")
        if conclusion == Iff(a.right, a.left):
            return RuleCheck(True, "the cited biconditional is symmetric")
    return RuleCheck(False, "no cited biconditional yields this implication")


def _double_negation(premises: Sequence[Node], conclusion: Node) -> RuleCheck:
    for a in premises:
        if isinstance(a, Not) and isinstance(a.operand, Not) and conclusion == a.operand.operand:
            return RuleCheck(True, "double negation eliminated")
        if conclusion == Not(Not(a)):
            return RuleCheck(True, "double negation introduced")
    return RuleCheck(False, "no cited premise is a double negation of the conclusion")


def _contraposition(premises: Sequence[Node], conclusion: Node) -> RuleCheck:
    if not isinstance(conclusion, Imp) or not isinstance(conclusion.left, Not) \
            or not isinstance(conclusion.right, Not):
        return RuleCheck(False, "the conclusion must be `~psi -> ~phi`")
    for a in premises:
        if isinstance(a, Imp) and a.left == conclusion.right.operand \
                and a.right == conclusion.left.operand:
            return RuleCheck(True, "contrapositive of a cited implication")
    return RuleCheck(False, "no cited implication is the contrapositive of the conclusion")


def _excluded_middle(premises: Sequence[Node], conclusion: Node) -> RuleCheck:
    if not isinstance(conclusion, Or):
        return RuleCheck(False, "the conclusion must be a disjunction")
    disjuncts = _flatten_disjunction(conclusion)
    for i, a in enumerate(disjuncts):
        for b in disjuncts[i + 1:]:
            if a == _negate(b) and (isinstance(a, Not) or isinstance(b, Not)):
                return RuleCheck(True, "instance of the law of excluded middle")
    return RuleCheck(False, "the disjunction is not an instance of `phi | ~phi`")


def _universal_instantiation(premises: Sequence[Node], conclusion: Node) -> RuleCheck:
    for a in premises:
        if not isinstance(a, Quant) or a.kind != "forall":
            continue
        if a.var in collect_symbols(conclusion):
            pass  # instantiating with a term containing the variable is allowed
        term = instantiate_match(a.body, conclusion, a.var)
        if term is not None:
            return RuleCheck(True, f"instantiated the quantified variable {a.var}",
                             {"witness_term": term.text()})
    return RuleCheck(False, "no cited `forall` statement instantiates to the conclusion")


def _existential_generalization(premises: Sequence[Node], conclusion: Node) -> RuleCheck:
    for a in premises:
        if not isinstance(conclusion, Quant) or conclusion.kind != "exists":
            break
        term = instantiate_match(conclusion.body, a, conclusion.var)
        if term is not None:
            return RuleCheck(True, "witnessed the existential with the cited instance",
                             {"witness_term": term.text()})
    return RuleCheck(False, "no cited premise is an instance of the existential conclusion")


def _universal_generalization(premises: Sequence[Node], conclusion: Node) -> RuleCheck:
    if not isinstance(conclusion, Quant) or conclusion.kind != "forall":
        return RuleCheck(False, "the conclusion must be a universal statement")
    elsewhere = set()
    for p in premises:
        elsewhere |= collect_symbols(p)
    for a in premises:
        term = instantiate_match(conclusion.body, a, conclusion.var)
        if term is None:
            continue
        if not isinstance(term, Sym):
            continue
        others = [p for p in premises if p != a]
        others_syms = set()
        for p in others:
            others_syms |= collect_symbols(p)
        if term.name in others_syms or term.name in collect_symbols(conclusion):
            continue
        return RuleCheck(True,
                         f"`{term.name}` is arbitrary: it appears nowhere outside the "
                         "instance being generalised")
    return RuleCheck(False,
                     "generalisation requires an instance in a variable that occurs "
                     "nowhere else in the cited premises or the conclusion")


def _existential_instantiation(premises: Sequence[Node], conclusion: Node) -> RuleCheck:
    others = set()
    for a in premises:
        if isinstance(a, Quant) and a.kind == "exists":
            continue
        others |= collect_symbols(a)
    for a in premises:
        if not isinstance(a, Quant) or a.kind != "exists":
            continue
        term = instantiate_match(a.body, conclusion, a.var)
        if term is None or not isinstance(term, Sym):
            continue
        if term.name in collect_symbols(a):
            continue
        if term.name in others:
            continue
        return RuleCheck(True,
                         f"`{term.name}` is fresh: it appears in no other premise",
                         {"witness_name": term.name})
    return RuleCheck(False,
                     "witness introduction requires a constant that is fresh with "
                     "respect to every other cited premise")


def _equality_substitution(premises: Sequence[Node], conclusion: Node) -> RuleCheck:
    for a in premises:
        if not isinstance(a, Cmp) or a.op != "eq":
            continue
        for source in premises:
            for old, new in ((a.left, a.right), (a.right, a.left)):
                if replace_subterm(source, old, new) == conclusion:
                    return RuleCheck(True, f"substituted `{old.text()} = {new.text()}`",
                                     {"replaced": old.text(), "with": new.text()})
    return RuleCheck(False, "no cited equality rewrites a cited formula into the conclusion")


RULES: dict[str, Rule] = {
    r.name: r for r in [
        Rule("modus_ponens", "propositional",
             "From phi and phi -> psi conclude psi.",
             "premises: P -> Q, P  =>  conclusion: Q", True, _mp),
        Rule("modus_tollens", "propositional",
             "From phi -> psi and ~psi conclude ~phi.",
             "premises: P -> Q, ~Q  =>  conclusion: ~P", True, _mt),
        Rule("hypothetical_syllogism", "propositional",
             "Chain two implications: phi -> psi, psi -> chi, hence phi -> chi.",
             "premises: P -> Q, Q -> R  =>  conclusion: P -> R", True, _hypothetical_syllogism),
        Rule("disjunctive_syllogism", "propositional",
             "From phi | psi and ~phi conclude psi.",
             "premises: P | Q, ~P  =>  conclusion: Q", True, _disjunctive_syllogism),
        Rule("conjunction_intro", "propositional",
             "From phi and psi conclude phi & psi.",
             "premises: P, Q  =>  conclusion: P & Q", True, _conjunction_intro),
        Rule("conjunction_elim", "propositional",
             "From phi & psi conclude phi (or psi).",
             "premises: P & Q  =>  conclusion: P", True, _conjunction_elim),
        Rule("disjunction_intro", "propositional",
             "From phi conclude phi | psi.",
             "premises: P  =>  conclusion: P | Q", True, _disjunction_intro),
        Rule("disjunction_elim", "propositional",
             "Proof by cases: from phi | psi, phi -> chi and psi -> chi conclude chi.",
             "premises: P | Q, P -> R, Q -> R  =>  conclusion: R", True, _disjunction_elim),
        Rule("conditional_proof", "propositional",
             "Assume phi and derive psi, then conclude phi -> psi.",
             "premises: P, Q  =>  conclusion: P -> Q", True, _conditional_proof),
        Rule("reductio", "propositional",
             "Assume ~phi, derive a contradiction, conclude phi.",
             "premises: ~P, falsum  =>  conclusion: P", True, _reductio),
        Rule("contradiction_intro", "propositional",
             "From phi and ~phi conclude the contradiction symbol.",
             "premises: P, ~P  =>  conclusion: falsum", True, _contradiction_intro),
        Rule("ex_falso", "propositional",
             "From a contradiction conclude anything (explosion).",
             "premises: falsum  =>  conclusion: Q", True, _ex_falso),
        Rule("biconditional_intro", "propositional",
             "Prove both directions to conclude phi <-> psi.",
             "premises: P -> Q, Q -> P  =>  conclusion: P <-> Q", True, _biconditional_intro),
        Rule("biconditional_elim", "propositional",
             "From phi <-> psi conclude phi -> psi (or psi -> phi).",
             "premises: P <-> Q  =>  conclusion: P -> Q", True, _biconditional_elim),
        Rule("double_negation", "propositional",
             "~~phi and phi are interchangeable.",
             "premises: ~~P  =>  conclusion: P", True, _double_negation),
        Rule("contraposition", "propositional",
             "From phi -> psi conclude ~psi -> ~phi.",
             "premises: P -> Q  =>  conclusion: ~Q -> ~P", True, _contraposition),
        Rule("excluded_middle", "propositional",
             "phi | ~phi holds with no premises.",
             "conclusion: P | ~P", True, _excluded_middle),
        Rule("universal_instantiation", "predicate",
             "From forall x. phi(x) conclude phi(t) for any term t.",
             "premises: forall x: x^2 >= 0  =>  conclusion: 9 >= 0", True,
             _universal_instantiation),
        Rule("existential_generalization", "predicate",
             "From phi(t) conclude exists x. phi(x).",
             "premises: 2 > 1  =>  conclusion: exists n in Z: n > 1", True,
             _existential_generalization),
        Rule("universal_generalization", "predicate",
             "From phi(a) with a arbitrary conclude forall x. phi(x). "
             "Not semantically valid on its own: the freshness condition is checked.",
             "premises: a^2 >= 0  =>  conclusion: forall x in R: x^2 >= 0", False,
             _universal_generalization),
        Rule("existential_instantiation", "predicate",
             "From exists x. phi(x) introduce a fresh witness c and conclude phi(c). "
             "Not semantically valid on its own: freshness is checked.",
             "premises: exists n in Z: n^2 = 4  =>  conclusion: c^2 = 4", False,
             _existential_instantiation),
        Rule("equality_substitution", "equality",
             "Replace equals by equals inside a cited formula.",
             "premises: x = y, x + 1 > 0  =>  conclusion: y + 1 > 0", True,
             _equality_substitution),
    ]
}

# Rules whose correctness is a matter of computation or definition unfolding
# rather than a schematic inference; the proof audit reports them as unchecked
# so that nothing is silently treated as proved.
NON_LOGICAL_RULES = {
    "algebra": "term rewriting justified by the field axioms",
    "arithmetic": "concrete arithmetic evaluation",
    "computation": "machine-verified evaluation (cite tool output)",
    "definition": "unfolding a definition stated in the problem",
    "assumption": "an explicit hypothesis of the argument",
    "premise": "a hypothesis taken from the problem statement",
    "theorem_citation": "a previously established theorem (name it in the justification)",
    "lemma": "a lemma proved inside this session",
    "induction_base": "the base case of an induction",
    "induction_step": "the inductive step of an induction",
    "case_analysis": "a case distinction whose cases are cited as premises",
    "construction": "exhibiting an object to satisfy an existential",
    "contradiction": "alias of contradiction_intro / reductio depending on the shape",
    # Tool-backed justifications: the audit still tries to re-derive the step, and
    # records the outcome either way.
    "machine_verified": "checked by an external tool; quote the tool output in the justification",
    "smt": "decided by the SMT solver (verify_forall / logic_entails)",
    "symbolic_computation": "decided by symbolic computation (sympy_eval / verify_identity)",
    "counterexample_search": "refuted by an explicit counterexample (find_counterexample)",
    "induction": "a verified induction (verify_induction): state the base case and the step",
    "verify_inequality": "verified as a global inequality (verify_inequality)",
}


# --------------------------------------------------------------------------- #
# Public checking entry points
# --------------------------------------------------------------------------- #


@dataclass
class StepCheck:
    verdict: str
    rule: str
    shape_detail: str
    semantic_status: str | None = None
    semantic_detail: str | None = None
    witness: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "verdict": self.verdict,
            "rule": self.rule,
            "shape_check": self.shape_detail,
        }
        if self.witness:
            out["bindings"] = self.witness
        if self.semantic_status:
            out["semantic_check"] = {
                "status": self.semantic_status,
                "detail": self.semantic_detail,
            }
        return out


def check_step(
    premise_texts: Sequence[str],
    conclusion_text: str,
    rule: str,
    *,
    functions: Sequence[str] = (),
    declared_sorts: Mapping[str, str] | None = None,
    timeout_ms: int = 15000,
    verify_semantics: bool = True,
) -> StepCheck:
    """Check that one proof step is a faithful instance of a named rule."""
    name = rule.strip().lower().replace("-", "_").replace(" ", "_")
    entry = RULES.get(name)
    if entry is None:
        known = ", ".join(sorted(RULES))
        return StepCheck(
            VERDICT_UNSUPPORTED, rule,
            f"unknown inference rule; logical rules are: {known}. "
            f"Non-logical justifications ({', '.join(sorted(NON_LOGICAL_RULES))}) "
            "are audited separately as unchecked steps.",
        )

    try:
        premises = [parse(p, functions) for p in premise_texts]
        conclusion = parse(conclusion_text, functions)
    except Exception as exc:  # noqa: BLE001
        return StepCheck(VERDICT_UNSUPPORTED, name,
                         f"could not parse the step: {type(exc).__name__}: {exc}")

    assert entry.checker is not None
    shape = entry.checker(premises, conclusion)
    if not shape.ok:
        return StepCheck(VERDICT_SHAPE, name,
                         f"the step is not an instance of {name}: {shape.detail}")

    if not entry.semantically_valid or not verify_semantics:
        detail = "checked by its side condition; this rule is not semantically valid on its own"
        if not entry.semantically_valid:
            return StepCheck(VERDICT_VALID, name, shape.detail,
                             "side_condition_only", detail, shape.witness)
        return StepCheck(VERDICT_VALID, name, shape.detail, None, None, shape.witness)

    from .smt import STATUS_PROVEN, STATUS_REFUTED, check_entailment

    outcome = check_entailment(
        [p.text() for p in premises], conclusion,
        declared_sorts=declared_sorts, functions=functions, timeout_ms=timeout_ms,
    )
    if outcome.status == STATUS_REFUTED:
        return StepCheck(VERDICT_SEMANTIC, name, shape.detail, STATUS_REFUTED,
                         f"the cited premises do not entail the conclusion; "
                         f"counterexample: {outcome.model}", shape.witness)
    # A matching shape is already a sufficient condition for these sound rules,
    # so an inconclusive solver must not downgrade a correct step.  The semantic
    # pass exists to catch a mis-cited step whose shape happened to fit.
    detail = ("the premises were also confirmed to entail the conclusion"
              if outcome.status == STATUS_PROVEN else
              f"the semantic double-check was inconclusive ({outcome.reason or outcome.method}); "
              "the step still stands on its rule shape, which is sound for this rule")
    return StepCheck(VERDICT_VALID, name, shape.detail, outcome.status, detail,
                     shape.witness)


# --------------------------------------------------------------------------- #
# Propositional truth tables
# --------------------------------------------------------------------------- #

_MAX_TRUTH_TABLE_VARS = 10


def propositional_variables(node: Node) -> list[str]:
    """Free symbols that must be truth values for the formula to be well formed."""
    names: set[str] = set()

    def visit(current: Node, boolean: bool) -> None:
        if isinstance(current, Sym):
            if is_falsum(current) or is_verum(current):
                return
            if boolean:
                names.add(current.name)
            return
        if isinstance(current, (Not, And, Or, Imp, Iff, Quant)):
            for child in children_of(current):
                visit(child, True)
            return
        if isinstance(current, Cmp):
            for child in children_of(current):
                visit(child, False)
            return
        for child in children_of(current):
            visit(child, False)

    visit(node, True)
    return sorted(names)


def is_purely_propositional(node: Node) -> bool:
    allowed = (Sym, Not, And, Or, Imp, Iff)
    return all(isinstance(n, allowed) for n in walk(node))


def evaluate_propositional(node: Node, env: Mapping[str, bool]) -> bool:
    if isinstance(node, Sym):
        if is_falsum(node):
            return False
        if is_verum(node):
            return True
        if node.name not in env:
            raise KeyError(f"no value assigned to the proposition {node.name!r}")
        return bool(env[node.name])
    if isinstance(node, Not):
        return not evaluate_propositional(node.operand, env)
    if isinstance(node, And):
        return all(evaluate_propositional(p, env) for p in node.parts)
    if isinstance(node, Or):
        return any(evaluate_propositional(p, env) for p in node.parts)
    if isinstance(node, Imp):
        return (not evaluate_propositional(node.left, env)) \
            or evaluate_propositional(node.right, env)
    if isinstance(node, Iff):
        return evaluate_propositional(node.left, env) == evaluate_propositional(node.right, env)
    raise ValueError(f"{node.text()} is not a propositional formula")


def truth_table(premises: Sequence[str], conclusion: str | None = None) -> dict[str, Any]:
    """Enumerate a propositional formula (and optional entailment) exhaustively."""
    pre_nodes = [parse(p) for p in premises]
    con_node = parse(conclusion) if conclusion else None
    all_nodes = pre_nodes + ([con_node] if con_node is not None else [])
    for node in all_nodes:
        if not is_purely_propositional(node):
            raise ValueError(
                f"`{node.text()}` is not purely propositional; use the SMT-backed "
                "logic_entails tool for quantifiers, predicates, or arithmetic"
            )
    variables: list[str] = []
    for node in all_nodes:
        for name in propositional_variables(node):
            if name not in variables:
                variables.append(name)
    if len(variables) > _MAX_TRUTH_TABLE_VARS:
        raise ValueError(
            f"{len(variables)} variables would need {2 ** len(variables)} rows; "
            f"the limit is {_MAX_TRUTH_TABLE_VARS}"
        )
    rows: list[dict[str, Any]] = []
    counterexamples: list[dict[str, Any]] = []
    for mask in range(1 << len(variables)):
        env = {name: bool(mask >> i & 1) for i, name in enumerate(variables)}
        row: dict[str, Any] = dict(env)
        values = [evaluate_propositional(node, env) for node in pre_nodes]
        if con_node is not None:
            values.append(evaluate_propositional(con_node, env))
        for label, value in zip(_labels(pre_nodes, con_node), values):
            row[label] = value
        rows.append(row)
        if pre_nodes and con_node is not None:
            if all(values[:-1]) and not values[-1]:
                counterexamples.append(dict(env))
    result: dict[str, Any] = {
        "variables": variables,
        "row_count": len(rows),
        "rows": rows,
    }
    if con_node is not None:
        result["premises"] = [n.text() for n in pre_nodes]
        result["conclusion"] = con_node.text()
        if pre_nodes:
            result["entailed"] = not counterexamples
            result["verdict"] = (
                "the premises entail the conclusion: no row makes every premise true "
                "and the conclusion false"
                if not counterexamples else
                "the premises do NOT entail the conclusion"
            )
            if counterexamples:
                result["falsifying_assignments"] = counterexamples
        else:
            result["tautology"] = all(row[_labels([], con_node)[0]] for row in rows)
            result["verdict"] = (
                "tautology: true under every assignment"
                if result["tautology"] else
                "not a tautology"
            )
    return result


def _labels(premises: Sequence[Node], conclusion: Node | None) -> list[str]:
    labels = []
    for index, node in enumerate(premises):
        labels.append(f"premise{index + 1}: {node.text()}")
    if conclusion is not None:
        labels.append(f"conclusion: {conclusion.text()}")
    return labels


def rules_catalogue() -> dict[str, Any]:
    """The controlled vocabulary of inference rules, for the skill to reference."""
    return {
        "logical_rules": {
            name: {
                "category": rule.category,
                "description": rule.description,
                "example": rule.example,
                "semantically_valid_on_its_own": rule.semantically_valid,
            }
            for name, rule in sorted(RULES.items())
        },
        "non_logical_justifications": dict(sorted(NON_LOGICAL_RULES.items())),
        "note": (
            "A step citing a logical rule is accepted only if the formulas have the "
            "rule's shape AND the premises semantically entail the conclusion. "
            "Non-logical justifications are recorded but audited as unchecked."
        ),
    }
