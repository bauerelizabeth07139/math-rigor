"""Translation layer: AST -> sympy, sympy -> AST, AST -> z3."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))

import sympy as sp  # noqa: E402

from rigor.ast_nodes import parse  # noqa: E402
from rigor.smt import check_valid  # noqa: E402
from rigor.translate import (  # noqa: E402
    SympyContext,
    ast_to_sympy,
    make_symbol,
    sympy_to_ast,
)

PASS = 0
FAIL = 0


def expect(label, actual, wanted):
    global PASS, FAIL
    if actual == wanted:
        PASS += 1
        print(f"ok   {label}: {actual}")
    else:
        FAIL += 1
        print(f"FAIL {label}: {actual!r} (wanted {wanted!r})")


print("=== ast -> z3: validity decisions ===")
Z3_CASES = [
    ("forall n in Z: n^2 >= 0", "proven"),
    ("forall x in R: x^2 >= x", "refuted"),
    ("forall x in R: x^2 - 1 = (x-1)(x+1)", "proven"),
    ("forall x in R: x^2 + 1 > 0", "proven"),
    ("forall n in Z: 6 | n^3 - n", "proven"),
    ("forall n in Z: 3 | n^3 - n", "proven"),
    ("forall n in Z: 30 | n^5 - n", "proven"),
    ("forall n in Z: (n^2 + n) % 2 = 0", "proven"),
    ("forall n in Z: exists k in Z: n^3 - n = 6*k", "proven"),
    ("forall n in Z: n^3 - n = 6*k", "refuted"),          # k free: false
    ("forall n in N: n >= 0", "proven"),
    ("forall n in N: n > 0", "refuted"),                  # n = 0
    ("forall x in U: P(x) -> P(x)", "proven"),
    ("forall x in U: P(x) -> Q(x)", "refuted"),
    ("exists n in Z: n^2 = 4", "proven"),
    ("exists n in Z: n^2 = 5", "refuted"),
    ("forall x in Z: x > 0 -> x >= 1", "proven"),
    ("forall x in R: x > 0 -> x >= 1", "refuted"),
    ("forall a in R: forall b in R: (a+b)^2 = a^2 + 2*a*b + b^2", "proven"),
    ("forall x in R: abs(x) >= x", "proven"),
    ("forall x in R: forall y in R: abs(x+y) <= abs(x) + abs(y)", "proven"),
    ("forall n in Z: n > 5 -> n >= 6", "proven"),
]
for statement, expected in Z3_CASES:
    outcome = check_valid(statement)
    expect(f"z3 {statement}", outcome.status, expected)
    if outcome.status == "refuted" and outcome.model:
        print(f"       counterexample: {outcome.model}")

print("\n=== integer power stays integral (Z3_mk_power is real-valued) ===")
outcome = check_valid("forall n in Z: 6 | n^3 - n")
expect("n^3 is handled as integer arithmetic", outcome.status, "proven")

print("\n=== ast -> sympy ===")
ctx = SympyContext()
SYMPY_CASES = [
    ("x^2 - 1", "(x-1)(x+1)"),
    ("sin(x)^2 + cos(x)^2", "1"),
    (r"\frac{x^2-1}{x-1}", "x+1"),
    ("Sum(k, (k, 1, n))", "n*(n+1)/2"),
    ("n*(n+1)*(2*n+1)/6", "Sum(k^2, (k, 1, n))"),
]
for left, right in SYMPY_CASES:
    a = ast_to_sympy(parse(left), ctx)
    b = ast_to_sympy(parse(right), ctx)
    difference = sp.simplify(sp.expand(a - b))
    expect(f"sympy {left} = {right}", difference, 0)

print("\n=== sympy -> ast round trip ===")
for text in ["n*(n+1)/2", "x**2 + 2*x + 1", "sin(x) + 1/2", "Abs(x) - sqrt(y)"]:
    original = sp.sympify(text)
    node = sympy_to_ast(original)
    back = ast_to_sympy(node, SympyContext())
    expect(f"round trip {text}", sp.simplify(back - original), 0)

closed = sp.summation(sp.Symbol("k"), (sp.Symbol("k"), 1, sp.Symbol("n")))
expect("summation closes", sp.sstr(closed), "n**2/2 + n/2")
expect("closed form prints in the AST dialect",
       sympy_to_ast(closed).text(), "1/2*n+1/2*n**2")
expect("closed form round-trips back to the same polynomial",
       sp.simplify(ast_to_sympy(sympy_to_ast(closed), SympyContext())
                   - sp.Symbol("n") * (sp.Symbol("n") + 1) / 2), 0)

print("\n=== sympy booleans survive the round trip ===")
for value, wanted in ((sp.true, "verum"), (sp.false, "falsum"), (True, "verum")):
    expect(f"{value!r} -> AST", sympy_to_ast(value).text(), wanted)

print("\n=== symbol assumptions ===")
positive = make_symbol("x", ["positive"])
expect("positive symbol is positive", bool(positive.is_positive), True)
natural = make_symbol("n", ["natural"])
expect("natural symbol is a nonnegative integer",
       (bool(natural.is_integer), bool(natural.is_nonnegative)), (True, True))
try:
    make_symbol("q", ["nonsense-flag"])
    expect("unknown assumption is rejected", "accepted", "rejected")
except Exception:
    expect("unknown assumption is rejected", "rejected", "rejected")

print(f"\npassed {PASS}, failed {FAIL}")
sys.exit(1 if FAIL else 0)
