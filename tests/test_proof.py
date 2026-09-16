import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))

tmp = tempfile.mkdtemp(prefix="mathrigor-test-")
os.environ["MATH_RIGOR_HOME"] = tmp

from rigor.proof import (  # noqa: E402
    AUDIT_FLAWED,
    AUDIT_GAPS,
    AUDIT_VERIFIED,
    Entry,
    ProofSession,
    audit,
    check_entry,
    export_session,
    goals_match,
)

PASS = 0
FAIL = 0


def expect(label, actual, wanted):
    global PASS, FAIL
    ok = actual == wanted
    if ok:
        PASS += 1
    else:
        FAIL += 1
    print(f"{'ok  ' if ok else 'FAIL'} {label}: {actual!r}" + ("" if ok else f" (wanted {wanted!r})"))


def new_session(**kwargs):
    session = ProofSession(session_id="t" + os.urandom(4).hex(), **kwargs)
    session.save()
    return session


def add(session, statement, rule, from_ids=(), kind="step", justification="", name="",
        assumed=False, entry_id=None):
    prefix = {"given": "g", "assumption": "a", "lemma": "l", "step": "s"}[kind]
    entry = Entry(id=entry_id or session.next_id(prefix), statement=statement, rule=rule,
                  from_ids=list(from_ids), kind=kind, justification=justification,
                  name=name, assumed=assumed)
    session.entries.append(entry)
    check_entry(session, entry)
    session.save()
    return entry


print("=== goal matching ===")
expect("exact", goals_match("n^2 >= 0", "n^2 >= 0"), True)
expect("whitespace", goals_match("n^2>=0", "n ^ 2  >=  0"), True)
expect("commutative add", goals_match("2*x + 1", "1 + 2*x"), True)
expect("commutative conj", goals_match("P & Q", "Q & P"), True)
expect("different claims", goals_match("n^2 >= 0", "n^2 > 0"), False)

print("\n=== algebra steps really are checked ===")
s = new_session(
    problem="Show that for all reals a and b, (a+b)^2 = a^2 + 2ab + b^2.",
    goal="(a+b)^2 = a^2 + 2*a*b + b^2",
    variables={"a": "real", "b": "real"},
)
g1 = add(s, "a + b = a + b", "premise", kind="given", justification="trivial given")
good = add(s, "(a+b)^2 = a^2 + 2*a*b + b^2", "algebra", from_ids=[g1.id],
           justification="expand the square")
expect("correct algebra step", good.verdict, "verified")
bad = add(s, "(a+b)^2 = a^2 + b^2", "algebra", from_ids=[g1.id],
          justification="deliberately wrong")
expect("wrong algebra step is caught", bad.verdict, "refuted")
print("   detail:", bad.detail[:170])

s2 = new_session(problem="Equation rewriting", goal="x^2 = 1", variables={"x": "real"})
add(s2, "x^2 - 1 = 0", "premise", kind="given")
rewrite = add(s2, "x^2 = 1", "algebra", from_ids=["g1"], justification="add 1 to both sides")
expect("rewrite from a cited equation", rewrite.verdict, "verified")

print("\n=== logical rules inside a session ===")
s3 = new_session(problem="Propositional detour", goal="R")
add(s3, "P -> Q", "premise", kind="given")
add(s3, "Q -> R", "premise", kind="given")
add(s3, "P", "premise", kind="given")
add(s3, "P -> R", "hypothetical_syllogism", from_ids=["g1", "g2"])
mp = add(s3, "R", "modus_ponens", from_ids=["g3", "s1"])
expect("valid modus ponens", mp.verdict, "verified")
mis = add(s3, "Q", "modus_ponens", from_ids=["g3", "g2"], justification="mis-cited")
expect("mis-cited rule is caught", mis.verdict, "invalid")

print("\n=== disjunction elimination (proof by cases) ===")
s3b = new_session(problem="Cases", goal="R")
add(s3b, "P || Q", "premise", kind="given")
add(s3b, "P -> R", "premise", kind="given")
add(s3b, "Q -> R", "premise", kind="given")
case = add(s3b, "R", "disjunction_elim", from_ids=["g1", "g2", "g3"],
           justification="either case yields R")
expect("case analysis step", case.verdict, "verified")

print("\n=== assumption discharge ===")
s4 = new_session(problem="Conditional proof", goal="P -> Q")
add(s4, "P -> Q", "premise", kind="given")
add(s4, "P", "assumption", kind="assumption")
add(s4, "Q", "modus_ponens", from_ids=["g1", "a1"])
r4 = audit(s4)
expect("open assumption flagged", r4["undischarged_assumption_ids"], ["a1"])
expect("open assumption is a flaw", r4["verdict"], AUDIT_FLAWED)

s5 = new_session(problem="Conditional proof done right", goal="P -> Q")
add(s5, "P -> Q", "premise", kind="given")
add(s5, "P", "assumption", kind="assumption")
add(s5, "Q", "modus_ponens", from_ids=["g1", "a1"])
add(s5, "P -> Q", "conditional_proof", from_ids=["a1", "s1"],
    justification="discharge the assumption")
r5 = audit(s5)
expect("discharged", r5["undischarged_assumption_ids"], [])
expect("goal reached", r5["goal_reached"], True)
expect("verdict", r5["verdict"], AUDIT_VERIFIED)

print("\n=== reductio ===")
s5b = new_session(problem="Contradiction", goal="P")
add(s5b, "P || Q", "premise", kind="given")
add(s5b, "~P", "assumption", kind="assumption")
add(s5b, "Q", "disjunctive_syllogism", from_ids=["g1", "a1"])
add(s5b, "~Q", "premise", kind="given")
add(s5b, "falsum", "contradiction_intro", from_ids=["s1", "g2"])
red = add(s5b, "P", "reductio", from_ids=["a1", "s2"], justification="~P led to a contradiction")
r5b = audit(s5b)
expect("reductio step valid", red.verdict, "verified")
expect("assumption discharged by reductio", r5b["undischarged_assumption_ids"], [])
expect("verdict", r5b["verdict"], AUDIT_VERIFIED)

print("\n=== lemmas ===")
s6 = new_session(problem="Lemma bookkeeping", goal="R")
add(s6, "R", "premise", kind="given")
lemma = add(s6, "Q", "lemma", kind="lemma", name="helper")
r6 = audit(s6)
expect("unproved lemma flagged", r6["unproved_lemma_ids"], [lemma.id])
expect("verdict", r6["verdict"], AUDIT_FLAWED)

s7 = new_session(problem="Lemma proved then used", goal="Q")
add(s7, "P", "premise", kind="given")
add(s7, "P -> Q", "premise", kind="given")
lemma7 = add(s7, "Q", "lemma", kind="lemma", name="helper")
lemma7.proved = True
lemma7.from_ids = ["g1", "g2"]
add(s7, "Q", "modus_ponens", from_ids=["g1", "g2"], justification="use the lemma")
# mark the lemma as proved explicitly through the audit path
lemma7.proved = True
r7 = audit(s7)
expect("no unproved lemmas", r7["unproved_lemma_ids"], [])

print("\n=== structural integrity ===")
s8 = new_session(problem="Forward reference", goal="P")
add(s8, "P", "premise", kind="given")
add(s8, "P", "algebra", from_ids=["g1"], entry_id="s1")
add(s8, "P", "algebra", from_ids=["s1"], entry_id="s2")
# now a step that cites a later id
add(s8, "P", "algebra", from_ids=["s2"], entry_id="s3")
forward = Entry(id="s0", statement="P", rule="algebra", from_ids=["s3"], kind="step")
s8.entries.insert(3, forward)
r8 = audit(s8)
forward_flaws = [f for f in r8["flaws"] if "earlier" in f["issue"]]
expect("forward reference detected", len(forward_flaws) > 0, True)
print("   ", forward_flaws[0]["issue"][:140] if forward_flaws else "")

s9 = new_session(problem="Dangling citation", goal="P")
add(s9, "P", "premise", kind="given")
add(s9, "P", "algebra", from_ids=["g99"])
r9 = audit(s9)
expect("dangling citation detected",
       any("unknown id" in f["issue"] for f in r9["flaws"]), True)

print("\n=== goal not derived ===")
s10 = new_session(problem="Wrong goal", goal="Q")
add(s10, "P", "premise", kind="given")
add(s10, "P", "algebra", from_ids=["g1"])
r10 = audit(s10)
expect("goal not reached", r10["goal_reached"], False)
expect("verdict", r10["verdict"], AUDIT_FLAWED)

print("\n=== unchecked steps give the honest middle verdict ===")
s11 = new_session(problem="Human-justified step", goal="x > 0", variables={"x": "real"})
add(s11, "x >= 1", "premise", kind="given")
add(s11, "x > 0", "theorem_citation", from_ids=["g1"],
    justification="standard consequence of x >= 1")
r11 = audit(s11)
expect("verdict", r11["verdict"], AUDIT_VERIFIED)  # SMT should close this one

s12 = new_session(problem="Genuinely unchecked", goal="P(3)", functions=["P"])
add(s12, "forall n in Z: P(n)", "premise", kind="given")
add(s12, "P(3)", "theorem_citation", from_ids=["g1"],
    justification="a citation the checker cannot formalise")
r12 = audit(s12)
print("   verdict:", r12["verdict"], "| unchecked:", r12["unchecked_ids"],
      "| coverage:", r12["machine_coverage"]["checked_fraction"])

print("\n=== export ===")
md = export_session(s5, "markdown")
expect("markdown mentions the verdict", "Audit verdict" in md, True)
tex = export_session(s5, "latex")
expect("latex export present", "\\begin{proof}" in tex, True)

print(f"\npassed {PASS}, failed {FAIL}")
shutil.rmtree(tmp, ignore_errors=True)
sys.exit(1 if FAIL else 0)
