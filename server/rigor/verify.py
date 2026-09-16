"""High-level mathematical verifiers.

Each function answers one question that appears constantly in real proofs and
returns a verdict that distinguishes *proved* from *refuted* from *undecided*.
Nothing here ever upgrades an undecided result to a proof.
"""

from __future__ import annotations

import itertools
import random
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

import mpmath
import sympy as sp

from .ast_nodes import (
    Abs,
    And,
    App,
    BigOp,
    Bin,
    Cmp,
    Iff,
    Imp,
    Neg,
    Node,
    Not,
    Num,
    Or,
    Quant,
    Sqrt,
    Sym,
    children_of,
    collect_symbols,
    parse,
    walk,
)
from .smt import (
    STATUS_ERROR,
    STATUS_PROVEN,
    STATUS_REFUTED,
    STATUS_UNKNOWN,
    SmtOutcome,
    check_entailment,
    check_satisfiable,
    check_valid,
    skolemise,
    substitute,
)
from .symbolic import (
    VERDICT_ERROR,
    VERDICT_INCONCLUSIVE,
    VERDICT_PROVEN,
    VERDICT_REFUTED,
    AssumptionSet,
    render,
    split_assumptions,
)
from .translate import (
    SORT_INT,
    SORT_NAT,
    SORT_REAL,
    SortTable,
    TranslateError,
    ast_to_sympy,
    SympyContext,
    sympy_to_ast,
)

_OP_TO_RELATION = {
    "<": "lt", "<=": "le", ">": "gt", ">=": "ge", "!=": "ne", "=": "eq",
    "==": "eq",
}


# --------------------------------------------------------------------------- #
# Numeric evaluation of an AST at a concrete assignment
# --------------------------------------------------------------------------- #

_SAFE_FUNCS: dict[str, Any] = {
    "sqrt": mpmath.sqrt, "abs": abs, "exp": mpmath.exp, "ln": mpmath.log,
    "log": mpmath.log, "sin": mpmath.sin, "cos": mpmath.cos, "tan": mpmath.tan,
    "cot": lambda x: 1 / mpmath.tan(x), "sec": lambda x: 1 / mpmath.cos(x),
    "csc": lambda x: 1 / mpmath.sin(x), "asin": mpmath.asin, "acos": mpmath.acos,
    "atan": mpmath.atan, "sinh": mpmath.sinh, "cosh": mpmath.cosh,
    "tanh": mpmath.tanh, "floor": mpmath.floor, "ceil": mpmath.ceil,
    "sign": lambda x: (x > 0) - (x < 0), "min": min, "max": max,
}


class NumericError(ValueError):
    pass


def evaluate_numeric(node: Node, env: Mapping[str, Any]) -> Any:
    """Evaluate an AST at a concrete assignment, returning a number or a bool."""
    if isinstance(node, Num):
        return node.number()
    if isinstance(node, Sym):
        if node.name in ("pi",):
            return mpmath.pi
        if node.name in ("e", "E"):
            return mpmath.e
        if node.name in env:
            return env[node.name]
        raise NumericError(f"no value assigned to {node.name}")
    if isinstance(node, Neg):
        return -evaluate_numeric(node.operand, env)
    if isinstance(node, Sqrt):
        value = evaluate_numeric(node.operand, env)
        if value < 0:
            raise NumericError("square root of a negative sample")
        return mpmath.sqrt(value)
    if isinstance(node, Abs):
        return abs(evaluate_numeric(node.operand, env))
    if isinstance(node, Bin):
        left = evaluate_numeric(node.left, env)
        right = evaluate_numeric(node.right, env)
        if node.op == "add":
            return left + right
        if node.op == "sub":
            return left - right
        if node.op == "mul":
            return left * right
        if node.op == "div":
            if right == 0:
                raise NumericError("division by zero at this sample")
            return left / right
        if node.op == "mod":
            if right == 0:
                raise NumericError("modulo by zero at this sample")
            return left % right
        if node.op == "pow":
            return left ** right
        raise NumericError(f"unsupported operator {node.op}")
    if isinstance(node, App):
        name = node.name
        args = [evaluate_numeric(a, env) for a in node.args]
        if name == "even":
            return args[0] % 2 == 0
        if name == "odd":
            return args[0] % 2 != 0
        if name in ("divisible", "divides"):
            first, second = (args[0], args[1]) if name == "divisible" else (args[1], args[0])
            if first == 0:
                raise NumericError("division by zero in a divisibility test")
            return second % first == 0
        if name == "mod":
            return args[0] % args[1]
        if name in ("gcd",):
            return mpmath.gcd(int(args[0]), int(args[1]))
        if name in ("lcm",):
            return mpmath.lcm(int(args[0]), int(args[1]))
        if name in ("factorial",):
            return mpmath.factorial(int(args[0]))
        if name in ("binomial",):
            return mpmath.binomial(int(args[0]), int(args[1]))
        if name in _SAFE_FUNCS:
            try:
                return _SAFE_FUNCS[name](*args)
            except (ValueError, ZeroDivisionError, TypeError) as exc:
                raise NumericError(f"{name} is undefined at this sample: {exc}") from exc
        raise NumericError(f"cannot evaluate the function {name} numerically")
    if isinstance(node, Cmp):
        left = evaluate_numeric(node.left, env)
        right = evaluate_numeric(node.right, env)
        if node.op == "eq":
            return left == right
        if node.op == "ne":
            return left != right
        if node.op == "lt":
            return left < right
        if node.op == "le":
            return left <= right
        if node.op == "gt":
            return left > right
        if node.op == "ge":
            return left >= right
        if node.op in ("divides", "not_divides"):
            if left == 0:
                raise NumericError("division by zero in a divisibility test")
            result = right % left == 0
            return result if node.op == "divides" else not result
        raise NumericError(f"unsupported relation {node.op}")
    if isinstance(node, Not):
        return not evaluate_numeric(node.operand, env)
    if isinstance(node, And):
        return all(evaluate_numeric(p, env) for p in node.parts)
    if isinstance(node, Or):
        return any(evaluate_numeric(p, env) for p in node.parts)
    if isinstance(node, Imp):
        return (not evaluate_numeric(node.left, env)) or evaluate_numeric(node.right, env)
    if isinstance(node, Iff):
        return evaluate_numeric(node.left, env) == evaluate_numeric(node.right, env)
    raise NumericError(
        f"cannot sample the node {node.text()!r}; quantified statements and symbolic "
        "sums must be handled by the SMT or symbolic tools"
    )


# --------------------------------------------------------------------------- #
# Sample generation
# --------------------------------------------------------------------------- #


def _sample_pool(sort: str, bound: int, count: int, rng: random.Random) -> list[Any]:
    if sort in (SORT_INT, SORT_NAT):
        low = 0 if sort == SORT_NAT else -bound
        values = list(range(low, bound + 1))
        edge = [0, 1, -1, 2, -2, bound, -bound, 3, -3]
        pool = [v for v in edge if low <= v <= bound] + values
        return pool[:max(count, 20)]
    values = [0.0, 1.0, -1.0, 0.5, -0.5, 2.0, -2.0, 3.0, -3.0, 0.25, -0.25,
              1.5, -1.5, 10.0, -10.0]
    pool = values[:max(count, 15)]
    while len(pool) < count:
        pool.append(rng.uniform(-bound, bound) if bound else rng.uniform(-5, 5))
    return pool


@dataclass
class _Sampling:
    variables: dict[str, str]
    bound: int
    samples: int
    seed: int


def sample_counterexample(
    goal: Node,
    sorts: SortTable,
    variables: Mapping[str, str],
    *,
    bound: int = 12,
    samples: int = 200,
    seed: int = 20240917,
    precision: int = 30,
) -> tuple[dict[str, Any] | None, int, str | None]:
    """Randomised search for an assignment falsifying ``goal``.

    Returns ``(counterexample, tried, note)``.  A returned counterexample is a
    proof of falsity; finding none is only evidence, never a proof.
    """
    free = sorted(collect_symbols(goal))
    if not free:
        return None, 0, "the statement has no free variables to sample"
    rng = random.Random(seed)
    pools = {
        name: _sample_pool(variables.get(name, sorts.sort_of(name)), bound, samples, rng)
        for name in free
    }
    tried = 0
    failures = 0
    for _ in range(samples):
        env = {name: rng.choice(pool) for name, pool in pools.items()}
        try:
            with mpmath.workdps(precision):
                holds = evaluate_numeric(goal, env)
        except (NumericError, ZeroDivisionError, OverflowError, ValueError):
            failures += 1
            continue
        tried += 1
        if holds is False:
            return ({k: _format_sample(v) for k, v in env.items()}, tried, None)
    note = None
    if failures and tried == 0:
        note = "every sample was inadmissible (singularities or undefined operations)"
    elif failures:
        note = f"{failures} of {samples} samples were inadmissible and skipped"
    return None, tried, note


def _format_sample(value: Any) -> str:
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return f"{value:.10g}"
    return str(value)


def _exhaustive_counterexample(
    goal: Node,
    variables: Mapping[str, str],
    ranges: Mapping[str, Sequence[Any]],
) -> tuple[dict[str, Any] | None, int]:
    """Exhaustive search over explicit ranges."""
    names = [n for n in variables if n in ranges]
    if not names:
        return None, 0
    existing = sorted(collect_symbols(goal))
    if set(names) != set(existing):
        return None, 0
    tried = 0
    for combination in itertools.product(*(ranges[n] for n in names)):
        env = dict(zip(names, combination))
        try:
            holds = evaluate_numeric(goal, env)
        except (NumericError, ZeroDivisionError, OverflowError, ValueError):
            continue
        tried += 1
        if holds is False:
            return {k: _format_sample(v) for k, v in env.items()}, tried
    return None, tried


# --------------------------------------------------------------------------- #
# verify_forall
# --------------------------------------------------------------------------- #


def verify_forall(
    statement: str,
    *,
    variables: Mapping[str, str] | None = None,
    functions: Sequence[str] = (),
    timeout_ms: int = 20000,
    sample_when_undecided: bool = True,
    sample_bound: int = 12,
    samples: int = 200,
) -> dict[str, Any]:
    """Prove or refute a universally quantified statement over stated domains.

    Falls back to sampling when the solver is undecided: a sample that violates
    the statement refutes it immediately, and the report says explicitly that
    failure to find one is not a proof.
    """
    result: dict[str, Any] = {"statement": statement}
    outcome = check_valid(statement, declared_sorts=variables, functions=functions,
                          timeout_ms=timeout_ms)
    result.update(outcome.to_dict())
    result["verdict"] = {
        STATUS_PROVEN: VERDICT_PROVEN,
        STATUS_REFUTED: VERDICT_REFUTED,
        STATUS_UNKNOWN: VERDICT_INCONCLUSIVE,
        STATUS_ERROR: VERDICT_ERROR,
    }[outcome.status]

    if outcome.status == STATUS_REFUTED:
        return result
    if outcome.status == STATUS_PROVEN:
        return result
    if outcome.status == STATUS_ERROR:
        return result

    if sample_when_undecided:
        try:
            node = parse(statement, functions)
            sorts = SortTable(node, declared=dict(variables or {}))
            body, freed, skolem_notes = skolemise(node, sorts)
            sampling: dict[str, Any] = {}
            if skolem_notes:
                sampling["domain_constraints"] = skolem_notes
            if not freed:
                sampling["outcome"] = (
                    "the statement does not begin with a universal quantifier, so "
                    "sampling free variables would not test it"
                )
                result["sampling"] = sampling
                return result
            witness, tried, note = sample_counterexample(
                body, sorts, dict(variables or {}), bound=sample_bound, samples=samples
            )
            sampling["assignments_tried"] = tried
            sampling["sampled_variables"] = freed
            if note:
                sampling["note"] = note
            if witness:
                result["verdict"] = VERDICT_REFUTED
                result["counterexample"] = witness
                sampling["outcome"] = "a sampled assignment violates the statement"
            else:
                sampling["outcome"] = (
                    "no sampled assignment violates the statement; this is evidence, "
                    "not a proof"
                )
            result["sampling"] = sampling
        except Exception as exc:  # noqa: BLE001
            result["sampling"] = {"error": f"{type(exc).__name__}: {exc}"}
    return result


# --------------------------------------------------------------------------- #
# verify_inequality
# --------------------------------------------------------------------------- #


def verify_inequality(
    lhs: str,
    relation: str,
    rhs: str,
    *,
    variables: Mapping[str, str] | None = None,
    constraints: Sequence[str] = (),
    functions: Sequence[str] = (),
    timeout_ms: int = 20000,
    sample_when_undecided: bool = True,
) -> dict[str, Any]:
    """Decide ``lhs <op> rhs`` for every value of the declared variables."""
    op = relation.strip()
    if op not in _OP_TO_RELATION:
        raise ValueError(
            f"unknown relation {relation!r}; use one of {', '.join(sorted(_OP_TO_RELATION))}"
        )
    declared = dict(variables or {})
    inferred: list[str] = []
    if not declared:
        # Fall back to reals, but say so: the domain is part of the claim.
        try:
            for side in (lhs, rhs):
                for name in sorted(collect_symbols(parse(side, functions))):
                    if name not in declared:
                        declared[name] = SORT_REAL
                        inferred.append(name)
        except Exception:  # noqa: BLE001 - parsing errors surface in verify_forall
            declared = {"x": SORT_REAL}
    variable_text = _format_variables(declared)
    constraint_text = " & ".join(f"({c})" for c in constraints)
    body = f"{lhs} {op} {rhs}"
    statement = f"forall {variable_text}: ({constraint_text}) -> ({body})" if constraint_text \
        else f"forall {variable_text}: {body}"
    result = verify_forall(statement, variables=declared, functions=functions,
                           timeout_ms=timeout_ms,
                           sample_when_undecided=sample_when_undecided)
    result["claim"] = body
    result["domains"] = declared
    result["constraints"] = list(constraints)
    result["generated_statement"] = statement
    notes = result.setdefault("notes", [])
    if isinstance(notes, list):
        if inferred:
            notes.append(
                "no domains were given, so these variables were assumed real: "
                + ", ".join(inferred)
            )
        if not constraints:
            notes.append(
                "no constraints were given, so the claim was tested over the whole "
                "declared domain"
            )
    return result


def _format_variables(variables: Mapping[str, str]) -> str:
    if not variables:
        return "x in R"
    rendered = []
    for name, sort in variables.items():
        setname = {"int": "Z", "nat": "N", "real": "R", "rat": "Q"}.get(sort, sort)
        rendered.append(f"{name} in {setname}")
    return ", ".join(rendered)


# --------------------------------------------------------------------------- #
# verify_induction
# --------------------------------------------------------------------------- #


def _close_forms(node: Node) -> tuple[Node, list[str]]:
    """Rewrite symbolic Sum/Product into closed form where sympy can."""
    if not any(isinstance(n, BigOp) for n in walk(node)):
        return node, []
    notes: list[str] = []
    try:
        ctx = SympyContext()
        expr = ast_to_sympy(node, ctx)
        evaluated = sp.simplify(expr.doit())
        reborn = sympy_to_ast(evaluated)
    except Exception as exc:  # noqa: BLE001
        return node, [f"could not close the symbolic sum: {type(exc).__name__}: {exc}"]
    if isinstance(reborn, Sym) and reborn.name == "verum":
        notes.append(
            "closing the symbolic sum made both sides identical, so the statement is "
            "an algebraic identity rather than an induction obligation"
        )
        return reborn, notes
    if isinstance(reborn, Sym) and reborn.name == "falsum":
        notes.append("closing the symbolic sum made the statement identically false")
        return reborn, notes
    if any(isinstance(n, BigOp) for n in walk(reborn)):
        notes.append(
            "a symbolic Sum/Product could not be closed; the inductive step needs the "
            "closed form to be stated explicitly"
        )
        return node, notes
    if reborn.text() != node.text():
        notes.append(f"replaced the symbolic sum with its closed form: {reborn.text()}")
    return reborn, notes


def verify_induction(
    predicate: str,
    variable: str = "n",
    start: str = "0",
    *,
    sort: str = "int",
    functions: Sequence[str] = (),
    timeout_ms: int = 20000,
) -> dict[str, Any]:
    """Check a proof by induction: the base case and ``P(k) -> P(k+1)``.

    The inductive step is verified as the universally quantified implication
    ``k >= start & P(k) -> P(k+1)``, which is exactly the obligation a written
    proof must discharge -- so a failure comes back with a concrete ``k``.
    """
    name = variable.strip() or "n"
    try:
        predicate_node = parse(predicate, functions)
        start_node = parse(start, functions) if start else Num("0")
    except Exception as exc:  # noqa: BLE001
        return {"verdict": VERDICT_ERROR, "reason": f"{type(exc).__name__}: {exc}"}

    if name not in collect_symbols(predicate_node):
        return {
            "verdict": VERDICT_ERROR,
            "reason": f"the induction variable {name!r} does not occur in the predicate",
            "predicate": predicate_node.text(),
        }

    closed, notes = _close_forms(predicate_node)
    if isinstance(closed, Sym) and closed.name == "verum":
        return {
            "predicate": predicate_node.text(),
            "variable": name,
            "start": start_node.text(),
            "verdict": VERDICT_PROVEN,
            "method": "the closed form makes both sides identical",
            "notes": notes,
            "conclusion": (
                "sympy closed the symbolic sum and the two sides became the same "
                "expression, so the identity holds outright and induction is not needed"
            ),
        }
    if isinstance(closed, Sym) and closed.name == "falsum":
        return {
            "predicate": predicate_node.text(),
            "verdict": VERDICT_REFUTED,
            "method": "the closed form makes the statement identically false",
            "notes": notes,
        }
    binder_sort = SORT_INT if sort in ("int", "nat", "integer") else SORT_REAL
    result: dict[str, Any] = {
        "predicate": closed.text(),
        "variable": name,
        "start": start_node.text(),
        "domain": "Z" if binder_sort == SORT_INT else "R",
        "notes": notes,
    }
    if binder_sort != SORT_INT:
        return {
            **result,
            "verdict": VERDICT_ERROR,
            "reason": "induction is only supported over the integers",
        }

    base = _eval_predicate_at(closed, name, start_node, functions)
    result["base_case"] = base
    if base["status"] == STATUS_REFUTED:
        return {
            **result,
            "verdict": VERDICT_REFUTED,
            "reason": "the base case is false, so the induction does not start",
        }
    if base["status"] not in (STATUS_PROVEN,):
        return {
            **result,
            "verdict": VERDICT_INCONCLUSIVE,
            "reason": "the base case could not be decided",
        }

    successor = Bin("add", Sym(name), Num("1"))
    step_predicate = substitute(closed, {name: successor})
    hypothesis = And((Cmp("ge", Sym(name), start_node), closed))
    step = Quant("forall", name, binder_sort, Imp(hypothesis, step_predicate))
    step_outcome = check_valid(step, functions=functions, timeout_ms=timeout_ms)
    result["inductive_step"] = {
        "obligation": step.text(),
        "status": step_outcome.status,
        "method": step_outcome.method,
        "reason": step_outcome.reason,
        "counterexample": step_outcome.model,
        "elapsed_ms": step_outcome.elapsed_ms,
    }
    if step_outcome.status == STATUS_PROVEN:
        return {
            **result,
            "verdict": VERDICT_PROVEN,
            "conclusion": (
                f"the statement holds for every integer {name} >= {start_node.text()} "
                "by induction"
            ),
        }
    if step_outcome.status == STATUS_REFUTED:
        return {
            **result,
            "verdict": VERDICT_REFUTED,
            "reason": (
                f"the inductive step fails: P({name}) holds while P({name}+1) fails at the "
                f"reported {name}. The claim needs a stronger induction hypothesis."
            ),
        }
    return {
        **result,
        "verdict": VERDICT_INCONCLUSIVE,
        "reason": (
            "the inductive step could not be decided; a stronger hypothesis or an "
            "explicit closed form is needed"
        ),
    }


def _eval_predicate_at(predicate: Node, name: str, point: Node,
                       functions: Sequence[str]) -> dict[str, Any]:
    instance = substitute(predicate, {name: point})
    outcome = check_valid(instance, functions=functions, timeout_ms=15000)
    return {
        "instance": instance.text(),
        "status": outcome.status,
        "reason": outcome.reason,
        "counterexample": outcome.model,
    }


# --------------------------------------------------------------------------- #
# verify_limit
# --------------------------------------------------------------------------- #


def verify_limit(
    expression: str,
    variable: str,
    point: str,
    target: str,
    *,
    direction: str = "+",
    assumptions: Sequence[str] = (),
    precision: int = 30,
) -> dict[str, Any]:
    """Check that a limit equals a claimed value, symbolically and numerically."""
    from .symbolic import to_sympy

    assumption_set = split_assumptions(assumptions)
    expr = to_sympy(expression, assumption_set, [variable])
    var = sp.Symbol(variable)
    at = to_sympy(point, assumption_set)
    wanted = to_sympy(target, assumption_set)
    computed = sp.limit(expr, var, at, dir=direction)
    result: dict[str, Any] = {
        "expression": sp.sstr(expr),
        "variable": variable,
        "point": sp.sstr(at),
        "direction": direction,
        "claimed_limit": sp.sstr(wanted),
        "computed_limit": sp.sstr(computed),
    }
    # Infinite limits compare equal to themselves but their difference is nan, so
    # equality has to be checked before any subtraction.
    if computed == wanted:
        result.update({
            "verdict": VERDICT_PROVEN,
            "method": "sympy computed exactly the claimed value",
        })
        return result

    difference = sp.simplify(computed - wanted)
    if difference.is_finite is False:
        result.update({
            "verdict": VERDICT_REFUTED,
            "method": "the computed limit is not the claimed value",
            "detail": (
                f"sympy computed {sp.sstr(computed)} and the claimed value is "
                f"{sp.sstr(wanted)}; one of them is infinite, so they are different"
            ),
        })
        return result
    collapsed, residual, how = _collapse(difference)
    if collapsed:
        result.update({
            "verdict": VERDICT_PROVEN,
            "method": f"sympy computed the limit and the difference vanishes by {how}",
        })
        return result
    with mpmath.workdps(precision):
        try:
            numeric = sp.N(computed, precision)
            numeric_wanted = sp.N(wanted, precision)
            close = abs(mpmath.mpf(str(numeric)) - mpmath.mpf(str(numeric_wanted))) \
                < mpmath.mpf(10) ** (-(precision - 10))
        except Exception:  # noqa: BLE001
            close = False
    if not close:
        result.update({
            "verdict": VERDICT_REFUTED,
            "residual_difference": sp.sstr(residual),
            "method": "the computed limit differs from the claimed value",
        })
        return result
    result.update({
        "verdict": VERDICT_INCONCLUSIVE,
        "residual_difference": sp.sstr(residual),
        "method": "the two values agree numerically but sympy did not reduce the difference",
    })
    return result


def _collapse(expr: Any) -> tuple[bool, Any, str]:
    from .symbolic import try_collapse

    return try_collapse(expr)


# --------------------------------------------------------------------------- #
# find_counterexample
# --------------------------------------------------------------------------- #


def find_counterexample(
    claim: str,
    *,
    variables: Mapping[str, str] | None = None,
    ranges: Mapping[str, Sequence[Any]] | None = None,
    bound: int = 12,
    samples: int = 400,
    functions: Sequence[str] = (),
    timeout_ms: int = 20000,
) -> dict[str, Any]:
    """Search for an assignment that makes ``claim`` false.

    Strategy, strongest first: ask the SMT solver (an unsatisfiable negation is a
    *proof* that no counterexample exists, and a model is a genuine
    counterexample), then exhaustively scan explicit ranges, then sample.
    """
    names = dict(variables or {})
    result: dict[str, Any] = {"claim": claim}
    try:
        node = parse(claim, functions)
    except Exception as exc:  # noqa: BLE001
        return {**result, "verdict": VERDICT_ERROR, "reason": f"{type(exc).__name__}: {exc}"}

    sorts = SortTable(node, declared=names)
    # Free the leading universal quantifiers first, otherwise the quantified
    # variables are bound and a model would report nothing.
    body, freed, skolem_notes = skolemise(node, sorts)
    if freed:
        result["domain_constraints"] = skolem_notes
        result["searched_variables"] = freed

    outcome = check_satisfiable(
        Not(body), declared_sorts=names, functions=functions, timeout_ms=timeout_ms
    )
    result["smt"] = outcome.to_dict()
    if outcome.status == "satisfiable":
        return {
            **result,
            "verdict": VERDICT_REFUTED,
            "counterexample": outcome.model,
            "method": "the SMT solver produced a model of the negation",
        }
    if outcome.status == "unsatisfiable":
        return {
            **result,
            "verdict": VERDICT_PROVEN,
            "method": (
                "the negation is unsatisfiable, so no counterexample exists and the "
                "claim holds over the declared domains"
            ),
        }

    search_target = body if freed else node
    if ranges:
        witness, tried = _exhaustive_counterexample(search_target, names, ranges)
        result["exhaustive_search"] = {"assignments_tried": tried,
                                       "ranges": {k: list(v) for k, v in ranges.items()}}
        if witness:
            return {
                **result,
                "verdict": VERDICT_REFUTED,
                "counterexample": witness,
                "method": "exhaustive search over the given ranges",
            }

    witness, tried, note = sample_counterexample(
        search_target, sorts, names, bound=bound, samples=samples
    )
    result["sampling"] = {"assignments_tried": tried}
    if note:
        result["sampling"]["note"] = note
    if witness:
        return {
            **result,
            "verdict": VERDICT_REFUTED,
            "counterexample": witness,
            "method": "randomised sampling",
        }
    return {
        **result,
        "verdict": VERDICT_INCONCLUSIVE,
        "method": (
            "the solver was undecided and no sample falsified the claim; absence of a "
            "counterexample is not a proof"
        ),
    }


# --------------------------------------------------------------------------- #
# number theory
# --------------------------------------------------------------------------- #

_NUMBER_THEORY_OPS = {
    "is_prime", "next_prime", "prev_prime", "factorize", "divisors", "divisor_count",
    "divisor_sum", "totient", "mobius", "gcd", "lcm", "bezout", "mod", "mod_inverse",
    "crt", "valuation", "is_perfect_square", "is_perfect_power", "primitive_root",
    "legendre", "jacobi", "fibonacci", "lucas", "binomial", "factorial", "is_coprime",
    "radical", "carmichael", "order_mod",
}


def number_theory(operation: str, *,
                  numbers: Sequence[str] = (),
                  moduli: Sequence[str] = (),
                  residues: Sequence[str] = (),
                  exponent: str = "") -> dict[str, Any]:
    """Exact integer computations used throughout number-theoretic proofs."""
    op = operation.strip().lower()
    if op not in _NUMBER_THEORY_OPS:
        raise ValueError(
            f"unknown operation {operation!r}; supported: {', '.join(sorted(_NUMBER_THEORY_OPS))}"
        )
    values = [sp.Integer(sp.sympify(str(v))) for v in numbers]

    def need(count: int) -> None:
        if len(values) < count:
            raise ValueError(
                f"`{op}` needs {count} integer argument(s) in `numbers`, got {len(values)}"
            )

    if op == "is_prime":
        need(1)
        n = values[0]
        return {"n": str(n), "is_prime": bool(sp.isprime(n)),
                "note": "sympy proves primality; it does not merely test small factors"}
    if op == "next_prime":
        need(1)
        return {"n": str(values[0]), "next_prime": str(sp.nextprime(values[0]))}
    if op == "prev_prime":
        need(1)
        return {"n": str(values[0]), "previous_prime": str(sp.prevprime(values[0]))}
    if op == "factorize":
        need(1)
        n = values[0]
        factors = sp.factorint(n)
        return {
            "n": str(n),
            "factorization": {str(p): int(e) for p, e in sorted(factors.items())},
            "rendered": " * ".join(
                f"{p}^{e}" if e > 1 else f"{p}" for p, e in sorted(factors.items())
            ) or "1",
            "is_prime": len(factors) == 1 and list(factors.values())[0] == 1,
        }
    if op == "divisors":
        need(1)
        divisors = sp.divisors(values[0])
        return {"n": str(values[0]), "divisor_count": len(divisors),
                "divisors": [str(d) for d in divisors]}
    if op == "divisor_count":
        need(1)
        return {"n": str(values[0]), "divisor_count": int(sp.divisor_count(values[0]))}
    if op == "divisor_sum":
        need(1)
        return {"n": str(values[0]), "divisor_sum": str(sp.divisor_sigma(values[0]))}
    if op == "totient":
        need(1)
        return {"n": str(values[0]), "totient": str(sp.totient(values[0]))}
    if op == "mobius":
        need(1)
        return {"n": str(values[0]), "mobius": int(sp.mobius(values[0]))}
    if op == "gcd":
        need(2)
        return {"gcd": str(sp.gcd(*values)), "arguments": [str(v) for v in values]}
    if op == "lcm":
        need(2)
        return {"lcm": str(sp.lcm(*values)), "arguments": [str(v) for v in values]}
    if op == "is_coprime":
        need(2)
        return {"coprime": sp.gcd(*values) == 1, "arguments": [str(v) for v in values]}
    if op == "bezout":
        need(2)
        a, b = values[0], values[1]
        found = sp.gcdex(a, b)
        g = sp.gcd(a, b)
        return {
            "a": str(a), "b": str(b), "gcd": str(g),
            "x": str(found[0]), "y": str(found[1]),
            "identity": f"({found[0]})*({a}) + ({found[1]})*({b}) = {g}",
            "check": str(found[0] * a + found[1] * b),
            "note": "Bezout coefficients: useful for proving coprimality or invertibility",
        }
    if op == "mod":
        need(2)
        return {"a": str(values[0]), "m": str(values[1]),
                "remainder": str(sp.Mod(values[0], values[1]))}
    if op == "mod_inverse":
        need(2)
        a, m = values[0], values[1]
        if sp.gcd(a, m) != 1:
            return {"a": str(a), "m": str(m), "inverse": None,
                    "reason": f"gcd({a}, {m}) = {sp.gcd(a, m)} != 1, so no inverse exists"}
        return {"a": str(a), "m": str(m), "inverse": str(pow(int(a), -1, int(m)))}
    if op == "crt":
        if not residues or not moduli or len(residues) != len(moduli):
            raise ValueError("`crt` needs `residues` and `moduli` of equal length")
        res = [sp.Integer(sp.sympify(str(r))) for r in residues]
        mods = [sp.Integer(sp.sympify(str(m))) for m in moduli]
        for i in range(len(mods)):
            for j in range(i + 1, len(mods)):
                if sp.gcd(mods[i], mods[j]) != 1:
                    return {"solution": None,
                            "reason": f"moduli {mods[i]} and {mods[j]} are not coprime"}
        solution = sp.ntheory.modular.crt(mods, res)[0]
        product = sp.prod(mods)
        return {
            "residues": [str(r) for r in res], "moduli": [str(m) for m in mods],
            "solution": str(solution), "modulus": str(product),
            "general_solution": f"x = {solution} (mod {product})",
        }
    if op == "valuation":
        need(2)
        return {"n": str(values[0]), "p": str(values[1]),
                "valuation": int(sp.multiplicity(values[1], values[0]))}
    if op == "is_perfect_square":
        need(1)
        root = sp.sqrt(values[0])
        return {"n": str(values[0]), "is_perfect_square": root.is_Integer,
                "root": str(root) if root.is_Integer else None}
    if op == "is_perfect_power":
        need(1)
        found = sp.ntheory.factor_.perfect_power(values[0])
        return {"n": str(values[0]), "is_perfect_power": bool(found),
                "representation": f"{found[0]}^{found[1]}" if found else None}
    if op == "primitive_root":
        need(1)
        found = sp.primitive_root(values[0])
        return {"n": str(values[0]), "primitive_root": str(found) if found else None}
    if op == "legendre":
        need(2)
        return {"a": str(values[0]), "p": str(values[1]),
                "symbol": int(sp.legendre_symbol(values[0], values[1]))}
    if op == "jacobi":
        need(2)
        return {"a": str(values[0]), "n": str(values[1]),
                "symbol": int(sp.jacobi_symbol(values[0], values[1]))}
    if op == "fibonacci":
        need(1)
        return {"n": str(values[0]), "fibonacci": str(sp.fibonacci(values[0]))}
    if op == "lucas":
        need(1)
        return {"n": str(values[0]), "lucas": str(sp.lucas(values[0]))}
    if op == "binomial":
        need(2)
        return {"binomial": str(sp.binomial(values[0], values[1]))}
    if op == "factorial":
        need(1)
        return {"factorial": str(sp.factorial(values[0]))}
    if op == "radical":
        need(1)
        return {"n": str(values[0]), "radical": str(sp.radical(values[0]))}
    if op == "carmichael":
        need(1)
        return {"n": str(values[0]), "carmichael_lambda": str(sp.ntheory.factor_.reduced_totient(values[0]))}
    if op == "order_mod":
        need(2)
        a, m = values[0], values[1]
        if sp.gcd(a, m) != 1:
            return {"order": None,
                    "reason": f"{a} and {m} are not coprime, so no multiplicative order exists"}
        return {"a": str(a), "m": str(m), "order": str(sp.n_order(a, m))}
    raise ValueError(f"unhandled operation {op!r}")  # pragma: no cover
