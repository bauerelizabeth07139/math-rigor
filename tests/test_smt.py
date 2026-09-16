import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))

from rigor.smt import check_entailment, check_valid, entails  # noqa: E402

CASES = [
    # (statement, expected status)
    ("forall n in Z: n^2 >= 0", "proven"),
    ("forall x in R: x^2 >= x", "refuted"),
    ("forall x in R: x^2 - 1 = (x-1)(x+1)", "proven"),
    ("forall x in R: x^2 + 1 > 0", "proven"),
    ("forall n in Z: 6 | n^3 - n", "proven"),
    ("forall n in Z: 3 | n^3 - n", "proven"),
    ("forall n in Z: n^3 - n = 6*k", "refuted"),          # k is free: false
    ("forall n in Z: exists k in Z: n^3 - n = 6*k", "proven"),
    ("forall n in N: n >= 0", "proven"),
    ("forall n in N: n > 0", "refuted"),                  # n = 0
    ("forall x in U: P(x) -> P(x)", "proven"),
    ("forall x in U: P(x) -> Q(x)", "refuted"),
    ("exists n in Z: n^2 = 4", "proven"),
    ("exists n in Z: n^2 = 5", "refuted"),
    ("forall x in Z: x > 0 -> x >= 1", "proven"),
    ("forall x in R: x > 0 -> x >= 1", "refuted"),
    ("forall a in R: forall b in R: (a+b)^2 = a^2 + 2*a*b + b^2", "proven"),
    ("forall n in Z: 30 | n^5 - n", "proven"),
    ("forall n in Z: (n^2 + n) % 2 = 0", "proven"),
    ("forall x in R: abs(x) >= x", "proven"),
    ("forall x in R: forall y in R: abs(x+y) <= abs(x) + abs(y)", "proven"),
    ("forall n in Z: n > 5 -> n >= 6", "proven"),
]

fails = 0
for stmt, expected in CASES:
    out = check_valid(stmt)
    ok = out.status == expected or (expected == "unknown-or-proven"
                                    and out.status in ("proven", "unknown"))
    if not ok:
        fails += 1
    flag = "ok  " if ok else "FAIL"
    print(f"{flag} [{out.status:8}] expect={expected:17} {stmt}")
    if out.model:
        print(f"       counterexample: {out.model}")
    if out.status == "unknown":
        print(f"       reason: {out.reason} | method: {out.method}")
    if not ok:
        print(f"       method={out.method} notes={out.notes}")

print("\n=== entailment ===")
e1 = entails(["P -> Q", "P"], "Q")
print("P->Q, P |= Q :", e1.status, "|", e1.method)
e2 = entails(["P -> Q", "Q"], "P")
print("P->Q, Q |= P :", e2.status, "| counterexample:", e2.model)
e3 = entails(["forall x in R: f(x) > 0"], "f(1) > 0", functions=["f"])
print("forall x: f(x)>0 |= f(1)>0 :", e3.status)
e4 = entails(["n > 2"], "n > 3", declared_sorts={"n": "int"})
print("n>2 |= n>3 :", e4.status, e4.model)
e5 = entails(["forall n in Z: 2 | n -> even(n)"], "forall n in Z: even(2*n)")
print("even(2n) :", e5.status)

print(f"\nfailures: {fails}")
sys.exit(1 if fails else 0)
