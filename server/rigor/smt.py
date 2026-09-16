"""SMT-backed validity checking: prove by refutation, or exhibit a counterexample.

The contract is deliberately honest.  A statement is reported

* ``proven``   only when the negation is unsatisfiable,
* ``refuted``  only when a concrete model of the negation exists,
* ``unknown``  when z3 cannot decide -- never silently upgraded to a proof.

Two ideas make this practical for real mathematical claims:

**Skolemisation.**  A goal of the form ``forall x. phi`` is checked by freeing
``x`` and asserting ``not phi``: an unsatisfiable result still means the
quantified statement is valid, while a satisfiable result yields an actual
counterexample.  Solvers decide the free-variable form far more often.

**Residue refinement.**  When the direct query is inconclusive, a claim such as
``6 | n**3 - n`` is re-proved as ``m`` separate goals ``n = m*q + r`` -- which is
how the fact is proved on paper.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

import z3

from .ast_nodes import (
    And,
    BigOp,
    Bin,
    Cmp,
    Imp,
    Node,
    Not,
    Num,
    Quant,
    Sym,
    children_of,
    collect_symbols,
    parse,
    walk,
)
from .translate import (
    SORT_INT,
    SORT_NAT,
    SortTable,
    TranslateError,
    ast_to_z3,
    require_bool,
    z3_constants,
)

STATUS_PROVEN = "proven"
STATUS_REFUTED = "refuted"
STATUS_UNKNOWN = "unknown"
STATUS_ERROR = "error"

_MAX_SUBPROBLEMS = 512
_MAX_SPLIT_MODULUS = 64
_DEFAULT_BUDGET_MS = 60000


@dataclass
class SmtOutcome:
    status: str
    method: str = "direct refutation"
    model: dict[str, str] | None = None
    sorts: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    reason: str | None = None
    elapsed_ms: int = 0

    @property
    def decided(self) -> bool:
        return self.status in (STATUS_PROVEN, STATUS_REFUTED)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "status": self.status,
            "method": self.method,
            "sorts_used": self.sorts,
            "elapsed_ms": self.elapsed_ms,
        }
        if self.model:
            key = "counterexample" if self.status == STATUS_REFUTED else "model"
            out[key] = self.model
        if self.notes:
            out["notes"] = self.notes
        if self.reason:
            out["reason"] = self.reason
        return out


# --------------------------------------------------------------------------- #
# AST surgery
# --------------------------------------------------------------------------- #


def substitute(node: Node, mapping: Mapping[str, Node]) -> Node:
    """Replace free occurrences of the given names, respecting quantifier scope."""
    if isinstance(node, Sym):
        return mapping.get(node.name, node)
    if isinstance(node, Num):
        return node
    if isinstance(node, Quant):
        inner = {k: v for k, v in mapping.items() if k != node.var}
        return Quant(node.kind, node.var, node.sort, substitute(node.body, inner))
    if isinstance(node, BigOp):
        inner = {k: v for k, v in mapping.items() if k != node.var}
        return BigOp(node.kind, substitute(node.expr, inner), node.var,
                     substitute(node.lo, inner), substitute(node.hi, inner))
    children = children_of(node)
    if not children:
        return node
    return _rebuild(node, [substitute(c, mapping) for c in children])


def _rebuild(node: Node, children: list[Node]) -> Node:
    from .ast_nodes import Abs, App, Iff, Neg, Or, Sqrt, TupleN

    if isinstance(node, App):
        return App(node.name, tuple(children))
    if isinstance(node, TupleN):
        return TupleN(tuple(children))
    if isinstance(node, Neg):
        return Neg(children[0])
    if isinstance(node, Not):
        return Not(children[0])
    if isinstance(node, Sqrt):
        return Sqrt(children[0])
    if isinstance(node, Abs):
        return Abs(children[0])
    if isinstance(node, Bin):
        return Bin(node.op, children[0], children[1])
    if isinstance(node, Cmp):
        return Cmp(node.op, children[0], children[1])
    if isinstance(node, Imp):
        return Imp(children[0], children[1])
    if isinstance(node, Iff):
        return Iff(children[0], children[1])
    if isinstance(node, And):
        return And(tuple(children))
    if isinstance(node, Or):
        return Or(tuple(children))
    return node


def skolemise(node: Node, sorts: SortTable) -> tuple[Node, list[str], list[str]]:
    """Free the leading ``forall`` binders.

    Returns ``(body, freed_names, constraints_text)``.  A natural-number binder
    contributes an explicit ``var >= 0`` hypothesis so that dropping the
    quantifier does not silently drop the element constraint.
    """
    binders: list[Quant] = []
    current = node
    while isinstance(current, Quant) and current.kind == "forall":
        binders.append(current)
        current = current.body
    if not binders:
        return node, [], []
    constraints: list[Node] = []
    notes: list[str] = []
    for binder in binders:
        sort = sorts.quantified.get(binder.var, binder.sort)
        if sort == SORT_NAT:
            constraints.append(Cmp("ge", Sym(binder.var), Num("0")))
            notes.append(f"{binder.var} >= 0 carried over from the natural-number binder")
    if constraints:
        current = Imp(And(tuple(constraints)), current)
    return current, [b.var for b in binders], notes


def _transform(node: Node, fn: Any) -> Node:
    """Bottom-up rewrite: rebuild children first, then apply ``fn``."""
    children = children_of(node)
    rebuilt = _rebuild(node, [_transform(c, fn) for c in children]) if children else node
    return fn(rebuilt)


def _only_that_symbol(expr: Node, name: str) -> bool:
    return isinstance(expr, Sym) and expr.name == name


def _linear_in(expr: Node, name: str) -> tuple[int, Node] | None:
    """Decompose ``expr`` as ``coefficient * name + constant`` when possible."""
    if _only_that_symbol(expr, name):
        return (1, Num("0"))
    if isinstance(expr, Bin) and expr.op == "mul":
        if isinstance(expr.left, Num) and expr.left.value.lstrip("+-").isdigit() \
                and _only_that_symbol(expr.right, name):
            return (int(expr.left.value), Num("0"))
        if isinstance(expr.right, Num) and expr.right.value.lstrip("+-").isdigit() \
                and _only_that_symbol(expr.left, name):
            return (int(expr.right.value), Num("0"))
    if isinstance(expr, Bin) and expr.op == "add":
        for first, second in ((expr.left, expr.right), (expr.right, expr.left)):
            if isinstance(second, Num) and second.value.lstrip("+-").isdigit() \
                    and _only_that_symbol(first, name):
                return (1, Num(str(int(second.value))))
            linear = _linear_in(first, name)
            if linear is not None and isinstance(second, Num) \
                    and second.value.lstrip("+-").isdigit() \
                    and isinstance(linear[1], Num):
                total = int(linear[1].value) + int(second.value)
                return (linear[0], Num(str(total)))
    return None


def rewrite_existential_divisibility(
    node: Node, sorts: SortTable
) -> tuple[Node, list[str]]:
    """Rewrite ``exists k in Z: A = c*k + d`` into ``c | A - d``.

    Writing divisibility as an explicit multiple is standard mathematical
    practice, but the existential forces the solver to synthesise a witness
    polynomial.  The two forms are equivalent for integer ``k`` (``c`` a non-zero
    literal), and the divisibility form is what SMT can actually decide.
    """
    notes: list[str] = []

    def visit(current: Node) -> Node:
        if not (isinstance(current, Quant) and current.kind == "exists"):
            return current
        sort = sorts.quantified.get(current.var, current.sort)
        if sort not in (SORT_INT, SORT_NAT):
            return current
        body = current.body
        if not (isinstance(body, Cmp) and body.op == "eq"):
            return current
        for target, source in ((body.right, body.left), (body.left, body.right)):
            linear = _linear_in(target, current.var)
            if linear is None:
                return current
            coefficient, offset = linear
            if coefficient == 0:
                return current
            if any(isinstance(n, Sym) and n.name == current.var
                   for n in walk(source)):
                return current
            notes.append(
                f"rewrote `exists {current.var}: {source.text()} = {target.text()}` "
                f"as the divisibility statement `{coefficient} | ({source.text()}) "
                f"- ({offset.text()})`"
            )
            return Cmp("divides", Num(str(coefficient)),
                       Bin("sub", source, offset))
        return current

    return _transform(node, visit), notes


def integer_moduli(node: Node, limit: int = _MAX_SPLIT_MODULUS) -> list[int]:
    """Moduli appearing in the statement: the natural case-split sizes."""
    from .ast_nodes import App

    mods: set[int] = set()

    def consider(value: Node) -> None:
        if isinstance(value, Num) and value.value.isdigit():
            m = int(value.value)
            if 1 < m <= limit:
                mods.add(m)

    for n in walk(node):
        if isinstance(n, Bin) and n.op == "mod":
            consider(n.right)
        elif isinstance(n, Cmp) and n.op in ("divides", "not_divides"):
            consider(n.left)
        elif isinstance(n, App) and n.name in ("divisible", "divides") and n.args:
            consider(n.args[0])
        elif isinstance(n, Bin) and n.op == "mul":
            # `n**3 - n = 6*k` states divisibility by 6 without ever writing
            # `mod`, so a literal factor is a usable case-split modulus.
            consider(n.left)
            consider(n.right)
    return sorted(mods)


def residue_groups(
    goal: Node,
    sorts: SortTable,
    free_integer_vars: Sequence[str],
    moduli: Sequence[int] | None = None,
    cap: int = _MAX_SUBPROBLEMS,
) -> list[tuple[str, list[Node]]]:
    """Case splits that jointly imply ``goal``.

    Every integer is congruent to exactly one residue mod ``m``, so proving all
    ``m`` subgoals of a group establishes the goal.
    """
    moduli = list(moduli if moduli is not None else integer_moduli(goal))
    targets = [v for v in free_integer_vars
               if sorts.sort_of(v) in (SORT_INT, SORT_NAT)]
    if not targets or not moduli:
        return []
    groups: list[tuple[str, list[Node]]] = []

    def split_once(current: list[Node], var: str, modulus: int) -> list[Node]:
        out: list[Node] = []
        for residue in range(modulus):
            fresh = Sym(f"{var}__q{modulus}_{residue}")
            replacement = Bin("add", Bin("mul", Num(str(modulus)), fresh), Num(str(residue)))
            for node in current:
                out.append(substitute(node, {var: replacement}))
        return out

    for modulus in moduli:
        if modulus <= cap:
            variants = [goal]
            for var in targets[:1]:
                variants = split_once(variants, var, modulus)
            groups.append((f"{targets[0]} mod {modulus}", variants))
        if len(targets) >= 2 and modulus * modulus <= cap:
            variants = [goal]
            for var in targets[:2]:
                variants = split_once(variants, var, modulus)
            groups.append((f"{' and '.join(targets[:2])} mod {modulus}", variants))
    return [g for g in groups if len(g[1]) <= cap]


# --------------------------------------------------------------------------- #
# Model rendering
# --------------------------------------------------------------------------- #


def _format_value(value: Any) -> str:
    if z3.is_rational_value(value):
        num = value.numerator_as_long()
        den = value.denominator_as_long()
        return str(num) if den == 1 else f"{num}/{den}"
    if z3.is_algebraic_value(value):
        return str(value.approx(12)).rstrip("?")
    return str(value)


def read_model(model: z3.ModelRef, constants: Mapping[str, Any],
               functions: Mapping[tuple[str, int], Any] | None = None) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, const in constants.items():
        try:
            out[name] = _format_value(model.eval(const, model_completion=True))
        except z3.Z3Exception:  # pragma: no cover - defensive
            continue
    if functions:
        for (fname, arity), func in functions.items():
            try:
                out[f"{fname}/{arity}"] = str(model.eval(func, model_completion=True))
            except z3.Z3Exception:  # pragma: no cover - defensive
                continue
    return out


# --------------------------------------------------------------------------- #
# Core refutation engine
# --------------------------------------------------------------------------- #


@dataclass
class _Attempt:
    status: str
    model: dict[str, str] | None
    notes: list[str]
    reason: str | None


def _refute(
    hypotheses: Sequence[Node],
    goal: Node,
    sorts: SortTable,
    timeout_ms: int,
) -> _Attempt:
    """Assert the hypotheses and the negated goal; unsat means the goal holds."""
    combined: Node = And(tuple(list(hypotheses) + [Not(goal)]))
    ctx, constants = z3_constants(combined, sorts)
    expr = require_bool(ast_to_z3(combined, ctx, True), combined)
    solver = z3.Solver()
    solver.set("timeout", timeout_ms)
    solver.add(expr)
    result = solver.check()
    notes = list(ctx.notes)
    if result == z3.unsat:
        return _Attempt(STATUS_PROVEN, None, notes, None)
    if result == z3.sat:
        return _Attempt(STATUS_REFUTED,
                        read_model(solver.model(), constants, ctx.functions), notes, None)
    return _Attempt(STATUS_UNKNOWN, None, notes, solver.reason_unknown())


def _budgeted(timeout_ms: int, deadline: float, remaining: int) -> int:
    left_ms = int((deadline - time.time()) * 1000)
    if left_ms <= 0:
        return 0
    share = max(500, left_ms // max(1, remaining))
    return int(min(timeout_ms, share))


def check_entailment(
    hypotheses: Sequence[str | Node],
    goal: str | Node,
    *,
    declared_sorts: Mapping[str, str] | None = None,
    functions: Iterable[str] = (),
    timeout_ms: int = 15000,
    budget_ms: int = _DEFAULT_BUDGET_MS,
    allow_residue: bool = True,
) -> SmtOutcome:
    """Decide whether ``hypotheses`` entail ``goal`` semantically."""
    started = time.time()
    deadline = started + budget_ms / 1000.0
    goal_node: Node
    try:
        goal_node = goal if isinstance(goal, Node) else parse(goal, functions)
    except Exception as exc:  # noqa: BLE001
        return SmtOutcome(STATUS_ERROR, method="the goal could not be parsed",
                          reason=f"{type(exc).__name__}: {exc}",
                          elapsed_ms=int((time.time() - started) * 1000))
    pre: list[Node] = []
    for hypothesis in hypotheses:
        if isinstance(hypothesis, Node):
            pre.append(hypothesis)
            continue
        try:
            pre.append(parse(hypothesis, functions))
        except Exception as exc:  # noqa: BLE001
            return SmtOutcome(
                STATUS_ERROR,
                method="a hypothesis could not be parsed as a formula",
                reason=(
                    f"`{hypothesis}` is not a formula ({type(exc).__name__}: {exc}). "
                    "Domain declarations such as `n in Z` belong in the `variables` "
                    "argument, not in the hypotheses."
                ),
                elapsed_ms=int((time.time() - started) * 1000),
            )

    whole: Node = And(tuple(pre + [goal_node])) if pre else goal_node
    sorts = SortTable(whole, declared=dict(declared_sorts or {}))
    notes: list[str] = []

    rewritten_pre: list[Node] = []
    for premise in pre:
        new_premise, pre_notes = rewrite_existential_divisibility(premise, sorts)
        rewritten_pre.append(new_premise)
        notes.extend(pre_notes)
    pre = rewritten_pre

    body, freed, skolem_notes = skolemise(goal_node, sorts)
    body, rewrite_notes = rewrite_existential_divisibility(body, sorts)
    notes.extend(skolem_notes)
    notes.extend(rewrite_notes)

    attempts: list[tuple[str, Node]] = [("refuted the negated goal with z3", body)]
    if freed or body is not goal_node:
        attempts.append(("refuted the negated goal with quantifiers kept", goal_node))

    last_reason: str | None = None
    for index, (method, target) in enumerate(attempts):
        remaining = max(1, len(attempts) - index)
        attempt = _refute(pre, target, sorts, _budgeted(timeout_ms, deadline, remaining))
        notes.extend(attempt.notes)
        if attempt.status == STATUS_PROVEN:
            return SmtOutcome(STATUS_PROVEN, method=method, sorts=sorts.report(),
                              notes=notes, elapsed_ms=int((time.time() - started) * 1000))
        if attempt.status == STATUS_REFUTED:
            model = attempt.model
            if not model:
                notes.append(
                    "the goal is false but no assignment describes why; "
                    "this happens for a false existential, whose search space is exhausted"
                )
            return SmtOutcome(STATUS_REFUTED,
                              method="z3 produced an assignment falsifying the goal",
                              model=model, sorts=sorts.report(), notes=notes,
                              elapsed_ms=int((time.time() - started) * 1000))
        last_reason = attempt.reason

    if allow_residue:
        targets = freed or sorted(collect_symbols(whole))
        groups = residue_groups(body, sorts, targets)
        for description, variants in groups:
            ok = True
            detail = ""
            for position, variant in enumerate(variants):
                share = _budgeted(timeout_ms, deadline,
                                  len(variants) - position + 1)
                if share <= 0:
                    ok = False
                    detail = "time budget exhausted"
                    break
                attempt = _refute(pre, variant, sorts, share)
                if attempt.status != STATUS_PROVEN:
                    ok = False
                    detail = f"residue case #{position} came back {attempt.status}"
                    if attempt.reason:
                        detail += f" ({attempt.reason})"
                    break
            if ok:
                return SmtOutcome(
                    STATUS_PROVEN,
                    method=f"residue case split on {description} "
                           f"({len(variants)} cases, each refuted)",
                    sorts=sorts.report(),
                    notes=notes + [
                        "each residue class was proved separately because the "
                        "direct SMT query was inconclusive"
                    ],
                    elapsed_ms=int((time.time() - started) * 1000),
                )
            notes.append(f"residue split on {description} did not close: {detail}")

    return SmtOutcome(STATUS_UNKNOWN, method="z3 could not decide", sorts=sorts.report(),
                      notes=notes,
                      reason=last_reason or "solver returned unknown",
                      elapsed_ms=int((time.time() - started) * 1000))


def collect_free_names(node: Node) -> set[str]:
    return collect_symbols(node)


def check_valid(
    statement: str | Node,
    *,
    declared_sorts: Mapping[str, str] | None = None,
    functions: Iterable[str] = (),
    timeout_ms: int = 15000,
    budget_ms: int = _DEFAULT_BUDGET_MS,
    allow_residue: bool = True,
) -> SmtOutcome:
    """Decide whether ``statement`` is valid over its declared domains."""
    return check_entailment([], statement, declared_sorts=declared_sorts,
                            functions=functions, timeout_ms=timeout_ms,
                            budget_ms=budget_ms, allow_residue=allow_residue)


def check_satisfiable(
    statement: str | Node,
    *,
    declared_sorts: Mapping[str, str] | None = None,
    functions: Iterable[str] = (),
    timeout_ms: int = 15000,
) -> SmtOutcome:
    """Decide whether ``statement`` has a model, returning one when it does."""
    started = time.time()
    try:
        node = statement if isinstance(statement, Node) else parse(statement, functions)
    except Exception as exc:  # noqa: BLE001
        return SmtOutcome(STATUS_ERROR, reason=f"{type(exc).__name__}: {exc}",
                          elapsed_ms=int((time.time() - started) * 1000))
    sorts = SortTable(node, declared=dict(declared_sorts or {}))
    try:
        ctx, constants = z3_constants(node, sorts)
        expr = require_bool(ast_to_z3(node, ctx, True), node)
        solver = z3.Solver()
        solver.set("timeout", timeout_ms)
        solver.add(expr)
        result = solver.check()
    except (TranslateError, z3.Z3Exception) as exc:
        return SmtOutcome(STATUS_ERROR, method="encoding failed", reason=str(exc),
                          sorts=sorts.report(), elapsed_ms=int((time.time() - started) * 1000))
    if result == z3.sat:
        return SmtOutcome("satisfiable", method="z3 found a model",
                          model=read_model(solver.model(), constants, ctx.functions),
                          sorts=sorts.report(), notes=list(ctx.notes),
                          elapsed_ms=int((time.time() - started) * 1000))
    if result == z3.unsat:
        return SmtOutcome("unsatisfiable", method="z3 proved unsatisfiability",
                          sorts=sorts.report(), notes=list(ctx.notes),
                          elapsed_ms=int((time.time() - started) * 1000))
    return SmtOutcome(STATUS_UNKNOWN, method="z3 returned unknown",
                      sorts=sorts.report(), notes=list(ctx.notes),
                      reason=solver.reason_unknown(),
                      elapsed_ms=int((time.time() - started) * 1000))


def entails(
    premises: Sequence[str | Node],
    conclusion: str | Node,
    **kwargs: Any,
) -> SmtOutcome:
    """Alias of :func:`check_entailment` kept for readability at call sites."""
    return check_entailment(premises, conclusion, **kwargs)
