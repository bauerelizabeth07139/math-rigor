"""math-rigor: an MCP server for rigorous, auditable mathematical work.

The server exposes three layers that a disciplined proof workflow needs:

* **proof sessions** -- a citable step DAG with an audit that separates
  ``verified`` from ``sound_with_gaps`` from ``flawed``;
* **logic** -- inference-rule checking that validates both the shape of a rule
  application and its semantics, plus exhaustive propositional truth tables;
* **mathematics** -- symbolic computation, SMT-backed verification of quantified
  claims and inequalities, induction checking, counterexample search, and exact
  number theory.

Every tool returns a JSON object.  Verdicts are never upgraded: a statement is
``proven`` only when its negation is unsatisfiable, ``refuted`` only when a
concrete counterexample is exhibited, and otherwise ``inconclusive``.
"""

from __future__ import annotations

import functools
import json
import traceback
import uuid
from typing import Any

from mcp.server.mcpserver import MCPServer

from rigor.logic import check_step as core_check_step
from rigor.logic import rules_catalogue
from rigor.logic import truth_table as core_truth_table
from rigor.proof import (
    STAGES,
    Entry,
    ProofSession,
    audit,
    check_entry,
    export_session,
    sessions_directory,
)
from rigor.smt import check_entailment as core_check_entailment
from rigor.symbolic import numeric_eval as core_numeric_eval
from rigor.symbolic import symbolic_eval as core_symbolic_eval
from rigor.symbolic import verify_identity as core_verify_identity
from rigor.verify import find_counterexample as core_find_counterexample
from rigor.verify import number_theory as core_number_theory
from rigor.verify import verify_forall as core_verify_forall
from rigor.verify import verify_induction as core_verify_induction
from rigor.verify import verify_inequality as core_verify_inequality
from rigor.verify import verify_limit as core_verify_limit

SERVER_INSTRUCTIONS = """\
This server supports a staged, auditable mathematical workflow.

1. Formalise before computing: fix the domain of every symbol, write the goal with
   all quantifiers explicit, and list the givens separately. Register the work with
   proof_start so the argument becomes inspectable data.
2. Attack each claim with the right instrument:
   - verify_identity for "these two expressions are the same function";
   - verify_inequality for "this holds for every value in this domain";
   - verify_forall for any quantified statement, including divisibility and parity;
   - verify_induction for induction, which checks the base case and P(k)->P(k+1);
   - symbolic_eval for differentiating, integrating, solving, and closing sums;
   - number_theory for exact integer facts;
   - find_counterexample when you suspect a claim is false.
3. Record each proof step with proof_add_step, citing exactly one rule and the ids
   it uses. Logical rules (see logic_rules) are checked for shape and for semantics;
   algebra and arithmetic steps are re-derived from the premises they cite.
4. Run proof_validate before believing anything. It reports refuted steps with
   counterexamples, invalid rule citations, undischarged assumptions, unproved
   lemmas, and how much of the argument was actually machine-checked.
5. Report honestly: distinguish what was machine-verified from what rests on human
   justification. The audit's `sound_with_gaps` verdict exists precisely so that
   this distinction is never blurred.

Verdict vocabulary: `proven` (negation shown unsatisfiable), `refuted` (a concrete
counterexample was found), `inconclusive` (the solver could not decide and no
counterexample was found -- this is NOT a proof).

Notation: `&` or `and` for conjunction; `||`, `or`, or a lone `|` between
propositions for disjunction; `~` or `not` for negation; `->` for implication;
`<->` for biconditional; `forall x in Z: ...` and `exists x in R: ...` for
quantifiers. A single `|` between numeric operands means divisibility (`6 | n`);
write `divides(6, n)` or `n % 6 = 0` to be explicit. Juxtaposition multiplies
(`n(n+1)`), so declare custom function names in the `functions` argument when you
need `f(x+1)` to be read as an application rather than a product.
"""

server = MCPServer(
    name="math-rigor",
    title="Math Rigor",
    version="1.0.0",
    instructions=SERVER_INSTRUCTIONS,
)


# --------------------------------------------------------------------------- #
# Return helpers
# --------------------------------------------------------------------------- #


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def _guard(function: Any, *args: Any, **kwargs: Any) -> str:
    """Run a tool body, turning any failure into a structured JSON error.

    A body that already returns a JSON string is passed through untouched, so the
    ``guarded`` decorator can be applied uniformly without double-encoding.
    """
    try:
        result = function(*args, **kwargs)
        return result if isinstance(result, str) else _json(result)
    except Exception as exc:  # noqa: BLE001 - the client must see a readable reason
        return _json({
            "error": True,
            "exception": type(exc).__name__,
            "message": str(exc),
            "hint": (
                "check the notation: a lone `|` is disjunction between propositions "
                "and divisibility between numbers; use `||` or `or` for disjunction "
                "and `divides(a, b)` or `a % b = 0` for divisibility when in doubt"
            ),
            "traceback_tail": traceback.format_exc().strip().splitlines()[-3:],
        })


def guarded(function: Any) -> Any:
    """Ensure no tool can ever leak a raw exception to the client."""
    @functools.wraps(function)
    def wrapper(*args: Any, **kwargs: Any) -> str:
        return _guard(function, *args, **kwargs)

    return wrapper


def _sorts(mapping: dict[str, str] | None) -> dict[str, str]:
    return {str(k): str(v) for k, v in (mapping or {}).items()}


def _names(items: list[str] | None) -> list[str]:
    return [str(item) for item in (items or [])]


# --------------------------------------------------------------------------- #
# Workflow guidance
# --------------------------------------------------------------------------- #


@server.tool(description=(
    "Return the mandated proof workflow stages and the checks the audit enforces. Call this "
    "once at the start of a mathematical task to align with the process."
))
@guarded
def proof_workflow() -> str:
    catalogue = rules_catalogue()
    return _json({
        "stages": [{"stage": name, "instruction": text} for name, text in STAGES],
        "verdict_vocabulary": {
            "proven": "the negation is unsatisfiable",
            "refuted": "a concrete counterexample was exhibited",
            "inconclusive": "undecided by the solver and no counterexample found; not a proof",
            "verified": "every step machine-checked and the goal derived",
            "sound_with_gaps": "structurally sound, nothing refuted, but some steps are unchecked",
            "flawed": "something is refuted, invalid, unproved, or the goal is not derived",
        },
        "logical_rules": sorted(catalogue["logical_rules"]),
        "non_logical_justifications": sorted(catalogue["non_logical_justifications"]),
        "enforced_checks": [
            "every cited id must exist and must come earlier in the proof",
            "logical steps must match their rule's shape and be semantically sound",
            "algebra and arithmetic steps must follow from the premises they cite",
            "every temporary assumption must be discharged by a discharging rule",
            "every lemma must be proved or explicitly disclosed as assumed",
            "the last step (or the step named by goal_step) must match the declared goal",
        ],
    })


@server.tool(description=(
    "List every inference rule the audit accepts, with descriptions and examples. Optionally "
    "filter by category: propositional, predicate, or equality."
))
@guarded
def logic_rules(category: str = "") -> str:
    catalogue = rules_catalogue()
    if category:
        wanted = category.strip().lower()
        catalogue["logical_rules"] = {
            name: rule for name, rule in catalogue["logical_rules"].items()
            if rule["category"] == wanted
        }
        catalogue["filtered_by"] = wanted
        if not catalogue["logical_rules"]:
            catalogue["note"] = (
                f"no rules in category {wanted!r}; categories are propositional, "
                "predicate, equality"
            )
    return _json(catalogue)


# --------------------------------------------------------------------------- #
# Proof sessions
# --------------------------------------------------------------------------- #


@server.tool(description=(
    "Open a proof session, the record of one mathematical argument. Fix the problem, the exact "
    "goal, and the domain of every symbol here. Returns the stage checklist to follow."
))
@guarded
def proof_start(
    problem: str,
    goal: str,
    kind: str = "prove",
    variables: dict[str, str] | None = None,
    functions: list[str] | None = None,
    strategy: str = "",
) -> str:
    session = ProofSession(
        session_id="ps_" + uuid.uuid4().hex[:12],
        problem=problem,
        goal=goal,
        kind=kind if kind in ("prove", "disprove", "decide", "compute") else "prove",
        strategy=strategy,
        variables=_sorts(variables),
        functions=_names(functions),
    )
    session.save()
    return _json({
        "session_id": session.session_id,
        "problem": session.problem,
        "goal": session.goal,
        "kind": session.kind,
        "variables": session.variables,
        "functions": session.functions,
        "stored_at": str(session.path),
        "next_stage": STAGES[1][0],
        "guidance": STAGES[1][1],
        "how_to_record": {
            "givens": "proof_add_given(session_id, statement) for each hypothesis from the problem",
            "assumptions": "proof_add_assumption(session_id, statement) for temporary assumptions",
            "lemmas": "proof_add_lemma(session_id, name, statement) before proving them",
            "steps": "proof_add_step(session_id, statement, rule, from_ids, justification)",
        },
    })


@server.tool(description=(
    "Register a hypothesis taken from the problem statement. Givens are not proved; the audit "
    "tracks that they remain hypotheses of the argument."
))
@guarded
def proof_add_given(session_id: str, statement: str, note: str = "") -> str:
    session = ProofSession.load(session_id)
    entry = Entry(id=session.next_id("g"), statement=statement, kind="given", note=note,
                  rule="premise")
    check_entry(session, entry)
    session.entries.append(entry)
    session.save()
    return _json({"added": entry.to_dict(), "total_entries": len(session.entries)})


@server.tool(description=(
    "Register a temporary assumption, such as the antecedent of a conditional proof or the "
    "negation assumed in a proof by contradiction. Every assumption must later be discharged by "
    "conditional_proof, reductio, disjunction_elim, existential_instantiation, or case_analysis."
))
@guarded
def proof_add_assumption(session_id: str, statement: str, note: str = "") -> str:
    session = ProofSession.load(session_id)
    entry = Entry(id=session.next_id("a"), statement=statement, kind="assumption", note=note,
                  rule="assumption")
    check_entry(session, entry)
    session.entries.append(entry)
    session.save()
    return _json({
        "added": entry.to_dict(),
        "discharge_required": (
            "cite this id from a conditional_proof, reductio, disjunction_elim, "
            "existential_instantiation, or case_analysis step"
        ),
    })


@server.tool(description=(
    "Register a lemma the plan needs. A lemma is an obligation: prove it later and pass its id "
    "as proves_lemma to proof_add_step, or declare assumed=true and disclose it as an "
    "assumption of the argument."
))
@guarded
def proof_add_lemma(session_id: str, name: str, statement: str, note: str = "",
                    assumed: bool = False) -> str:
    session = ProofSession.load(session_id)
    entry = Entry(id=session.next_id("l"), statement=statement, kind="lemma", name=name,
                  note=note, assumed=assumed, rule="lemma")
    check_entry(session, entry)
    session.entries.append(entry)
    session.save()
    return _json({
        "added": entry.to_dict(),
        "status": ("declared as an assumption of the argument, which the audit will report"
                   if assumed else
                   "outstanding obligation: prove it, then pass its id as proves_lemma"),
    })


@server.tool(description=(
    "Add a proof step. It is machine-checked immediately. Cite exactly one rule and the ids it "
    "uses. Logical rules are checked for shape and semantics; algebra and arithmetic steps must "
    "follow from the premises they cite. A refuted step comes back with a counterexample."
))
@guarded
def proof_add_step(
    session_id: str,
    statement: str,
    rule: str,
    from_ids: list[str] | None = None,
    justification: str = "",
    proves_lemma: str = "",
) -> str:
    session = ProofSession.load(session_id)
    entry = Entry(id=session.next_id("s"), statement=statement, rule=rule,
                  from_ids=_names(from_ids), kind="step", justification=justification)
    check_entry(session, entry)
    session.entries.append(entry)
    if proves_lemma:
        lemma = session.by_id(proves_lemma)
        if lemma is not None and lemma.kind == "lemma":
            lemma.proved = entry.verdict in ("verified", "unchecked")
            lemma.from_ids = entry.from_ids
            if not lemma.proved:
                lemma.note = (lemma.note + " the proof attempt was refuted").strip()
            else:
                lemma.rule = entry.rule
    session.save()
    return _json({
        "step": entry.to_dict(),
        "verdict": entry.verdict,
        "detail": entry.detail,
        "next": (
            "this step is machine-verified; continue"
            if entry.verdict == "verified" else
            "this step is invalid -- fix the cited rule or the statement"
            if entry.verdict == "invalid" else
            "this step is refuted -- the counterexample above disproves it as stated"
            if entry.verdict == "refuted" else
            "this step is unchecked: it rests on human justification. Either cite the "
            "premises the rule needs so it can be checked, or disclose it in the write-up"
        ),
    })


@server.tool(description=(
    "Audit the whole proof: re-check every step, verify the citation graph is well-founded, "
    "confirm every assumption is discharged and every lemma proved, check that the goal is "
    "actually derived, and report how much was machine-checked."
))
@guarded
def proof_validate(session_id: str, goal_step: str = "") -> str:
    session = ProofSession.load(session_id)
    if goal_step:
        session.goal_id = goal_step
    result = audit(session)
    session.audit = result
    session.save()
    return _json(result)


@server.tool(description=(
    "Show a proof session: its statements, verdicts, and structure. Pass an empty session_id to "
    "list every stored session."
))
@guarded
def proof_status(session_id: str = "") -> str:
    if not session_id:
        listing = []
        for path in sorted(sessions_directory().glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            listing.append({
                "session_id": raw.get("session_id"),
                "goal": raw.get("goal"),
                "entries": len(raw.get("entries", [])),
                "last_verdict": (raw.get("audit") or {}).get("verdict"),
            })
        return _json({"sessions": listing, "directory": str(sessions_directory())})
    session = ProofSession.load(session_id)
    return _json({
        "session_id": session.session_id,
        "problem": session.problem,
        "goal": session.goal,
        "strategy": session.strategy,
        "variables": session.variables,
        "entries": [entry.to_dict() for entry in session.entries],
        "last_audit": session.audit,
    })


@server.tool(description=(
    "Export the proof as markdown (default), LaTeX, or raw JSON, including every step's "
    "justification and the audit verdict."
))
@guarded
def proof_export(session_id: str, format: str = "markdown") -> str:
    session = ProofSession.load(session_id)
    return export_session(session, format)


# --------------------------------------------------------------------------- #
# Logic
# --------------------------------------------------------------------------- #


@server.tool(description=(
    "Check one inference step: do the premises have the shape the named rule requires, and do "
    "they semantically entail the conclusion? Both must hold. Use logic_rules for the "
    "vocabulary. Returns the matched bindings, or the precise reason the citation fails."
))
@guarded
def logic_check_step(
    premises: list[str] | None = None,
    conclusion: str = "",
    rule: str = "",
    functions: list[str] | None = None,
    timeout_ms: int = 15000,
) -> str:
    outcome = core_check_step(_names(premises), conclusion, rule,
                              functions=_names(functions), timeout_ms=timeout_ms)
    return _json(outcome.to_dict())


@server.tool(description=(
    "Decide whether premises entail a conclusion, over propositional logic, predicates, "
    "quantifiers, or arithmetic. Reports proven with the method, or refuted with a concrete "
    "counterexample assignment."
))
@guarded
def logic_entails(
    premises: list[str] | None = None,
    conclusion: str = "",
    variables: dict[str, str] | None = None,
    functions: list[str] | None = None,
    timeout_ms: int = 20000,
) -> str:
    outcome = core_check_entailment(_names(premises), conclusion,
                                   declared_sorts=_sorts(variables),
                                   functions=_names(functions), timeout_ms=timeout_ms)
    return _json(outcome.to_dict())


@server.tool(description=(
    "Exhaustively enumerate a purely propositional statement: every assignment, whether it is a "
    "tautology, and for entailment the falsifying rows. Exact and complete, limited to at most "
    "10 variables."
))
def logic_truth_table(premises: list[str] | None = None, conclusion: str = "") -> str:
    return _guard(core_truth_table, _names(premises), conclusion or None)


# --------------------------------------------------------------------------- #
# Symbolic computation
# --------------------------------------------------------------------------- #


@server.tool(description=(
    "One symbolic operation on an expression: simplify, expand, factor, collect, cancel, apart, "
    "together, trigsimp, ratsimp, diff, integrate, limit, series, solve, roots, sum, product, "
    "coeffs, subs, numeric, latex, domain. Accepts plain, LaTeX, or sympy-style input. Supply "
    "symbol assumptions such as `x: positive` when the result depends on them."
))
def symbolic_eval(
    expression: str,
    operation: str = "simplify",
    variable: str = "",
    point: str = "",
    direction: str = "+",
    order: int = 1,
    lower: str = "",
    upper: str = "",
    substitutions: dict[str, str] | None = None,
    precision: int = 30,
    assumptions: list[str] | None = None,
) -> str:
    return _guard(core_symbolic_eval, expression, operation, variable, point=point,
                  direction=direction, order=order, lower=lower, upper=upper,
                  substitutions=substitutions, precision=precision,
                  assumptions=_names(assumptions))


@server.tool(description=(
    "Evaluate an expression to high precision at given values, reporting whether the result is "
    "an exact integer or rational and the exact fraction when it is. Use it to check a numerical "
    "claim that would otherwise rest on a calculator."
))
def symbolic_numeric(
    expression: str,
    substitutions: dict[str, str] | None = None,
    precision: int = 30,
) -> str:
    return _guard(core_numeric_eval, expression, substitutions, precision=precision)


@server.tool(description=(
    "Rewrite an expression into a chosen dialect and confirm it parses: `latex` to typeset it, "
    "`text` for unambiguous plain form, `python` for sympy-style. Use it to standardise notation "
    "before quoting an expression in a proof."
))
@guarded
def expr_normalise(expression: str, to: str = "latex") -> str:
    import sympy as sp

    from rigor.ast_nodes import collect_symbols, parse
    from rigor.translate import SympyContext, ast_to_sympy

    node = parse(expression)
    style = (to or "latex").strip().lower()
    out: dict[str, Any] = {
        "input": expression,
        "parsed": node.text(),
        "latex": node.latex(),
        "text": node.text(),
        "dialect": style,
        "rendered": node.latex() if style in ("latex", "tex") else node.text(),
        "free_symbols": sorted(collect_symbols(node)),
    }
    try:
        out["sympy"] = sp.sstr(ast_to_sympy(node, SympyContext()))
    except Exception as exc:  # noqa: BLE001
        out["sympy"] = None
        out["sympy_note"] = f"not translatable to sympy: {type(exc).__name__}: {exc}"
    return _json(out)


# --------------------------------------------------------------------------- #
# Verification
# --------------------------------------------------------------------------- #


@server.tool(description=(
    "Decide whether two expressions are the same function. `proven` when the difference "
    "collapses to zero under the stated assumptions; `refuted` with a numerical witness when "
    "they genuinely differ; `inconclusive` with the residual difference otherwise."
))
def verify_identity(
    lhs: str,
    rhs: str,
    variables: list[str] | None = None,
    assumptions: list[str] | None = None,
    precision: int = 30,
) -> str:
    return _guard(core_verify_identity, lhs, rhs, variables=_names(variables),
                  assumptions=_names(assumptions), precision=precision)


@server.tool(description=(
    "Decide a global inequality such as `x + 1/x >= 2` for x > 0. Provable by SMT, refutable "
    "with a counterexample, and sampled as a fallback. Always state the domain: the domain is "
    "part of the claim."
))
def verify_inequality(
    lhs: str,
    relation: str,
    rhs: str,
    variables: dict[str, str] | None = None,
    constraints: list[str] | None = None,
    functions: list[str] | None = None,
    timeout_ms: int = 20000,
) -> str:
    return _guard(core_verify_inequality, lhs, relation, rhs,
                  variables=_sorts(variables), constraints=_names(constraints),
                  functions=_names(functions), timeout_ms=timeout_ms)


@server.tool(description=(
    "Prove or refute a universally quantified statement over stated domains: divisibility, "
    "parity, bounds, algebraic identities, quantified logic. Returns `proven`, or `refuted` with "
    "a concrete counterexample, or `inconclusive` with the reason."
))
def verify_forall(
    statement: str,
    variables: dict[str, str] | None = None,
    functions: list[str] | None = None,
    timeout_ms: int = 20000,
    samples: int = 200,
) -> str:
    return _guard(core_verify_forall, statement, variables=_sorts(variables),
                  functions=_names(functions), timeout_ms=timeout_ms, samples=samples)


@server.tool(description=(
    "Check a proof by induction: verifies the base case P(start), then the inductive step as the "
    "quantified implication `k >= start & P(k) -> P(k+1)`. Symbolic sums are closed first when "
    "sympy can. A failing step returns the k that breaks it."
))
def verify_induction(
    predicate: str,
    variable: str = "n",
    start: str = "0",
    functions: list[str] | None = None,
    timeout_ms: int = 20000,
) -> str:
    return _guard(core_verify_induction, predicate, variable, start,
                  functions=_names(functions), timeout_ms=timeout_ms)


@server.tool(description=(
    "Check that a limit equals a claimed value, symbolically and numerically. Use `oo` for an "
    "infinite point and direction `+` or `-` for one-sided limits."
))
def verify_limit(
    expression: str,
    variable: str,
    point: str,
    target: str,
    direction: str = "+",
    assumptions: list[str] | None = None,
) -> str:
    return _guard(core_verify_limit, expression, variable, point, target,
                  direction=direction, assumptions=_names(assumptions))


@server.tool(description=(
    "Search for an assignment that makes a claim false. Asks the SMT solver first (an "
    "unsatisfiable negation proves that no counterexample exists), then scans explicit ranges, "
    "then samples. Use it to test a conjecture before investing in a proof."
))
def find_counterexample(
    claim: str,
    variables: dict[str, str] | None = None,
    ranges: dict[str, list[int]] | None = None,
    bound: int = 12,
    samples: int = 400,
    functions: list[str] | None = None,
    timeout_ms: int = 20000,
) -> str:
    return _guard(core_find_counterexample, claim, variables=_sorts(variables),
                  ranges={str(k): list(v) for k, v in (ranges or {}).items()},
                  bound=bound, samples=samples, functions=_names(functions),
                  timeout_ms=timeout_ms)


# --------------------------------------------------------------------------- #
# Number theory
# --------------------------------------------------------------------------- #


@server.tool(description=(
    "Exact integer computations: is_prime, factorize, divisors, divisor_count, divisor_sum, "
    "totient, mobius, gcd, lcm, is_coprime, bezout (Bezout coefficients), mod, mod_inverse, crt, "
    "valuation, is_perfect_square, is_perfect_power, primitive_root, legendre, jacobi, "
    "fibonacci, lucas, binomial, factorial, radical, carmichael, order_mod."
))
def number_theory(
    operation: str,
    numbers: list[str] | None = None,
    residues: list[str] | None = None,
    moduli: list[str] | None = None,
) -> str:
    return _guard(core_number_theory, operation, numbers=_names(numbers),
                  residues=_names(residues), moduli=_names(moduli))


def main() -> None:
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
