"""Translation between the rigor AST, sympy, and z3.

Three directions are needed by the workflow:

``ast_to_sympy``   symbolic manipulation, simplification, identity checking
``sympy_to_ast``   feeding sympy's closed forms (e.g. ``Sum`` results) back in
``ast_to_z3``      semantic validity: prove by refutation, or exhibit a model

The z3 layer performs sort inference so that ``forall n: 6 | n**3 - n`` works
without the caller spelling out ``n in Z``: any use of ``mod``/``|``/parity
predicates forces integer arithmetic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

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
    ParseError,
    Quant,
    Sqrt,
    Sym,
    TupleN,
    collect_sorts,
    collect_symbols,
    has_modular,
    walk,
)


class TranslateError(ValueError):
    """Raised when an AST cannot be expressed in the target backend."""


# --------------------------------------------------------------------------- #
# Sort inference shared by both backends
# --------------------------------------------------------------------------- #

SORT_INT = "int"
SORT_REAL = "real"
SORT_NAT = "nat"
SORT_RAT = "rat"
SORT_BOOL = "bool"
SORT_UNINTERP = "uninterpreted"

_INT_SORTS = {SORT_INT, SORT_NAT}
_NUMERIC_SORTS = {SORT_INT, SORT_REAL, SORT_NAT, SORT_RAT}


def uses_float_literal(node: Node) -> bool:
    return any(isinstance(n, Num) and not re.fullmatch(r"[+-]?\d+", n.value)
               for n in walk(node))


def infer_default_sort(node: Node) -> str:
    """Default numeric sort for undeclared variables."""
    if has_modular(node) or uses_float_literal(node):
        return SORT_INT if has_modular(node) else SORT_REAL
    for n in walk(node):
        if isinstance(n, App) and n.name in ("even", "odd", "divisible", "divides"):
            return SORT_INT
    return SORT_REAL


@dataclass
class SortTable:
    node: Node
    declared: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # A statement that mentions an integer at all is almost always a
        # statement about integers throughout: `forall n in Z: n**3 - n = 6*k`
        # means k is an integer too, not a real that may be -1/6.
        explicit = {**collect_sorts(self.node), **dict(self.declared)}
        if any(s in _INT_SORTS for s in explicit.values()):
            self.default = SORT_INT
        else:
            self.default = infer_default_sort(self.node)
        self.quantified: dict[str, str] = {}
        self.arithmetic: set[str] = set()
        self.boolean: set[str] = set()
        self._analyse(self.node, True)

    def _analyse(self, node: Node, boolean: bool) -> None:
        """Classify every symbol as propositional or numeric by its position.

        A bare ``P`` standing where a truth value is expected is a proposition,
        not a real; a ``P`` inside an arithmetic term is numeric.  Without this
        distinction z3 receives a Real constant where a Bool is required.
        """
        from .ast_nodes import Abs as _Abs
        from .ast_nodes import is_formula as _is_formula

        if isinstance(node, Quant):
            sort = node.sort
            if sort == SORT_UNINTERP:
                if self._numeric_use(node.body, node.var):
                    sort = self.declared.get(node.var) or self.default
                else:
                    sort = self.declared.get(node.var, SORT_UNINTERP)
            self.quantified[node.var] = sort
            self._analyse(node.body, True)
            return
        if isinstance(node, Sym):
            if node.name not in _FALSE_NAMES and node.name not in _TRUE_NAMES:
                (self.boolean if boolean else self.arithmetic).add(node.name)
            return
        if isinstance(node, Num):
            return
        if isinstance(node, BigOp):
            self.quantified.setdefault(node.var, SORT_INT)
            self._analyse(node.expr, False)
            self._analyse(node.lo, False)
            self._analyse(node.hi, False)
            return
        if isinstance(node, (App, TupleN)):
            args = node.args if isinstance(node, App) else node.items
            for arg in args:
                self._analyse(arg, False)
            return
        if isinstance(node, Cmp):
            # `P = Q` between two formulas is a biconditional, so those operands
            # sit in a boolean position.
            operands_boolean = node.op in ("eq", "ne") and (
                _is_formula(node.left) or _is_formula(node.right)
            )
            self._analyse(node.left, operands_boolean)
            self._analyse(node.right, operands_boolean)
            return
        if isinstance(node, (Not, And, Or, Imp, Iff)):
            for child in _children(node):
                self._analyse(child, True)
            return
        if isinstance(node, (Bin, Neg, Sqrt, _Abs)):
            for child in _children(node):
                self._analyse(child, False)
            return
        for child in _children(node):
            self._analyse(child, boolean)

    def _numeric_use(self, body: Node, var: str) -> bool:
        for n in walk(body):
            if isinstance(n, (Bin, Neg, Sqrt)) and self._mentions(n, var):
                return True
            if isinstance(n, Cmp) and self._mentions(n, var):
                # a comparison mentioning the variable is numeric evidence only
                # if the variable is not simply an argument of a predicate
                if not (isinstance(n.left, App) or isinstance(n.right, App)):
                    return True
        return False

    @staticmethod
    def _mentions(node: Node, var: str) -> bool:
        return any(isinstance(n, Sym) and n.name == var for n in walk(node))

    def sort_of(self, name: str) -> str:
        if name in self.declared:
            return self.declared[name]
        if name in self.quantified:
            return self.quantified[name]
        if name in self.boolean and name not in self.arithmetic:
            return SORT_BOOL
        return self.default

    def report(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for name in sorted(collect_symbols(self.node) | set(self.quantified)):
            out[name] = self.sort_of(name)
        return out


def _children(node: Node) -> list[Node]:
    from .ast_nodes import children_of

    return children_of(node)


# --------------------------------------------------------------------------- #
# AST -> sympy
# --------------------------------------------------------------------------- #

_ASSUMPTION_FLAGS = {
    "positive": {"positive": True},
    "negative": {"negative": True},
    "nonnegative": {"nonnegative": True},
    "nonpositive": {"nonpositive": True},
    "nonzero": {"nonzero": True},
    "real": {"real": True},
    "integer": {"integer": True},
    "rational": {"rational": True},
    "complex": {"complex": True},
    "natural": {"integer": True, "nonnegative": True},
    "nat": {"integer": True, "nonnegative": True},
    "even": {"integer": True, "even": True},
    "odd": {"integer": True, "odd": True},
}

_SYMPY_FUNCS = {
    "sqrt": sp.sqrt, "abs": sp.Abs, "Abs": sp.Abs, "exp": sp.exp,
    "ln": sp.log, "log": sp.log, "sin": sp.sin, "cos": sp.cos, "tan": sp.tan,
    "cot": sp.cot, "sec": sp.sec, "csc": sp.csc, "asin": sp.asin,
    "acos": sp.acos, "atan": sp.atan, "sinh": sp.sinh, "cosh": sp.cosh,
    "tanh": sp.tanh, "floor": sp.floor, "ceil": sp.ceiling, "sign": sp.sign,
    "min": sp.Min, "max": sp.Max, "gcd": sp.gcd, "lcm": sp.lcm,
    "factorial": sp.factorial, "binomial": sp.binomial, "re": sp.re, "im": sp.im,
    "conjugate": sp.conjugate, "gamma": sp.gamma, "erf": sp.erf,
}


def make_symbol(name: str, assumptions: Iterable[str] = ()) -> sp.Symbol:
    flags: dict[str, Any] = {}
    for a in assumptions:
        key = str(a).strip().lower()
        if key in _ASSUMPTION_FLAGS:
            flags.update(_ASSUMPTION_FLAGS[key])
        else:
            raise TranslateError(
                f"unknown assumption {a!r}; known: {', '.join(sorted(_ASSUMPTION_FLAGS))}"
            )
    return sp.Symbol(name, **flags)


_FALSE_NAMES = {"falsum", "false", "False", "⊥", "contradiction"}
_TRUE_NAMES = {"verum", "true", "True", "⊤"}


def is_falsum(node: Node) -> bool:
    return isinstance(node, Sym) and node.name in _FALSE_NAMES


def is_verum(node: Node) -> bool:
    return isinstance(node, Sym) and node.name in _TRUE_NAMES


def is_truth_constant(node: Node) -> bool:
    return is_falsum(node) or is_verum(node)


@dataclass
class SympyContext:
    symbols: dict[str, sp.Symbol] = field(default_factory=dict)
    functions: dict[str, Any] = field(default_factory=dict)

    def symbol(self, name: str) -> sp.Symbol:
        if name not in self.symbols:
            self.symbols[name] = sp.Symbol(name)
        return self.symbols[name]


def ast_to_sympy(node: Node, ctx: SympyContext | None = None) -> Any:
    """Translate an AST into a sympy object."""
    ctx = ctx or SympyContext()
    if isinstance(node, Num):
        text = node.value
        if re.fullmatch(r"[+-]?\d+", text):
            return sp.Integer(int(text))
        return sp.Float(text)
    if isinstance(node, Sym):
        if node.name in _FALSE_NAMES:
            return sp.false
        if node.name in _TRUE_NAMES:
            return sp.true
        # A symbol explicitly declared by the caller always wins.  Otherwise the
        # usual mathematical constants are recognised, so that
        # `verify_limit("(1+1/n)^n", "n", "oo", "e")` means Euler's number while
        # `symbolic_eval(..., variable="e")` still treats `e` as a variable.
        declared = node.name in ctx.symbols
        if node.name == "pi" and not declared:
            return sp.pi
        if node.name in ("e", "E") and not declared:
            return sp.E
        if node.name in ("oo", "inf", "infinity") and not declared:
            return sp.oo
        if node.name == "I" and not declared:
            return sp.I
        return ctx.symbol(node.name)
    if isinstance(node, App):
        name = node.name
        args = [ast_to_sympy(a, ctx) for a in node.args]
        if name in ("even",):
            return sp.Eq(sp.Mod(args[0], 2), 0)
        if name in ("odd",):
            return sp.Ne(sp.Mod(args[0], 2), 0)
        if name in ("divisible", "divides"):
            d, n = (args[0], args[1]) if name == "divisible" else (args[1], args[0])
            return sp.Eq(sp.Mod(n, d), 0)
        if name == "mod" and len(args) == 2:
            return sp.Mod(args[0], args[1])
        if name in ("Sum", "sum"):
            return _sympy_bigop("sum", node, ctx)
        if name in ("Product", "prod"):
            return _sympy_bigop("prod", node, ctx)
        if name in _SYMPY_FUNCS:
            func = _SYMPY_FUNCS[name]
            if name == "log" and len(args) == 2:
                return sp.log(args[0], args[1])
            return func(*args)
        if name in ctx.functions:
            return ctx.functions[name](*args)
        return sp.Function(name)(*args)
    if isinstance(node, TupleN):
        return sp.Tuple(*[ast_to_sympy(i, ctx) for i in node.items])
    if isinstance(node, Neg):
        return -ast_to_sympy(node.operand, ctx)
    if isinstance(node, Sqrt):
        return sp.sqrt(ast_to_sympy(node.operand, ctx))
    if isinstance(node, Abs):
        return sp.Abs(ast_to_sympy(node.operand, ctx))
    if isinstance(node, Bin):
        left = ast_to_sympy(node.left, ctx)
        right = ast_to_sympy(node.right, ctx)
        if node.op == "add":
            return left + right
        if node.op == "sub":
            return left - right
        if node.op == "mul":
            return left * right
        if node.op == "div":
            return left / right
        if node.op == "mod":
            return sp.Mod(left, right)
        if node.op == "pow":
            return left ** right
        raise TranslateError(f"unknown binary operator {node.op!r}")
    if isinstance(node, Cmp):
        left = ast_to_sympy(node.left, ctx)
        right = ast_to_sympy(node.right, ctx)
        table = {
            "eq": sp.Eq, "ne": sp.Ne, "lt": sp.Lt, "le": sp.Le,
            "gt": sp.Gt, "ge": sp.Ge,
            "divides": lambda a, b: sp.Eq(sp.Mod(b, a), 0),
            "not_divides": lambda a, b: sp.Ne(sp.Mod(b, a), 0),
        }
        if node.op not in table:
            raise TranslateError(f"unknown relation {node.op!r}")
        return table[node.op](left, right)
    if isinstance(node, Not):
        return sp.Not(ast_to_sympy(node.operand, ctx))
    if isinstance(node, And):
        return sp.And(*[ast_to_sympy(p, ctx) for p in node.parts])
    if isinstance(node, Or):
        return sp.Or(*[ast_to_sympy(p, ctx) for p in node.parts])
    if isinstance(node, Imp):
        return sp.Implies(ast_to_sympy(node.left, ctx), ast_to_sympy(node.right, ctx))
    if isinstance(node, Iff):
        return sp.Equivalent(ast_to_sympy(node.left, ctx), ast_to_sympy(node.right, ctx))
    if isinstance(node, Quant):
        raise TranslateError(
            "sympy cannot express quantified statements here; "
            "use the SMT-backed tools (verify_forall / logic_entails) for quantifiers"
        )
    if isinstance(node, BigOp):
        return _sympy_bigop(node.kind, node, ctx)
    raise TranslateError(f"cannot translate node of type {type(node).__name__}")


def _sympy_bigop(kind: str, node: Node, ctx: SympyContext) -> Any:
    if isinstance(node, BigOp):
        body, var, lo, hi = node.expr, node.var, node.lo, node.hi
    else:
        spec = node.args[1]
        if not isinstance(spec, TupleN) or len(spec.items) != 3:
            raise TranslateError("Sum/Product expects (expression, (index, lo, hi))")
        body, idx, lo, hi = node.args[0], spec.items[0], spec.items[1], spec.items[2]
        if not isinstance(idx, Sym):
            raise TranslateError("the summation index must be a symbol")
        var = idx.name
    cls = sp.Sum if kind == "sum" else sp.Product
    return cls(
        ast_to_sympy(body, ctx),
        (ctx.symbol(var), ast_to_sympy(lo, ctx), ast_to_sympy(hi, ctx)),
    )


# --------------------------------------------------------------------------- #
# sympy -> AST
# --------------------------------------------------------------------------- #


def sympy_to_ast(expr: Any) -> Node:
    """Convert a sympy expression back into the AST (used for closed forms)."""
    if expr is sp.true or expr is True:
        return Sym("verum")
    if expr is sp.false or expr is False:
        return Sym("falsum")
    if isinstance(expr, sp.logic.boolalg.BooleanTrue):
        return Sym("verum")
    if isinstance(expr, sp.logic.boolalg.BooleanFalse):
        return Sym("falsum")
    if isinstance(expr, sp.Integer):
        return Num(str(int(expr)))
    if isinstance(expr, sp.Rational):
        return Bin("div", Num(str(expr.p)), Num(str(expr.q)))
    if isinstance(expr, sp.Float):
        return Num(repr(float(expr)))
    if isinstance(expr, sp.Symbol):
        return Sym(expr.name)
    if expr is sp.pi:
        return Sym("pi")
    if expr is sp.oo:
        return Sym("oo")
    if expr is sp.I:
        return Sym("I")
    if isinstance(expr, sp.Pow):
        base, exponent = expr.args
        if exponent == sp.Rational(1, 2):
            return Sqrt(sympy_to_ast(base))
        return Bin("pow", sympy_to_ast(base), sympy_to_ast(exponent))
    if isinstance(expr, sp.Mul):
        args = list(expr.args)
        out = sympy_to_ast(args[0])
        for a in args[1:]:
            out = Bin("mul", out, sympy_to_ast(a))
        return out
    if isinstance(expr, sp.Add):
        args = list(expr.args)
        out = sympy_to_ast(args[0])
        for a in args[1:]:
            if a.could_extract_minus_sign():
                out = Bin("sub", out, sympy_to_ast(-a))
            else:
                out = Bin("add", out, sympy_to_ast(a))
        return out
    if isinstance(expr, sp.Abs):
        return Abs(sympy_to_ast(expr.args[0]))
    if isinstance(expr, sp.Mod):
        return Bin("mod", sympy_to_ast(expr.args[0]), sympy_to_ast(expr.args[1]))
    if isinstance(expr, sp.Sum):
        return BigOp("sum", sympy_to_ast(expr.function),
                     expr.limits[0][0].name,
                     sympy_to_ast(expr.limits[0][1]),
                     sympy_to_ast(expr.limits[0][2]))
    if isinstance(expr, sp.Product):
        return BigOp("prod", sympy_to_ast(expr.function),
                     expr.limits[0][0].name,
                     sympy_to_ast(expr.limits[0][1]),
                     sympy_to_ast(expr.limits[0][2]))
    if isinstance(expr, sp.Equality):
        return Cmp("eq", sympy_to_ast(expr.lhs), sympy_to_ast(expr.rhs))
    if isinstance(expr, sp.Unequality):
        return Cmp("ne", sympy_to_ast(expr.lhs), sympy_to_ast(expr.rhs))
    if isinstance(expr, sp.StrictLessThan):
        return Cmp("lt", sympy_to_ast(expr.lhs), sympy_to_ast(expr.rhs))
    if isinstance(expr, sp.LessThan):
        return Cmp("le", sympy_to_ast(expr.lhs), sympy_to_ast(expr.rhs))
    if isinstance(expr, sp.StrictGreaterThan):
        return Cmp("gt", sympy_to_ast(expr.lhs), sympy_to_ast(expr.rhs))
    if isinstance(expr, sp.GreaterThan):
        return Cmp("ge", sympy_to_ast(expr.lhs), sympy_to_ast(expr.rhs))
    if isinstance(expr, sp.And):
        return And(tuple(sympy_to_ast(a) for a in expr.args))
    if isinstance(expr, sp.Or):
        return Or(tuple(sympy_to_ast(a) for a in expr.args))
    if isinstance(expr, sp.Not):
        return Not(sympy_to_ast(expr.args[0]))
    if isinstance(expr, sp.Implies):
        return Imp(sympy_to_ast(expr.args[0]), sympy_to_ast(expr.args[1]))
    if isinstance(expr, sp.Equivalent):
        return Iff(sympy_to_ast(expr.args[0]), sympy_to_ast(expr.args[1]))
    if isinstance(expr, sp.Tuple):
        return TupleN(tuple(sympy_to_ast(a) for a in expr.args))
    if isinstance(expr, sp.Function):
        return App(type(expr).__name__, tuple(sympy_to_ast(a) for a in expr.args))
    if expr.is_Number:
        return Num(str(expr))
    raise TranslateError(f"cannot convert sympy node {expr!r} back to the AST")


# --------------------------------------------------------------------------- #
# AST -> z3
# --------------------------------------------------------------------------- #


@dataclass
class Z3Context:
    """Holds z3 declarations so a formula and its model stay consistent."""

    sorts: SortTable
    scope: dict[str, Any] = field(default_factory=dict)
    functions: dict[tuple[str, int], Any] = field(default_factory=dict)
    _uninterp: Any = None
    notes: list[str] = field(default_factory=list)

    # -- declarations ------------------------------------------------------ #
    def domain_sort(self) -> Any:
        import z3

        if self._uninterp is None:
            self._uninterp = z3.DeclareSort("U")
        return self._uninterp

    def sort_object(self, sort: str) -> Any:
        import z3

        if sort in (SORT_INT, SORT_NAT):
            return z3.IntSort()
        if sort in (SORT_REAL, SORT_RAT):
            return z3.RealSort()
        if sort == SORT_BOOL:
            return z3.BoolSort()
        return self.domain_sort()

    def const(self, name: str) -> Any:
        import z3

        if name in self.scope:
            return self.scope[name]
        c = z3.Const(name, self.sort_object(self.sorts.sort_of(name)))
        self.scope[name] = c
        return c

    def function(self, name: str, args: Sequence[Any], result_bool: bool) -> Any:
        import z3

        key = (name, len(args))
        if key in self.functions:
            return self.functions[key]
        arg_sorts = [a.sort() for a in args]
        result = z3.BoolSort() if result_bool else self.sort_object(self.sorts.default)
        f = z3.Function(name, *arg_sorts, result)
        self.functions[key] = f
        return f


def is_int_term(term: Any) -> bool:
    """True for z3 integers *and* Python ints, which z3py coerces lazily."""
    import z3

    return isinstance(term, int) and not isinstance(term, bool) or z3.is_int(term)


def is_real_term(term: Any) -> bool:
    import z3

    return isinstance(term, float) or z3.is_real(term)


def constant_fold_kind(term: Any) -> str:
    if isinstance(term, bool):
        return "a truth value"
    if isinstance(term, int):
        return "the integer %d" % term
    if isinstance(term, float):
        return "the real %r" % term
    try:
        return f"a term of sort {term.sort()}"
    except AttributeError:  # pragma: no cover - defensive
        return f"a value of type {type(term).__name__}"


def require_bool(term: Any, node: Node) -> Any:
    """Fail with an actionable message when a subformula is not a truth value.

    Without this, a statement such as ``n is an integer`` is silently read as the
    product ``n * is * an * integer`` and surfaces only as a z3 sort-mismatch
    exception.
    """
    import z3

    if isinstance(term, bool):
        return z3.BoolVal(term)
    if not z3.is_bool(term):
        raise TranslateError(
            f"`{node.text()}` is not a boolean formula: it evaluated to "
            f"{constant_fold_kind(term)}. If you meant a domain declaration, put it in "
            f"the `variables` argument instead (for example variables={{'n': 'int'}}), "
            f"not in the statement."
        )
    return term


def ast_to_z3(node: Node, ctx: Z3Context, boolean: bool = True) -> Any:
    """Translate an AST into a z3 expression.

    ``boolean`` states whether the node is expected to denote a truth value,
    which is how an otherwise-unknown ``P(x)`` is typed as a predicate rather
    than a numeric function.
    """
    import z3

    if isinstance(node, Num):
        # Plain Python numbers let z3py coerce to whatever numeric sort the
        # neighbouring term uses; IntVal/RealVal would force a sort here.
        return float(node.value) if "." in node.value else int(node.value)
    if isinstance(node, Sym):
        if node.name in _FALSE_NAMES:
            return z3.BoolVal(False)
        if node.name in _TRUE_NAMES:
            return z3.BoolVal(True)
        if node.name not in ctx.sorts.declared:
            if node.name == "pi":
                raise TranslateError(
                    "z3 has no exact pi; use the symbolic tools (verify_identity, "
                    "symbolic_eval) for statements about pi"
                )
            if node.name in ("e", "E"):
                raise TranslateError(
                    "z3 has no exact e; use the symbolic tools (verify_limit, "
                    "verify_identity, symbolic_eval) for statements about e, or declare "
                    "`e` in `variables` if it is meant to be an ordinary variable"
                )
            if node.name in ("oo", "inf", "infinity"):
                raise TranslateError("z3 cannot reason about infinity in this form")
        return ctx.const(node.name)
    if isinstance(node, Neg):
        return -ast_to_z3(node.operand, ctx, False)
    if isinstance(node, Bin):
        left = ast_to_z3(node.left, ctx, False)
        if node.op == "pow":
            return _pow_to_z3(left, node.right, ctx)
        right = ast_to_z3(node.right, ctx, False)
        return {
            "add": lambda a, b: a + b, "sub": lambda a, b: a - b,
            "mul": lambda a, b: a * b, "div": lambda a, b: a / b,
            "mod": lambda a, b: a % b,
        }[node.op](left, right)
    if isinstance(node, Sqrt):
        inner = ast_to_z3(node.operand, ctx, False)
        if is_real_term(inner):
            return z3.Sqrt(inner)
        if is_int_term(inner):
            return z3.Sqrt(z3.ToReal(inner) if not isinstance(inner, int) else float(inner))
        raise TranslateError("cannot take a square root of this z3 term")
    if isinstance(node, Abs):
        inner = ast_to_z3(node.operand, ctx, False)
        return z3.If(inner >= 0, inner, -inner)
    if isinstance(node, Cmp):
        return _cmp_to_z3(node, ctx)
    if isinstance(node, App):
        return _app_to_z3(node, ctx, boolean)
    if isinstance(node, Not):
        return z3.Not(require_bool(ast_to_z3(node.operand, ctx, True), node.operand))
    if isinstance(node, And):
        if not node.parts:
            return z3.BoolVal(True)
        return z3.And(*[require_bool(ast_to_z3(p, ctx, True), p) for p in node.parts])
    if isinstance(node, Or):
        if not node.parts:
            return z3.BoolVal(False)
        return z3.Or(*[require_bool(ast_to_z3(p, ctx, True), p) for p in node.parts])
    if isinstance(node, Imp):
        return z3.Implies(require_bool(ast_to_z3(node.left, ctx, True), node.left),
                          require_bool(ast_to_z3(node.right, ctx, True), node.right))
    if isinstance(node, Iff):
        left = require_bool(ast_to_z3(node.left, ctx, True), node.left)
        right = require_bool(ast_to_z3(node.right, ctx, True), node.right)
        return left == right
    if isinstance(node, Quant):
        return _quant_to_z3(node, ctx)
    if isinstance(node, BigOp):
        raise TranslateError(
            "z3 cannot evaluate a symbolic Sum/Product; close the form first "
            "(sympy_eval with operation='sum') or state the closed form directly"
        )
    raise TranslateError(f"cannot translate node of type {type(node).__name__} to z3")


_MAX_EXPANDED_POWER = 64


def _pow_to_z3(base: Any, exponent_node: Node, ctx: Z3Context) -> Any:
    """Raise ``base`` to ``exponent_node`` without silently leaving the integers.

    SMT-LIB defines ``^`` over the reals, so ``Z3_mk_power`` would turn an
    integer ``n**3`` into a real expression (and break ``mod``/``|``).  An
    integer base with a small literal exponent is therefore expanded into
    repeated multiplication, which also keeps z3 in the decidable-enough
    nonlinear-integer fragment instead of the power theory.
    """
    import z3

    if isinstance(exponent_node, Num) and re.fullmatch(r"\d+", exponent_node.value):
        e = int(exponent_node.value)
        if is_int_term(base):
            if e > _MAX_EXPANDED_POWER:
                raise TranslateError(
                    f"integer exponent {e} is too large to expand for z3; "
                    "supply a bound or restructure the claim"
                )
            result: Any = base
            for _ in range(e - 1):
                result = result * base
            return result if e >= 1 else 1
        return base ** e
    exponent = ast_to_z3(exponent_node, ctx, False)
    if is_int_term(base):
        ctx.notes.append(
            "integer base raised to a symbolic exponent was lifted to the reals; "
            "results about integrality may be weaker"
        )
        base = z3.ToReal(base) if not isinstance(base, int) else float(base)
    if is_int_term(exponent) and not isinstance(exponent, (int, float)):
        exponent = z3.ToReal(exponent)
    return base ** exponent


def _cmp_to_z3(node: Cmp, ctx: Z3Context) -> Any:
    import z3

    from .ast_nodes import is_formula as _is_formula

    if node.op in ("divides", "not_divides"):
        divisor = ast_to_z3(node.left, ctx, False)
        target = ast_to_z3(node.right, ctx, False)
        expr = target % divisor == 0
        return expr if node.op == "divides" else z3.Not(expr)
    # `P = Q` between two formulas compares truth values, not numbers
    operands_boolean = node.op in ("eq", "ne") and (
        _is_formula(node.left) or _is_formula(node.right)
    )
    left = ast_to_z3(node.left, ctx, operands_boolean)
    right = ast_to_z3(node.right, ctx, operands_boolean)
    if node.op == "eq":
        return left == right
    if node.op == "ne":
        return left != right
    # A comparison against a boolean-free term may itself be a boolean equality
    op = {"lt": lambda a, b: a < b, "le": lambda a, b: a <= b,
          "gt": lambda a, b: a > b, "ge": lambda a, b: a >= b}[node.op]
    return op(left, right)


def _app_to_z3(node: App, ctx: Z3Context, boolean: bool) -> Any:
    import z3

    name = node.name
    if name == "even" and len(node.args) == 1:
        return ast_to_z3(node.args[0], ctx, False) % 2 == 0
    if name == "odd" and len(node.args) == 1:
        return ast_to_z3(node.args[0], ctx, False) % 2 != 0
    if name == "divisible" and len(node.args) == 2:
        d = ast_to_z3(node.args[0], ctx, False)
        n = ast_to_z3(node.args[1], ctx, False)
        return n % d == 0
    if name == "divides" and len(node.args) == 2:
        d = ast_to_z3(node.args[0], ctx, False)
        n = ast_to_z3(node.args[1], ctx, False)
        return n % d == 0
    if name == "mod" and len(node.args) == 2:
        return ast_to_z3(node.args[0], ctx, False) % ast_to_z3(node.args[1], ctx, False)
    if name == "abs" or name == "Abs":
        inner = ast_to_z3(node.args[0], ctx, False)
        return z3.If(inner >= 0, inner, -inner)
    if name in ("min", "max") and len(node.args) >= 2:
        vals = [ast_to_z3(a, ctx, False) for a in node.args]
        return z3.Min(*vals) if name == "min" else z3.Max(*vals)
    if name == "sqrt":
        inner = ast_to_z3(node.args[0], ctx, False)
        if is_real_term(inner):
            return z3.Sqrt(inner)
        if is_int_term(inner):
            return z3.Sqrt(z3.ToReal(inner) if not isinstance(inner, int) else float(inner))
        raise TranslateError("z3 only supports sqrt over reals")
    if name in ("floor", "ceil", "sign", "ln", "log", "exp", "sin", "cos", "tan",
                "factorial", "binomial", "gamma", "erf"):
        raise TranslateError(
            f"z3 cannot decide statements involving {name}(...) exactly; "
            "use the symbolic tools or supply the closed form"
        )
    args = [ast_to_z3(a, ctx, False) for a in node.args]
    func = ctx.function(name, args, result_bool=boolean)
    return func(*args)


def _quant_to_z3(node: Quant, ctx: Z3Context) -> Any:
    import z3

    sort_name = ctx.sorts.sort_of(node.var)
    if node.var in ctx.sorts.quantified:
        sort_name = ctx.sorts.quantified[node.var]
    var = z3.Const(node.var, ctx.sort_object(sort_name))
    # push the bound variable so that Sym(name) inside the body resolves to it
    # rather than to a same-named free constant
    had = node.var in ctx.scope
    previous = ctx.scope.get(node.var)
    ctx.scope[node.var] = var
    try:
        body = ast_to_z3(node.body, ctx, True)
    finally:
        if had:
            ctx.scope[node.var] = previous
        else:
            ctx.scope.pop(node.var, None)
    if sort_name == SORT_NAT:
        bound = var >= 0
        body = z3.And(bound, body) if node.kind == "exists" else z3.Implies(bound, body)
    return z3.ForAll([var], body) if node.kind == "forall" else z3.Exists([var], body)


def z3_constants(node: Node, sorts: SortTable) -> tuple[Z3Context, dict[str, Any]]:
    """Declare every free symbol mentioned by ``node`` as a z3 constant."""
    ctx = Z3Context(sorts=sorts)
    out: dict[str, Any] = {}
    for name in sorted(collect_symbols(node)):
        if name in _FALSE_NAMES or name in _TRUE_NAMES:
            continue
        out[name] = ctx.const(name)
    return ctx, out
