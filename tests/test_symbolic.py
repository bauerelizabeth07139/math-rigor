"""Symbolic computation, identity verification, and high-precision numerics."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))

from rigor.symbolic import numeric_eval, symbolic_eval, verify_identity  # noqa: E402

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


print("=== symbolic_eval ===")
OPERATIONS = [
    (dict(expression="x^2 - 1", operation="factor"), "text", "(x - 1)*(x + 1)"),
    (dict(expression=r"\frac{x^2-1}{x-1}", operation="cancel"), "text", "x + 1"),
    (dict(expression="sin(x)^2 + cos(x)^2", operation="simplify"), "text", "1"),
    (dict(expression="x^3 - 3*x + 1", operation="diff", variable="x"), "text", "3*x**2 - 3"),
    (dict(expression="x^2", operation="integrate", variable="x"), "text", "x**3/3"),
    (dict(expression="x^2", operation="integrate", variable="x", lower="0", upper="1"),
     "text", "1/3"),
    (dict(expression="sin(x)/x", operation="limit", variable="x", point="0"), "text", "1"),
    (dict(expression="1/x", operation="limit", variable="x", point="oo"), "text", "0"),
    (dict(expression="x^2 - 4", operation="solve", variable="x"), "solution_count", 2),
    (dict(expression="k^2", operation="sum", variable="k", lower="1", upper="n"),
     "text", "n**3/3 + n**2/2 + n/6"),
    (dict(expression="Sum(k, (k, 1, n))", operation="simplify"),
     "text", "Sum(k, (k, 1, n))"),
    (dict(expression="x^2 - 3*x + 2", operation="subs", substitutions={"x": "5"}),
     "text", "12"),
    (dict(expression="x^2 + 2*x + 1", operation="coeffs", variable="x"), "degree", 2),
]
for kwargs, field, wanted in OPERATIONS:
    result = symbolic_eval(**kwargs)
    actual = result.get("result", {}).get(field, result.get(field))
    expect(f"{kwargs['operation']} {kwargs['expression'][:26]}", actual, wanted)

solutions = symbolic_eval(expression="x^2 - 4", operation="solve", variable="x")
expect("solutions are -2 and 2",
       sorted(item["text"] for item in solutions["solutions"]), ["-2", "2"])

roots = symbolic_eval(expression="x^3 - 6*x^2 + 11*x - 6", operation="roots", variable="x")
expect("cubic roots are 1, 2, 3",
       sorted(item["root"]["text"] for item in roots["roots"]), ["1", "2", "3"])

series = symbolic_eval(expression="exp(x)", operation="series", variable="x", point="0", order=3)
expect("exp series to order 3", series["result"]["text"], "x**3/6 + x**2/2 + x + 1")

try:
    symbolic_eval(expression="x", operation="no_such_operation")
    expect("unknown operation is rejected", "accepted", "rejected")
except ValueError:
    expect("unknown operation is rejected", "rejected", "rejected")

try:
    symbolic_eval(expression="x^2", operation="diff")
    expect("diff without a variable is rejected", "accepted", "rejected")
except ValueError:
    expect("diff without a variable is rejected", "rejected", "rejected")


print("\n=== verify_identity ===")
IDENTITIES = [
    # (lhs, rhs, extra kwargs, expected verdict)
    ("(x-1)(x+1)", "x^2 - 1", {}, "proven"),
    ("sin(x)^2 + cos(x)^2", "1", {}, "proven"),
    ("(a+b)^2", "a^2 + 2*a*b + b^2", {}, "proven"),
    (r"\frac{x^2-1}{x-1}", "x+1", {}, "proven"),
    ("Sum(k, (k, 1, n))", "n*(n+1)/2", {}, "proven"),
    ("(n+1)^2", "n^2 + 1", {}, "refuted"),
    ("sqrt(x^2)", "x", {}, "refuted"),
    ("sqrt(x^2)", "x", {"assumptions": ["x: positive"]}, "proven"),
    ("log(x*y)", "log(x) + log(y)", {}, "inconclusive"),
    ("log(x*y)", "log(x) + log(y)",
     {"assumptions": ["x: positive", "y: positive"]}, "proven"),
    ("log(exp(x))", "x", {}, "inconclusive"),
]
for lhs, rhs, extra, expected in IDENTITIES:
    result = verify_identity(lhs, rhs, **extra)
    detail = extra.get("assumptions", "")
    expect(f"identity {lhs} = {rhs} {detail}".strip(), result["verdict"], expected)

refuted = verify_identity("(n+1)^2", "n^2 + 1")
expect("a refuted identity carries a witness", bool(refuted.get("counterexample")), True)
inconclusive = verify_identity("log(x*y)", "log(x) + log(y)")
expect("an inconclusive identity reports the residual difference",
       bool(inconclusive.get("residual_difference")), True)

print("\n=== symbolic_numeric ===")
exact = numeric_eval("Sum(k, (k, 1, 100))")
expect("sum of 1..100 is an exact integer", exact["is_exact_integer"], True)
expect("sum of 1..100 equals 5050", exact["exact_fraction"], "5050")

rational = numeric_eval("x^2 + y", {"x": "3", "y": "1/4"})
expect("3^2 + 1/4 is the exact rational 37/4", rational["exact_fraction"], "37/4")

pi_value = numeric_eval("pi", precision=25)
expect("pi is not reported as rational", pi_value["is_exact_rational"], False)
expect("pi is quoted to the requested precision",
       pi_value["value"].startswith("3.141592653589793238462643"), True)

symbolic_value = numeric_eval("3^100")
expect("3^100 is an exact integer", symbolic_value["is_exact_integer"], True)
expect("3^100 has the right leading digits",
       symbolic_value["exact_fraction"].startswith("515377520732011331036461129765621272702107522001"),
       True)

unbound = numeric_eval("x + 1")
expect("unsubstituted symbols are reported", unbound.get("unsubstituted_symbols"), ["x"])

print(f"\npassed {PASS}, failed {FAIL}")
sys.exit(1 if FAIL else 0)
