import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))

from rigor.logic import check_step, rules_catalogue, truth_table  # noqa: E402

CASES = [
    # (premises, conclusion, rule, expected verdict)
    (["P -> Q", "P"], "Q", "modus_ponens", "valid"),
    (["P -> Q", "P"], "P", "modus_ponens", "invalid_shape"),
    (["P -> Q", "Q"], "P", "modus_ponens", "invalid_shape"),
    (["P -> Q", "~Q"], "~P", "modus_tollens", "valid"),
    (["P -> Q", "~P"], "~Q", "modus_tollens", "invalid_shape"),
    (["P -> Q", "Q -> R"], "P -> R", "hypothetical_syllogism", "valid"),
    (["P | Q", "~P"], "Q", "disjunctive_syllogism", "valid"),
    (["P", "Q"], "P & Q", "conjunction_intro", "valid"),
    (["P", "Q"], "P", "conjunction_intro", "invalid_shape"),
    (["P & Q"], "P", "conjunction_elim", "valid"),
    (["P & Q"], "Q", "conjunction_elim", "valid"),
    (["P"], "P | Q", "disjunction_intro", "valid"),
    (["P | Q", "P -> R", "Q -> R"], "R", "disjunction_elim", "valid"),
    (["P | Q", "P -> R"], "R", "disjunction_elim", "invalid_shape"),
    (["P", "Q"], "P -> Q", "conditional_proof", "valid"),
    (["~P", "falsum"], "P", "reductio", "valid"),
    (["~P", "P"], "falsum", "contradiction_intro", "valid"),
    (["falsum"], "Q", "ex_falso", "valid"),
    (["P"], "Q", "ex_falso", "invalid_shape"),
    (["P -> Q", "Q -> P"], "P <-> Q", "biconditional_intro", "valid"),
    (["P <-> Q"], "P -> Q", "biconditional_elim", "valid"),
    (["~~P"], "P", "double_negation", "valid"),
    (["P -> Q"], "~Q -> ~P", "contraposition", "valid"),
    ([], "P | ~P", "excluded_middle", "valid"),
    (["forall x in R: x^2 >= 0"], "3^2 >= 0", "universal_instantiation", "valid"),
    (["forall x in R: x^2 >= 0"], "9 >= 0", "universal_instantiation", "invalid_shape"),
    (["forall n in Z: 6 | n^3 - n"], "6 | (2*k)^3 - 2*k", "universal_instantiation", "valid"),
    (["2 > 1"], "exists n in Z: n > 1", "existential_generalization", "valid"),
    (["a^2 >= 0"], "forall x in R: x^2 >= 0", "universal_generalization", "valid"),
    (["a^2 >= 0", "a > 1"], "forall x in R: x^2 >= 0", "universal_generalization", "invalid_shape"),
    (["exists n in Z: n^2 = 4"], "c^2 = 4", "existential_instantiation", "valid"),
    (["exists n in Z: n^2 = 4", "c > 0"], "c^2 = 4", "existential_instantiation", "invalid_shape"),
    (["x = y", "x + 1 > 0"], "y + 1 > 0", "equality_substitution", "valid"),
    (["n > 2"], "n > 3", "totally_made_up", "unsupported_rule"),
]

fails = 0
for premises, conclusion, rule, expected in CASES:
    out = check_step(premises, conclusion, rule)
    ok = out.verdict == expected
    if not ok:
        fails += 1
    print(f"{'ok  ' if ok else 'FAIL'} [{out.verdict:16}] {rule:26} "
          f"{' , '.join(premises) if premises else '(no premises)'} |= {conclusion}")
    if not ok:
        print(f"       expected={expected} shape={out.shape_detail} sem={out.semantic_status} {out.semantic_detail}")
    if out.witness:
        print(f"       bindings={out.witness}")

print("\n=== truth table ===")
tt = truth_table(["P -> Q", "P"], "Q")
print("P->Q, P |= Q :", tt["verdict"], "| rows:", tt["row_count"])
print("  entailed:", tt["entailed"])
tt2 = truth_table(["P -> Q", "Q"], "P")
print("P->Q, Q |= P :", tt2["verdict"])
print("  falsifying:", tt2["falsifying_assignments"])
tt3 = truth_table([], "P | ~P")
print("|= P|~P :", tt3["verdict"])
tt4 = truth_table([], "(P -> Q) <-> (~Q -> ~P)")
print("|= contraposition :", tt4["verdict"])
tt5 = truth_table(["~(P & Q)"], "~P | ~Q")
print("De Morgan :", tt5["verdict"])

print("\n=== catalogue ===")
cat = rules_catalogue()
print("logical rules:", len(cat["logical_rules"]))
print("non-logical:", list(cat["non_logical_justifications"]))
print(f"\nfailures: {fails}")
sys.exit(1 if fails else 0)
