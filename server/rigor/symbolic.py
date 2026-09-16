"""Symbolic manipulation and identity verification built on sympy.

The guiding rule is that a symbolic result is *evidence*, not a proof, unless the
difference actually collapses to zero under stated assumptions.  When sympy
cannot collapse it, the verdict is ``inconclusive`` and the residual difference
is reported -- the workflow then has to supply assumptions or a different route.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

import mpmath
import sympy as sp

from .ast_nodes import BigOp, Node, Num, Sym, parse
from .translate import (
    SympyContext,
    TranslateError,
    ast_to_sympy,
    make_symbol,
    sympy_to_ast,
)

VERDICT_PROVEN = "proven"
VERDICT_REFUTED = "refuted"
VERDICT_INCONCLUSIVE = "inconclusive"
VERDICT_ERROR = "error"

_DEFAULT_PRECISION = 30
_COMPARISON_OPS = ("<=", ">=", "!=", "==", "<", ">", "=", "∈", "in ")


# --------------------------------------------------------------------------- #
# Assumptions
# --------------------------------------------------------------------------- #


@dataclass
class AssumptionSet:
    """Symbol properties (`x positive`) separated from logical constraints (`x > 0`)."""

    properties: dict[str, list[str]] = field(default_factory=dict)
    constraints: list[str] = field(default_factory=list)

    def symbols(self) -> SympyContext:
        ctx = SympyContext()
        for name, flags in self.properties.items():
            ctx.symbols[name] = make_symbol(name, flags)
        return ctx


def split_assumptions(items: Iterable[str]) -> AssumptionSet:
    """Accept both ``"x: positive"`` and ``"x >= 0"`` in one list."""
    result = AssumptionSet()
    for raw in items:
        text = str(raw).strip()
        if not text:
            continue
        if any(op in text for op in _COMPARISON_OPS):
            result.constraints.append(text)
            continue
        cleaned = text.replace(":", " ").replace(",", " ")
        parts = cleaned.split()
        if len(parts) >= 2:
            name, *flags = parts
            result.properties.setdefault(name, []).extend(flags)
        else:
            raise ValueError(
                f"cannot read the assumption {raw!r}; use `x: positive` for a symbol "
                "property or `x > 0` for a constraint"
            )
    return result


def _symbol_context(variables: Sequence[str], assumptions: AssumptionSet) -> SympyContext:
    ctx = SympyContext()
    for name in variables:
        flags = assumptions.properties.get(name, [])
        ctx.symbols[name] = make_symbol(name, flags)
    for name, flags in assumptions.properties.items():
        if name not in ctx.symbols:
            ctx.symbols[name] = make_symbol(name, flags)
    return ctx


# --------------------------------------------------------------------------- #
# Core conversions
# --------------------------------------------------------------------------- #


def to_sympy(expression: str, assumptions: AssumptionSet | None = None,
             variables: Sequence[str] = ()) -> Any:
    assumptions = assumptions or AssumptionSet()
    ctx = _symbol_context(list(variables), assumptions)
    node = parse(expression, functions=[])
    return ast_to_sympy(node, ctx)


def render(expr: Any) -> dict[str, str]:
    return {"text": sp.sstr(expr), "latex": sp.latex(expr)}


# --------------------------------------------------------------------------- #
# symbolic_eval
# --------------------------------------------------------------------------- #

_SIMPLIFIERS = (
    ("simplify", lambda e: sp.simplify(e)),
    ("trigsimp", lambda e: sp.trigsimp(e)),
    ("cancel", lambda e: sp.cancel(e)),
    ("factor", lambda e: sp.factor(e)),
    ("expand", lambda e: sp.expand(e)),
    ("radsimp", lambda e: sp.radsimp(e)),
    ("powsimp", lambda e: sp.powsimp(e, force=True)),
    ("logcombine", lambda e: sp.logcombine(e, force=True)),
    ("combsimp", lambda e: sp.combsimp(e)),
    ("ratsimp", lambda e: sp.ratsimp(e)),
)


def try_collapse(expr: Any) -> tuple[bool, Any, str]:
    """Try a battery of normalisations; report the first that reaches zero."""
    best = expr
    for name, function in _SIMPLIFIERS:
        try:
            candidate = function(expr)
        except Exception:  # noqa: BLE001 - sympy raises many unrelated types
            continue
        if candidate == 0:
            return True, sp.Integer(0), name
        if sp.count_ops(candidate) < sp.count_ops(best):
            best = candidate
    return False, best, ""


_OPERATIONS = {
    "simplify", "expand", "factor", "collect", "cancel", "apart", "together",
    "trigsimp", "ratsimp", "diff", "integrate", "limit", "series", "solve",
    "roots", "sum", "product", "coeffs", "subs", "numeric", "latex", "domain",
}


def symbolic_eval(
    expression: str,
    operation: str = "simplify",
    variable: str = "",
    *,
    point: str = "",
    direction: str = "+",
    order: int = 1,
    lower: str = "",
    upper: str = "",
    substitutions: Mapping[str, str] | None = None,
    precision: int = _DEFAULT_PRECISION,
    assumptions: Sequence[str] = (),
) -> dict[str, Any]:
    """Perform one symbolic operation and report the result with its LaTeX form."""
    op = operation.strip().lower()
    if op not in _OPERATIONS:
        raise ValueError(f"unknown operation {operation!r}; supported: {', '.join(sorted(_OPERATIONS))}")
    assumption_set = split_assumptions(assumptions)
    variables = [v.strip() for v in re.split(r"[,\s]+", variable) if v.strip()]
    ctx = _symbol_context(variables, assumption_set)
    node = parse(expression, functions=[])
    expr = ast_to_sympy(node, ctx)
    var = ctx.symbol(variables[0]) if variables else None

    result: Any
    notes: list[str] = []
    if op == "simplify":
        result = sp.simplify(expr)
    elif op == "expand":
        result = sp.expand(expr)
    elif op == "factor":
        result = sp.factor(expr)
    elif op == "collect":
        result = sp.collect(expr, var) if var is not None else sp.collect(expr, expr.free_symbols)
    elif op == "cancel":
        result = sp.cancel(expr)
    elif op == "apart":
        result = sp.apart(expr, var) if var is not None else sp.apart(expr)
    elif op == "together":
        result = sp.together(expr)
    elif op == "trigsimp":
        result = sp.trigsimp(expr)
    elif op == "ratsimp":
        result = sp.ratsimp(expr)
    elif op == "diff":
        if var is None:
            raise ValueError("`diff` needs the `variable` argument")
        result = sp.diff(expr, var, order)
    elif op == "integrate":
        if var is None:
            raise ValueError("`integrate` needs the `variable` argument")
        if lower and upper:
            result = sp.integrate(
                expr, (var, to_sympy(lower, assumption_set), to_sympy(upper, assumption_set))
            )
        else:
            result = sp.integrate(expr, var)
    elif op == "limit":
        if var is None:
            raise ValueError("`limit` needs the `variable` argument")
        if not point:
            raise ValueError("`limit` needs the `point` argument (use `oo` for infinity)")
        result = sp.limit(expr, var, to_sympy(point, assumption_set), dir=direction)
    elif op == "series":
        if var is None:
            raise ValueError("`series` needs the `variable` argument")
        at = to_sympy(point, assumption_set) if point else sp.Integer(0)
        result = sp.series(expr, var, at, order + 1).removeO()
    elif op == "solve":
        if var is None:
            raise ValueError("`solve` needs the `variable` argument")
        target = expr if isinstance(expr, sp.Equality) else sp.Eq(expr, 0)
        solutions = sp.solve(target, var, dict=False)
        if not isinstance(solutions, list):
            solutions = [solutions]
        result_list = [render(_maybe_clean(s)) for s in solutions]
        return {
            "operation": op, "input": render(expr), "solutions": result_list,
            "solution_count": len(result_list),
            "notes": [f"solved for {var}"],
        }
    elif op == "roots":
        if var is None:
            raise ValueError("`roots` needs the `variable` argument")
        found = sp.roots(expr, var)
        return {
            "operation": op, "input": render(expr),
            "roots": [{"root": render(r), "multiplicity": int(m)} for r, m in found.items()],
            "note": "only the roots sympy can express exactly are listed",
        }
    elif op == "sum":
        if var is None or not lower or not upper:
            raise ValueError("`sum` needs `variable`, `lower` and `upper`")
        result = sp.summation(
            expr, (var, to_sympy(lower, assumption_set), to_sympy(upper, assumption_set))
        )
    elif op == "product":
        if var is None or not lower or not upper:
            raise ValueError("`product` needs `variable`, `lower` and `upper`")
        result = sp.product(
            expr, (var, to_sympy(lower, assumption_set), to_sympy(upper, assumption_set))
        )
    elif op == "coeffs":
        if var is None:
            raise ValueError("`coeffs` needs the `variable` argument")
        poly = sp.Poly(expr, var)
        return {
            "operation": op, "input": render(expr), "degree": int(poly.degree()),
            "coefficients": [render(c) for c in poly.all_coeffs()],
            "note": "coefficients are listed from the highest degree downwards",
        }
    elif op == "subs":
        if not substitutions:
            raise ValueError("`subs` needs the `substitutions` argument, e.g. {\"x\": \"2\"}")
        mapping = {ctx.symbol(k): to_sympy(v, assumption_set) for k, v in substitutions.items()}
        result = expr.subs(mapping)
    elif op == "numeric":
        result = sp.N(expr, precision)
    elif op == "latex":
        return {"operation": op, "input": render(expr), "latex": sp.latex(expr)}
    elif op == "domain":
        free = sorted(str(s) for s in expr.free_symbols)
        return {
            "operation": op, "input": render(expr),
            "free_symbols": free,
            "notes": [
                "domain questions are answered by verify_forall or verify_inequality, "
                "which take the domain explicitly",
            ],
        }
    else:  # pragma: no cover - guarded by the membership check above
        raise ValueError(f"unhandled operation {op!r}")

    payload: dict[str, Any] = {
        "operation": op,
        "input": render(expr),
        "result": render(result),
    }
    if notes:
        payload["notes"] = notes
    return payload


def _maybe_clean(value: Any) -> Any:
    try:
        return sp.simplify(value)
    except Exception:  # noqa: BLE001
        return value


# --------------------------------------------------------------------------- #
# Numeric evaluation
# --------------------------------------------------------------------------- #


def numeric_eval(
    expression: str,
    substitutions: Mapping[str, str] | None = None,
    *,
    precision: int = _DEFAULT_PRECISION,
) -> dict[str, Any]:
    """Evaluate an expression at high precision and report how exact the value is."""
    assumption_set = split_assumptions([])
    ctx = SympyContext()
    node = parse(expression, functions=[])
    expr = ast_to_sympy(node, ctx)
    for name in sorted(expr.free_symbols, key=str):
        ctx.symbols.setdefault(name.name, name)
    mapping = {}
    for key, value in (substitutions or {}).items():
        symbol = ctx.symbols.get(key) or sp.Symbol(key)
        mapping[symbol] = to_sympy(value)
    if mapping:
        expr = expr.subs(mapping)
    remaining = sorted(str(s) for s in expr.free_symbols)
    # `doit` closes any Sum/Product with concrete bounds so exactness is judged
    # on the value rather than on the unevaluated form
    exact = sp.simplify(expr.doit())
    with mpmath.workdps(precision):
        value = sp.N(expr, precision)
        out: dict[str, Any] = {
            "expression": sp.sstr(expr),
            "value": sp.sstr(value),
            "precision_digits": precision,
            "is_exact_integer": bool(exact.is_Integer),
            "is_exact_rational": bool(exact.is_Rational) and not bool(exact.is_Integer),
        }
        if exact.is_Rational:
            out["exact_fraction"] = sp.sstr(sp.Rational(exact))
        try:
            out["decimal"] = mpmath.nstr(mpmath.mpf(str(value)), precision)
        except Exception:  # noqa: BLE001
            pass
    if remaining:
        out["unsubstituted_symbols"] = remaining
        out["note"] = "the value is not a number until these symbols are substituted"
    return out


# --------------------------------------------------------------------------- #
# Identity verification
# --------------------------------------------------------------------------- #

_SAMPLE_RATIONALS = [(1, 2), (-1, 2), (3, 2), (2, 1), (-2, 1),
                     (1, 3), (5, 7), (-3, 4), (4, 1), (7, 5)]
_IRRATIONAL_SAMPLES = ["sqrt(2)", "pi", "e", "sqrt(3)/2"]


def _sample_values(include_irrational: bool) -> list[Any]:
    values: list[Any] = [sp.Rational(n, d) for n, d in _SAMPLE_RATIONALS]
    if include_irrational:
        values += [sp.sqrt(2), sp.pi, sp.E, sp.sqrt(3) / 2]
    return values


def verify_identity(
    lhs: str,
    rhs: str,
    *,
    variables: Sequence[str] = (),
    assumptions: Sequence[str] = (),
    precision: int = _DEFAULT_PRECISION,
    samples_per_symbol: int = 12,
) -> dict[str, Any]:
    """Decide whether two expressions are the same function.

    Returns ``proven`` when the difference collapses to zero under the stated
    assumptions, ``refuted`` with a witness when a sample falsifies it, and
    ``inconclusive`` otherwise -- in that case the residual difference is
    reported so the caller can see what is still unproved.
    """
    assumption_set = split_assumptions(assumptions)
    names = [v.strip() for v in variables if str(v).strip()]
    ctx = _symbol_context(names, assumption_set)
    try:
        left = ast_to_sympy(parse(lhs, functions=[]), ctx)
        right = ast_to_sympy(parse(rhs, functions=[]), ctx)
    except Exception as exc:  # noqa: BLE001
        return {"verdict": VERDICT_ERROR, "reason": f"{type(exc).__name__}: {exc}"}

    for name in sorted({str(s) for s in (left.free_symbols | right.free_symbols)}):
        ctx.symbols.setdefault(name, sp.Symbol(name))

    difference = sp.simplify(left - right)
    collapsed, residual, how = try_collapse(difference)
    if collapsed:
        return {
            "verdict": VERDICT_PROVEN,
            "method": f"the difference reduces to zero by {how}",
            "assumptions_used": sorted(assumption_set.properties) or None,
            "lhs": sp.sstr(left),
            "rhs": sp.sstr(right),
        }

    symbols = sorted(left.free_symbols | right.free_symbols, key=str)
    witness = _search_counterexample(left, right, symbols, assumption_set, precision,
                                     samples_per_symbol)
    if witness is not None:
        return {
            "verdict": VERDICT_REFUTED,
            "method": "a numerical sample falsifies the identity",
            "counterexample": witness["assignment"],
            "lhs_value": witness["lhs"],
            "rhs_value": witness["rhs"],
            "note": "the two sides are genuinely different functions, not merely hard to simplify",
        }

    return {
        "verdict": VERDICT_INCONCLUSIVE,
        "method": "sympy could not reduce the difference to zero and no sample falsified it",
        "residual_difference": sp.sstr(residual),
        "residual_latex": sp.latex(residual),
        "hints": [
            "state the assumptions the identity needs, e.g. assumptions=['x: positive']",
            "if the identity only holds on a domain, prove it pointwise with verify_forall",
            "an identity that survives sampling but does not simplify may still be false "
            "on a measure-zero set",
        ],
    }


def _is_admissible_sub(assignment: Mapping[str, Any], assumption_set: AssumptionSet) -> bool:
    """Reject samples that violate a stated symbol property."""
    for name, value in assignment.items():
        flags = assumption_set.properties.get(name, [])
        if "positive" in flags and not (value > 0):
            return False
        if "negative" in flags and not (value < 0):
            return False
        if ("nonzero" in flags or "positive" in flags or "negative" in flags) and value == 0:
            return False
        if ("integer" in flags or "natural" in flags or "even" in flags or "odd" in flags):
            if not sp.Rational(value).is_Integer:
                return False
    return True


def _search_counterexample(
    left: sp.Expr,
    right: sp.Expr,
    symbols: Sequence[sp.Symbol],
    assumption_set: AssumptionSet,
    precision: int,
    samples_per_symbol: int,
) -> dict[str, Any] | None:
    import itertools

    if not symbols:
        return None
    pool: list[list[Any]] = []
    per_symbol = min(samples_per_symbol, len(_SAMPLE_RATIONALS) + len(_IRRATIONAL_SAMPLES))
    for index, symbol in enumerate(symbols):
        values = _sample_values(include_irrational=index == 0)
        pool.append(values[:per_symbol])
    checked = 0
    for combination in itertools.product(*pool):
        if len(symbols) > 3 and checked > 4000:
            break
        assignment = dict(zip((s.name for s in symbols), combination))
        if not _is_admissible_sub(assignment, assumption_set):
            continue
        checked += 1
        try:
            with mpmath.workdps(precision):
                lhs_value = sp.N(left.subs(dict(zip(symbols, combination))), precision)
                rhs_value = sp.N(right.subs(dict(zip(symbols, combination))), precision)
                if not (lhs_value.is_finite and rhs_value.is_finite):
                    continue
                lhs_float = mpmath.mpf(str(lhs_value))
                rhs_float = mpmath.mpf(str(rhs_value))
                scale = max(1, abs(lhs_float), abs(rhs_float))
                if abs(lhs_float - rhs_float) > mpmath.mpf(10) ** (-(precision - 10)) * scale:
                    return {
                        "assignment": {k: sp.sstr(v) for k, v in assignment.items()},
                        "lhs": sp.sstr(lhs_value),
                        "rhs": sp.sstr(rhs_value),
                    }
        except Exception:  # noqa: BLE001 - singular samples are simply skipped
            continue
    return None


def _is_admissible_sub(assignment: Mapping[str, Any], assumption_set: AssumptionSet) -> bool:
    for name, value in assignment.items():
        flags = assumption_set.properties.get(name, [])
        if "positive" in flags and not (value > 0):
            return False
        if "negative" in flags and not (value < 0):
            return False
        if ("nonzero" in flags or "positive" in flags or "negative" in flags) and value == 0:
            return False
        if ("integer" in flags or "natural" in flags) and not sp.Integer(value).q == 1 \
                if False else False:
            return False
    return True
