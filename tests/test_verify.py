"""High-level verifiers: forall, inequality, induction, limits, counterexamples, number theory."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))

from rigor.verify import (  # noqa: E402
    find_counterexample,
    number_theory,
    sample_counterexample,
    verify_forall,
    verify_induction,
    verify_inequality,
    verify_limit,
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


print("=== verify_forall ===")
FORALL = [
    ("forall n in Z: n^2 >= 0", "proven"),
    ("forall x in R: x^2 >= x", "refuted"),
    ("forall n in Z: 6 | n^3 - n", "proven"),
    ("forall n in Z: exists k in Z: n^3 - n = 6*k", "proven"),
    ("forall a in R: forall b in R: a >= 0 & b >= 0 -> (a+b)/2 >= sqrt(a*b)", "proven"),
    ("forall x in Z: x > 0 -> x >= 1", "proven"),
    ("forall n in N: n > 0", "refuted"),
]
for statement, expected in FORALL:
    outcome = verify_forall(statement)
    expect(statement[:52], outcome["verdict"], expected)

refuted = verify_forall("forall x in R: x^2 >= x")
expect("a refuted forall reports a counterexample",
       refuted["counterexample"].get("x"), "1/2")

# Sampling must run on the skolemised body, otherwise the quantified variable is
# bound and every sample is inadmissible.
sampled = verify_forall("forall a in R: forall b in R: (a+b)/2 >= sqrt(a*b)")
expect("sampling reaches inside the quantifiers", sampled["verdict"], "refuted")
expect("sampling reports which variables it sampled",
       "a" in (sampled.get("counterexample") or {}), True)
expect("sampling reports what it tried",
       sampled.get("sampling", {}).get("assignments_tried", 0) > 0, True)

print("\n=== verify_induction ===")
INDUCTION = [
    ("Sum(k, (k, 1, n)) = n*(n+1)/2", "n", "0", "proven"),
    ("Sum(k^2, (k, 1, n)) = n*(n+1)*(2*n+1)/6", "n", "1", "proven"),
    ("6 | n^3 - n", "n", "0", "proven"),
    ("n^2 <= n", "n", "0", "refuted"),
    ("Sum(k, (k, 1, n)) = n^2", "n", "0", "refuted"),
]
for predicate, variable, start, expected in INDUCTION:
    outcome = verify_induction(predicate, variable, start)
    expect(f"induction {predicate[:40]}", outcome["verdict"], expected)

closing = verify_induction("Sum(k, (k, 1, n)) = n*(n+1)/2", "n", "0")
expect("a solvable symbolic sum is closed instead of being left as an obligation",
       closing["method"], "the closed form makes both sides identical")

divisibility = verify_induction("6 | n^3 - n", "n", "0")
expect("a divisibility induction proves its base case",
       divisibility["base_case"]["status"], "proven")
expect("a divisibility induction proves its step",
       divisibility["inductive_step"]["status"], "proven")

missing_base = verify_induction("n < 2^n", "n", "0")
expect("a failing inductive step reports the k that breaks it",
       bool(missing_base.get("inductive_step", {}).get("counterexample")) or
       missing_base["verdict"] == "inconclusive", True)

no_variable = verify_induction("m^2 >= 0", "n", "0")
expect("an induction variable that does not occur is rejected",
       no_variable["verdict"], "error")

print("\n=== verify_inequality ===")
expect("x^2 + 1 > 0 over R",
       verify_inequality("x^2 + 1", ">", "0", variables={"x": "real"})["verdict"],
       "proven")
expect("x + 1/x >= 2 for x > 0",
       verify_inequality("x + 1/x", ">=", "2", variables={"x": "real"},
                         constraints=["x > 0"])["verdict"], "proven")
expect("x + 1/x >= 2 without the domain condition",
       verify_inequality("x + 1/x", ">=", "2", variables={"x": "real"})["verdict"],
       "refuted")

inferred = verify_inequality("x^2 + 1", ">", "0")
expect("missing domains are inferred and disclosed",
       bool(inferred.get("notes")), True)
expect("the inferred domain is reported", inferred["domains"], {"x": "real"})

try:
    verify_inequality("x", "approximately", "y", variables={"x": "real"})
    expect("an unknown relation is rejected", "accepted", "rejected")
except ValueError:
    expect("an unknown relation is rejected", "rejected", "rejected")

print("\n=== verify_limit ===")
expect("sin(x)/x -> 1",
       verify_limit("sin(x)/x", "x", "0", "1")["verdict"], "proven")
expect("(1+1/n)^n -> e",
       verify_limit("(1+1/n)^n", "n", "oo", "e")["verdict"], "proven")
wrong = verify_limit("(1+1/n)^n", "n", "oo", "1")
expect("a wrong limit is refuted", wrong["verdict"], "refuted")
expect("the true value is reported", wrong["computed_limit"], "E")
expect("sin(x)/x -> 0 is refuted",
       verify_limit("sin(x)/x", "x", "0", "0")["verdict"], "refuted")
expect("one-sided 1/x from above -> oo",
       verify_limit("1/x", "x", "0", "oo", direction="+")["verdict"], "proven")

print("\n=== find_counterexample ===")
expect("forall n in R: n^2 >= n is false",
       find_counterexample("forall n in R: n^2 >= n")["verdict"], "refuted")
expect("the counterexample is a real witness",
       find_counterexample("forall n in R: n^2 >= n")["counterexample"].get("n"), "1/2")
expect("forall n in Z: n > 2 -> n^2 > 2*n holds",
       find_counterexample("forall n in Z: n > 2 -> n^2 > 2*n",
                           variables={"n": "int"})["verdict"], "proven")
exhaustive = find_counterexample("forall n in Z: n^2 >= 2*n", variables={"n": "int"},
                                 ranges={"n": list(range(-10, 11))})
expect("exhaustive search finds the failure at n = 1",
       exhaustive["verdict"], "refuted")

print("\n=== number_theory ===")
expect("1000003 is prime", number_theory("is_prime", numbers=["1000003"])["is_prime"], True)
expect("360 = 2^3 * 3^2 * 5",
       number_theory("factorize", numbers=["360"])["rendered"], "2^3 * 3^2 * 5")
expect("28 has 6 divisors", number_theory("divisors", numbers=["28"])["divisor_count"], 6)
expect("totient(36) = 12", number_theory("totient", numbers=["36"])["totient"], "12")
bezout = number_theory("bezout", numbers=["240", "46"])
expect("bezout identity is verified by recomputation", bezout["check"], "2")
expect("gcd(240, 46) = 2", bezout["gcd"], "2")
expect("3 is invertible mod 11",
       number_theory("mod_inverse", numbers=["3", "11"])["inverse"], "4")
expect("2 is not invertible mod 4",
       number_theory("mod_inverse", numbers=["2", "4"])["inverse"], None)
crt = number_theory("crt", residues=["2", "3", "2"], moduli=["3", "5", "7"])
expect("CRT gives 23 mod 105", crt["general_solution"], "x = 23 (mod 105)")
expect("CRT rejects non-coprime moduli",
       number_theory("crt", residues=["1", "1"], moduli=["4", "6"])["solution"], None)
expect("v_2(72) = 3", number_theory("valuation", numbers=["72", "2"])["valuation"], 3)
expect("144 is a perfect square",
       number_theory("is_perfect_square", numbers=["144"])["root"], "12")
expect("(2/7) = 1", number_theory("legendre", numbers=["2", "7"])["symbol"], 1)
expect("ord_7(2) = 3", number_theory("order_mod", numbers=["2", "7"])["order"], "3")
expect("fib(20) = 6765", number_theory("fibonacci", numbers=["20"])["fibonacci"], "6765")

try:
    number_theory("no_such_operation")
    expect("an unknown operation is rejected", "accepted", "rejected")
except ValueError:
    expect("an unknown operation is rejected", "rejected", "rejected")

print(f"\npassed {PASS}, failed {FAIL}")
sys.exit(1 if FAIL else 0)
