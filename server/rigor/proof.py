"""Proof sessions: a citable step DAG plus an audit that refuses to overstate.

A *proof session* turns a proof into data.  Every statement gets an id, every
step must cite a rule and the ids it uses, and the audit then answers three
separate questions:

* **structure** -- do the citations exist, are they acyclic, is the goal
  actually derived, was every temporary assumption discharged?
* **soundness** -- is each logical step a faithful instance of its cited rule,
  and does each algebraic step really follow from what it cites?
* **coverage** -- how much of the argument was machine-checked at all?

The final verdict distinguishes ``verified`` (everything machine-checked),
``sound_with_gaps`` (structurally valid, nothing refuted, but some steps rest on
human justification) and ``flawed`` (something is wrong or missing).  Collapsing
those three into a single "ok" would be exactly the dishonesty this toolkit
exists to prevent.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .ast_nodes import Node, collect_symbols, parse
from .logic import (
    NON_LOGICAL_RULES,
    RULES,
    VERDICT_SEMANTIC,
    VERDICT_SHAPE,
    VERDICT_UNSUPPORTED,
    check_step,
)
from .smt import STATUS_PROVEN, STATUS_REFUTED, STATUS_UNKNOWN, check_entailment

STEP_VERIFIED = "verified"
STEP_REFUTED = "refuted"
STEP_UNCHECKED = "unchecked"
STEP_INVALID = "invalid"
STEP_ASSUMED = "assumed"

AUDIT_VERIFIED = "verified"
AUDIT_GAPS = "sound_with_gaps"
AUDIT_FLAWED = "flawed"

# Rules whose conclusion must follow from the cited premises; a counterexample
# here is a genuine error rather than a mere absence of machine support.
ENTAILMENT_REQUIRED = {"algebra", "arithmetic", "computation"}

# Rules that discharge a temporary assumption when they cite it.
DISCHARGING_RULES = {
    "conditional_proof", "reductio", "disjunction_elim",
    "existential_instantiation", "case_analysis", "contradiction",
}

STAGES = [
    ("formalise", "Restate the problem, fix the domain of every symbol, and write the "
                  "exact goal with all quantifiers explicit. Record givens separately."),
    ("plan", "Choose a strategy and name it: direct, contrapositive, contradiction, "
             "induction, construction, cases, invariant, extremal, or counting. "
             "Register every lemma the plan needs before proving anything."),
    ("decompose", "Split the goal into obligations (lemmas) with their own statements. "
                  "Each lemma is proved and audited separately."),
    ("prove", "Add steps one at a time. Each step states one claim, cites exactly one "
              "rule, and cites the ids it uses. Machine-checkable steps are checked on "
              "the spot; a refuted step must be fixed or abandoned."),
    ("audit", "Run proof_validate and read the verdict. Resolve every refuted or "
              "invalid step, discharge every assumption, and either prove or explicitly "
              "disclose every unchecked step."),
    ("report", "Export the proof with proof_export. State what was machine-verified and "
               "what rests on human justification."),
]


def home_directory() -> Path:
    base = os.environ.get("MATH_RIGOR_HOME")
    if base:
        return Path(base)
    return Path.home() / ".zcode" / "math-rigor-mcp"


def sessions_directory() -> Path:
    path = home_directory() / "sessions"
    path.mkdir(parents=True, exist_ok=True)
    return path


# --------------------------------------------------------------------------- #
# Session model
# --------------------------------------------------------------------------- #


@dataclass
class Entry:
    id: str
    statement: str
    rule: str = ""
    from_ids: list[str] = field(default_factory=list)
    justification: str = ""
    kind: str = "step"          # given | assumption | lemma | step
    verdict: str = STEP_UNCHECKED
    detail: str = ""
    machine_checked: bool = False
    note: str = ""
    name: str = ""              # lemmas
    proved: bool = False        # lemmas
    assumed: bool = False       # lemmas declared without proof
    added_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProofSession:
    session_id: str
    problem: str
    goal: str
    kind: str = "prove"
    strategy: str = ""
    variables: dict[str, str] = field(default_factory=dict)
    functions: list[str] = field(default_factory=list)
    entries: list[Entry] = field(default_factory=list)
    goal_id: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    audit: dict[str, Any] = field(default_factory=dict)

    # -- persistence ------------------------------------------------------- #
    @property
    def path(self) -> Path:
        return sessions_directory() / f"{self.session_id}.json"

    def save(self) -> None:
        self.updated_at = time.time()
        payload = asdict(self)
        payload["entries"] = [e.to_dict() for e in self.entries]
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                             encoding="utf-8")

    @classmethod
    def load(cls, session_id: str) -> "ProofSession":
        path = sessions_directory() / f"{session_id}.json"
        if not path.exists():
            available = [p.stem for p in sorted(sessions_directory().glob("*.json"))]
            raise KeyError(
                f"no proof session {session_id!r}; known sessions: "
                f"{', '.join(available) if available else '(none)'}"
            )
        raw = json.loads(path.read_text(encoding="utf-8"))
        entries = [Entry(**item) for item in raw.pop("entries", [])]
        session = cls(**raw)
        session.entries = entries
        return session

    # -- lookups ----------------------------------------------------------- #
    def by_id(self, entry_id: str) -> Entry | None:
        for entry in self.entries:
            if entry.id == entry_id:
                return entry
        return None

    def ids(self) -> list[str]:
        return [e.id for e in self.entries]

    def next_id(self, prefix: str) -> str:
        used = {e.id for e in self.entries}
        index = 1
        while f"{prefix}{index}" in used:
            index += 1
        return f"{prefix}{index}"

    def statements(self, ids: Sequence[str]) -> list[str]:
        out: list[str] = []
        for entry_id in ids:
            entry = self.by_id(entry_id)
            if entry is None:
                raise KeyError(f"unknown id {entry_id!r}")
            out.append(entry.statement)
        return out


# --------------------------------------------------------------------------- #
# Normalisation used for goal comparison
# --------------------------------------------------------------------------- #


def _sort_commutative(node: Node) -> Node:
    """Order the operands of commutative connectives so equal claims compare equal."""
    from .ast_nodes import And as _And, Bin as _Bin, Or as _Or, children_of as _kids

    children = [_sort_commutative(child) for child in _kids(node)]
    if not children:
        return node
    from .smt import _rebuild

    rebuilt = _rebuild(node, children)
    if isinstance(rebuilt, (_And, _Or)):
        return type(rebuilt)(tuple(sorted(rebuilt.parts, key=lambda p: p.text())))
    if isinstance(rebuilt, _Bin) and rebuilt.op in ("add", "mul"):
        operands = []
        stack = [rebuilt]
        while stack:
            current = stack.pop()
            if isinstance(current, _Bin) and current.op == rebuilt.op:
                stack.extend([current.right, current.left])
            else:
                operands.append(current)
        operands.sort(key=lambda p: p.text())
        folded = operands[0]
        for operand in operands[1:]:
            folded = _Bin(rebuilt.op, folded, operand)
        return folded
    return rebuilt


def canonical(text: str, functions: Sequence[str] = ()) -> str:
    """A comparable form of a statement: parsed, re-printed, commutativity-aware."""
    stripped = str(text).strip()
    try:
        node = _sort_commutative(parse(stripped, functions))
        return re.sub(r"\s+", "", node.text())
    except Exception:  # noqa: BLE001
        return re.sub(r"\s+", "", stripped).lower()


def goals_match(goal: str, statement: str, functions: Sequence[str] = ()) -> bool:
    return canonical(goal, functions) == canonical(statement, functions)


# --------------------------------------------------------------------------- #
# Step checking
# --------------------------------------------------------------------------- #


def check_entry(session: ProofSession, entry: Entry) -> Entry:
    """Fill in the machine verdict for one step.

    Any failure while checking is recorded on the step rather than raised, so a
    malformed statement stays visible in the proof and in the audit instead of
    silently vanishing.
    """
    rule = (entry.rule or "").strip().lower().replace("-", "_").replace(" ", "_")
    if entry.kind in ("given", "assumption"):
        entry.verdict = STEP_ASSUMED
        entry.machine_checked = False
        entry.detail = (
            "taken as a hypothesis; it is not proved here and the audit tracks whether "
            "it was discharged"
        )
        return entry

    try:
        return _check_entry_inner(session, entry, rule)
    except Exception as exc:  # noqa: BLE001 - report, never crash the session
        entry.verdict = STEP_INVALID
        entry.machine_checked = False
        entry.detail = (
            f"the step could not be checked: {type(exc).__name__}: {exc}. "
            "The statement is probably not a well-formed formula, or a cited premise is not one."
        )
        return entry


def _check_entry_inner(session: ProofSession, entry: Entry, rule: str) -> Entry:
    try:
        premises = session.statements(entry.from_ids)
    except KeyError as exc:
        entry.verdict = STEP_INVALID
        entry.machine_checked = False
        entry.detail = str(exc)
        return entry

    if rule in RULES:
        outcome = check_step(premises, entry.statement, rule,
                            functions=session.functions)
        if outcome.verdict == "valid":
            entry.verdict = STEP_VERIFIED
            entry.machine_checked = True
            entry.detail = f"{outcome.shape_detail}; {outcome.semantic_detail or ''}".strip("; ")
        elif outcome.verdict in (VERDICT_SHAPE, VERDICT_UNSUPPORTED):
            entry.verdict = STEP_INVALID
            entry.machine_checked = True
            entry.detail = outcome.shape_detail
        elif outcome.verdict == VERDICT_SEMANTIC:
            entry.verdict = STEP_REFUTED
            entry.machine_checked = True
            entry.detail = outcome.semantic_detail or "the cited premises do not entail the conclusion"
        else:
            entry.verdict = STEP_UNCHECKED
            entry.machine_checked = False
            entry.detail = outcome.semantic_detail or "the checker could not decide this step"
        return entry

    if rule not in NON_LOGICAL_RULES:
        entry.verdict = STEP_INVALID
        entry.machine_checked = False
        entry.detail = (
            f"unknown rule {entry.rule!r}. Logical rules: {', '.join(sorted(RULES))}. "
            f"Non-logical justifications: {', '.join(sorted(NON_LOGICAL_RULES))}."
        )
        return entry

    # Non-logical justification: try to upgrade it to a machine check, and for
    # rules that genuinely assert entailment treat a counterexample as an error.
    if premises:
        outcome = check_entailment(premises, entry.statement, declared_sorts=session.variables,
                                   functions=session.functions)
        if outcome.status == STATUS_PROVEN:
            entry.verdict = STEP_VERIFIED
            entry.machine_checked = True
            entry.detail = (
                f"the cited premises were machine-verified to entail this statement "
                f"({outcome.method})"
            )
            return entry
        if outcome.status == STATUS_REFUTED and rule in ENTAILMENT_REQUIRED:
            entry.verdict = STEP_REFUTED
            entry.machine_checked = True
            entry.detail = (
                f"this {rule} step does not follow from the cited premises; "
                f"counterexample: {outcome.model}. Either cite the premises the "
                "rewrite actually uses, or fix the step."
            )
            return entry
        if outcome.status == STATUS_REFUTED:
            entry.detail = (
                "the cited premises alone do not entail this statement, which is "
                f"expected for a {rule} step; the justification text is what must be reviewed"
            )
        else:
            entry.detail = f"the entailment check was inconclusive ({outcome.reason or outcome.method})"
    entry.verdict = STEP_UNCHECKED
    entry.machine_checked = False
    return entry


# --------------------------------------------------------------------------- #
# The audit
# --------------------------------------------------------------------------- #


# Rules that legitimately cite nothing: they either restate something from the
# problem, invoke a tool, or are axioms/tautologies.
PREMISELESS_OK = {
    "excluded_middle", "premise", "assumption", "construction", "definition",
    "arithmetic", "computation", "lemma", "theorem_citation", "induction_base",
    "induction_step", "machine_verified", "smt", "symbolic_computation",
    "counterexample_search", "verify_inequality",
}


def audit(session: ProofSession) -> dict[str, Any]:
    """Validate structure, re-check every step, and account for what is unproved."""
    order = session.ids()
    position = {entry_id: index for index, entry_id in enumerate(order)}
    flaws: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    for entry in session.entries:
        # -- structural checks -------------------------------------------- #
        if entry.kind in ("given", "assumption"):
            continue
        rule = (entry.rule or "").strip().lower()
        if rule and not entry.from_ids and rule not in PREMISELESS_OK and (
                rule in RULES or rule in NON_LOGICAL_RULES):
            warnings.append({
                "id": entry.id,
                "issue": f"step cites no premises; a {rule} step normally cites something",
            })
        for cited in entry.from_ids:
            if cited not in position:
                flaws.append({"id": entry.id,
                              "issue": f"cites the unknown id {cited!r}"})
            elif position[cited] >= position[entry.id]:
                flaws.append({
                    "id": entry.id,
                    "issue": f"cites {cited!r}, which does not come earlier in the proof "
                             "(this would be a circular or forward reference)",
                })

    # -- per-step re-check -------------------------------------------------- #
    for entry in session.entries:
        if entry.kind not in ("given", "assumption"):
            check_entry(session, entry)

    refuted = [e.id for e in session.entries if e.verdict == STEP_REFUTED]
    invalid = [e.id for e in session.entries if e.verdict == STEP_INVALID]
    unchecked = [e.id for e in session.entries if e.verdict == STEP_UNCHECKED]
    verified = [e.id for e in session.entries if e.verdict == STEP_VERIFIED]

    for entry_id in refuted:
        entry = session.by_id(entry_id)
        flaws.append({"id": entry_id,
                      "issue": f"REFUTED: {entry.detail if entry else ''}"})
    for entry_id in invalid:
        entry = session.by_id(entry_id)
        flaws.append({"id": entry_id,
                      "issue": f"INVALID: {entry.detail if entry else ''}"}

                     )

    # -- assumption discharge ---------------------------------------------- #
    discharged: set[str] = set()
    for entry in session.entries:
        rule = (entry.rule or "").strip().lower()
        if rule in DISCHARGING_RULES and entry.verdict in (STEP_VERIFIED, STEP_UNCHECKED):
            for cited in entry.from_ids:
                cited_entry = session.by_id(cited)
                if cited_entry is not None and cited_entry.kind == "assumption":
                    discharged.add(cited)
    undischarged = [e.id for e in session.entries
                    if e.kind == "assumption" and e.id not in discharged]
    for entry_id in undischarged:
        entry = session.by_id(entry_id)
        flaws.append({
            "id": entry_id,
            "issue": (
                f"the assumption `{entry.statement if entry else ''}` is never discharged. "
                "Close it with conditional_proof, reductio, disjunction_elim, "
                "case_analysis, or existential_instantiation."
            ),
        })

    # -- lemma discharge ---------------------------------------------------- #
    open_lemmas: list[str] = []
    disclosed: list[str] = []
    for entry in session.entries:
        if entry.kind != "lemma":
            continue
        if entry.assumed:
            disclosed.append(entry.id)
            warnings.append({
                "id": entry.id,
                "issue": f"lemma `{entry.name or entry.statement}` is declared without proof "
                         "and is therefore an assumption of the whole argument",
            })
        elif not entry.proved:
            open_lemmas.append(entry.id)
    for entry_id in open_lemmas:
        entry = session.by_id(entry_id)
        flaws.append({
            "id": entry_id,
            "issue": f"lemma `{entry.name or (entry.statement if entry else '')}` is used but "
                     "not proved; prove it or mark it as assumed",
        })

    # -- goal --------------------------------------------------------------- #
    goal_entry: Entry | None = None
    if session.goal_id:
        goal_entry = session.by_id(session.goal_id)
    if goal_entry is None:
        for entry in reversed(session.entries):
            if entry.kind == "step":
                goal_entry = entry
                break
    goal_reached = False
    if goal_entry is None:
        flaws.append({"id": "-", "issue": "the session contains no proof step"})
    else:
        goal_reached = goals_match(session.goal, goal_entry.statement, session.functions)
        if not goal_reached:
            flaws.append({
                "id": goal_entry.id,
                "issue": (
                    f"the last step states `{goal_entry.statement}` but the declared goal "
                    f"is `{session.goal}`; the goal is not derived"
                ),
            })

    total = len(verified) + len(unchecked) + len(refuted) + len(invalid)
    coverage = (len(verified) / total) if total else 0.0
    if flaws:
        verdict = AUDIT_FLAWED
    elif unchecked:
        verdict = AUDIT_GAPS
    else:
        verdict = AUDIT_VERIFIED

    summary = {
        AUDIT_VERIFIED: "every step was machine-checked and the goal is derived",
        AUDIT_GAPS: (
            f"the proof is structurally sound and nothing was refuted, but "
            f"{len(unchecked)} step(s) rest on human justification rather than a machine check"
        ),
        AUDIT_FLAWED: "the proof has flaws that must be resolved before it can be accepted",
    }[verdict]

    return {
        "session_id": session.session_id,
        "goal": session.goal,
        "verdict": verdict,
        "summary": summary,
        "goal_reached": goal_reached,
        "goal_step": goal_entry.id if goal_entry else None,
        "machine_coverage": {
            "verified_steps": len(verified),
            "unchecked_steps": len(unchecked),
            "refuted_steps": len(refuted),
            "invalid_steps": len(invalid),
            "checked_fraction": round(coverage, 3),
        },
        "verified_ids": verified,
        "unchecked_ids": unchecked,
        "refuted_ids": refuted,
        "invalid_ids": invalid,
        "undischarged_assumption_ids": undischarged,
        "unproved_lemma_ids": open_lemmas,
        "disclosed_assumption_ids": disclosed,
        "flaws": flaws,
        "warnings": warnings,
        "next_actions": _next_actions(flaws, unchecked, session),
        "checked_at": time.time(),
    }


def _next_actions(flaws: Sequence[Mapping[str, str]], unchecked: Sequence[str],
                  session: ProofSession) -> list[str]:
    actions: list[str] = []
    if any("REFUTED" in f.get("issue", "") for f in flaws):
        actions.append(
            "Repair or remove the refuted steps; each one comes with the counterexample that "
            "kills it."
        )
    if any("INVALID" in f.get("issue", "") for f in flaws):
        actions.append(
            "Fix the cited rule or the statement for the invalid steps; the report names the "
            "shape the rule requires."
        )
    if any("never discharged" in f.get("issue", "") for f in flaws):
        actions.append(
            "Close the open assumptions with the discharging rule that matches how they "
            "were used."
        )
    if any("not proved" in f.get("issue", "") for f in flaws):
        actions.append("Prove the outstanding lemmas, or mark them as assumed and disclose that.")
    if any("not derived" in f.get("issue", "") for f in flaws):
        actions.append("Add the steps that connect the last statement to the declared goal.")
    if unchecked:
        actions.append(
            f"{len(unchecked)} step(s) are unchecked. Machine-check them by citing the exact "
            "premises the rule needs, or state explicitly in the final write-up that they rest "
            "on human justification."
        )
    if not actions:
        actions.append("The proof is ready to export with proof_export.")
    return actions


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #


def export_session(session: ProofSession, fmt: str = "markdown") -> str:
    fmt = (fmt or "markdown").strip().lower()
    if fmt in ("latex", "tex"):
        return _export_latex(session)
    if fmt in ("json",):
        payload = asdict(session)
        payload["entries"] = [e.to_dict() for e in session.entries]
        return json.dumps(payload, ensure_ascii=False, indent=2)
    return _export_markdown(session)


def _export_markdown(session: ProofSession) -> str:
    audit_result = session.audit or audit(session)
    lines: list[str] = []
    lines.append(f"# Proof session `{session.session_id}`")
    lines.append("")
    lines.append(f"**Problem.** {session.problem}")
    lines.append("")
    lines.append(f"**Goal.** {session.goal}")
    if session.strategy:
        lines.append("")
        lines.append(f"**Strategy.** {session.strategy}")
    if session.variables:
        lines.append("")
        lines.append("**Domains.** " + ", ".join(
            f"{name}: {sort}" for name, sort in session.variables.items()))
    lines.append("")
    audit_line = {
        AUDIT_VERIFIED: "machine-verified",
        AUDIT_GAPS: "sound with unchecked steps",
        AUDIT_FLAWED: "contains flaws",
    }[audit_result["verdict"]]
    counts = audit_result["machine_coverage"]
    lines.append(
        f"**Audit verdict.** {audit_line} — {counts['verified_steps']} verified, "
        f"{counts['unchecked_steps']} unchecked, {counts['refuted_steps']} refuted, "
        f"{counts['invalid_steps']} invalid."
    )
    lines.append("")

    for label, kinds in (("Givens", ("given",)), ("Assumptions", ("assumption",)),
                         ("Lemmas", ("lemma",)), ("Proof", ("step",))):
        group = [e for e in session.entries if e.kind in kinds]
        if not group:
            continue
        lines.append(f"## {label}")
        lines.append("")
        for entry in group:
            tag = {
                STEP_VERIFIED: "machine-verified",
                STEP_REFUTED: "**REFUTED**",
                STEP_INVALID: "**INVALID**",
                STEP_UNCHECKED: "unchecked",
                STEP_ASSUMED: "assumed",
            }.get(entry.verdict, entry.verdict)
            cites = f" [{', '.join(entry.from_ids)}]" if entry.from_ids else ""
            rule = f" by *{entry.rule}*" if entry.rule else ""
            name = f" ({entry.name})" if entry.name else ""
            lines.append(f"{entry.id}. {entry.statement}")
            lines.append(f"   - justification:{rule}{cites} — {tag}")
            if entry.justification:
                lines.append(f"   - note: {entry.justification}")
            if entry.detail and entry.verdict in (STEP_REFUTED, STEP_INVALID):
                lines.append(f"   - detail: {entry.detail}")
        lines.append("")

    if audit_result["flaws"]:
        lines.append("## Outstanding flaws")
        lines.append("")
        for flaw in audit_result["flaws"]:
            lines.append(f"- `{flaw['id']}`: {flaw['issue']}")
        lines.append("")
    if audit_result["warnings"]:
        lines.append("## Warnings")
        lines.append("")
        for warning in audit_result["warnings"]:
            lines.append(f"- `{warning['id']}`: {warning['issue']}")
        lines.append("")
    return "\n".join(lines)


def _export_latex(session: ProofSession) -> str:
    lines: list[str] = []
    lines.append(f"% proof session {session.session_id}")
    lines.append("\\paragraph{Problem.} " + _escape(session.problem))
    lines.append("\\paragraph{Goal.} $" + _latex_of(session.goal, session.functions) + "$")
    if session.variables:
        lines.append("\\paragraph{Domains.} " + ", ".join(
            f"${_escape(name)} \\in {_sort_latex(sort)}$"
            for name, sort in session.variables.items()))
    lines.append("\\begin{proof}")
    for entry in session.entries:
        if entry.kind == "given":
            lines.append(f"\\textbf{{({entry.id})}} (given) ${_latex_of(entry.statement, session.functions)}$.")
        elif entry.kind == "assumption":
            lines.append(f"\\textbf{{({entry.id})}} Assume ${_latex_of(entry.statement, session.functions)}$.")
        elif entry.kind == "lemma":
            lines.append(f"\\textbf{{({entry.id})}} Lemma ({_escape(entry.name or entry.id)}): "
                         f"${_latex_of(entry.statement, session.functions)}$.")
        else:
            cites = f" by {entry.rule}" if entry.rule else ""
            if entry.from_ids:
                cites += f" from {', '.join(entry.from_ids)}"
            lines.append(f"\\textbf{{({entry.id})}} ${_latex_of(entry.statement, session.functions)}$"
                         f"\\footnote{{justification:{_escape(cites)}}}")
    lines.append("\\end{proof}")
    return "\n".join(lines)


def _sort_latex(sort: str) -> str:
    return {"int": r"\mathbb{Z}", "nat": r"\mathbb{N}", "real": r"\mathbb{R}",
            "rat": r"\mathbb{Q}", "bool": r"\mathrm{Bool}"}.get(sort, r"\mathcal{U}")


def _latex_of(text: str, functions: Sequence[str]) -> str:
    try:
        return parse(text, functions).latex()
    except Exception:  # noqa: BLE001
        return _escape(text)


def _escape(text: str) -> str:
    return (str(text).replace("\\", r"\textbackslash{}").replace("_", r"\_")
            .replace("&", r"\&").replace("%", r"\%").replace("#", r"\#"))
