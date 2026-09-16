"""End-to-end test of the math-rigor MCP server over the real stdio protocol.

Run from anywhere: the server is located relative to this file.
"""

import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = Path(__file__).resolve().parent
PY = sys.executable
SRV = str(HERE.parent / "server" / "math_rigor_server.py")


PY = r"C:\Users\cloud_user\.zcode\math-rigor-mcp\venv\Scripts\python.exe"
SRV = r"C:\Users\cloud_user\.zcode\math-rigor-mcp\server\math_rigor_server.py"

TMP = tempfile.mkdtemp(prefix="mathrigor-mcp-")
os.environ["MATH_RIGOR_HOME"] = TMP

PASS = 0
FAIL = 0


def check(label, condition, extra=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"ok   {label}")
    else:
        FAIL += 1
        print(f"FAIL {label} {extra}")


async def call(session, name, args, expect_error=False):
    result = await session.call_tool(name, args)
    text = result.content[0].text if result.content else ""
    try:
        payload = json.loads(text)
    except Exception:
        payload = {"_raw": text}
    if not expect_error and isinstance(payload, dict) and payload.get("error"):
        print(f"     !! {name} returned an error: {str(payload.get('message'))[:220]}")
    return payload


async def main():
    params = StdioServerParameters(command=PY, args=[SRV],
                                   env={**os.environ, "MATH_RIGOR_HOME": TMP})
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print(f"connected to {init.server_info.name} v{init.server_info.version}")

            tools = (await session.list_tools()).tools
            names = sorted(t.name for t in tools)
            print(f"\n{len(tools)} tools registered:")
            for name in names:
                print(f"   - {name}")
            check("tool count is 23", len(tools) == 23, f"got {len(tools)}")
            for required in ["proof_start", "proof_add_step", "proof_validate", "proof_export",
                             "logic_check_step", "logic_entails", "logic_truth_table",
                             "logic_rules", "proof_workflow", "symbolic_eval",
                             "verify_identity", "verify_inequality", "verify_forall",
                             "verify_induction", "verify_limit", "find_counterexample",
                             "number_theory", "expr_normalise"]:
                check(f"tool present: {required}", required in names)

            print("\n=== workflow guidance ===")
            wf = await call(session, "proof_workflow", {})
            check("workflow has 6 stages", len(wf.get("stages", [])) == 6)
            rules = await call(session, "logic_rules", {})
            check("rule catalogue is populated", len(rules.get("logical_rules", {})) >= 20)

            print("\n=== a complete proof session: 6 divides n^3 - n ===")
            start = await call(session, "proof_start", {
                "problem": "For every integer n, n^3 - n is divisible by 6.",
                "goal": "6 | n^3 - n",
                "kind": "prove",
                "variables": {"n": "int"},
                "strategy": "direct, by the verified residue analysis of n mod 2 and n mod 3",
            })
            sid = start["session_id"]
            check("session opened", sid.startswith("ps_"))

            g1 = await call(session, "proof_add_given",
                            {"session_id": sid,
                             "statement": "n^3 - n = n*(n-1)*(n+1)",
                             "note": "factorisation used by the proof"})
            check("given recorded", g1.get("added", {}).get("id") == "g1")

            machine = await call(session, "verify_forall", {"statement": "forall n in Z: 6 | n^3 - n"})
            check("verify_forall proves it", machine.get("status") == "proven",
                  str(machine)[:160])

            s1 = await call(session, "proof_add_step", {
                "session_id": sid,
                "statement": "6 | n^3 - n",
                "rule": "machine_verified",
                "from_ids": ["g1"],
                "justification": "verify_forall returned proven by residue case split on n mod 6",
            })
            check("machine_verified step", s1.get("verdict") == "verified", str(s1)[:200])

            validation = await call(session, "proof_validate", {"session_id": sid})
            check("audit verdict is verified", validation.get("verdict") == "verified",
                  str(validation)[:200])
            check("goal reached", validation.get("goal_reached") is True)

            exported = await call(session, "proof_export", {"session_id": sid})
            check("markdown export", "Audit verdict" in str(exported))

            print("\n=== the audit catches a wrong algebra step ===")
            start2 = await call(session, "proof_start", {
                "problem": "Show correct and incorrect algebra side by side.",
                "goal": "(a+b)^2 = a^2 + 2*a*b + b^2",
                "variables": {"a": "real", "b": "real"},
            })
            sid2 = start2["session_id"]
            await call(session, "proof_add_given", {"session_id": sid2, "statement": "a + b = a + b"})
            ok_step = await call(session, "proof_add_step", {
                "session_id": sid2, "statement": "(a+b)^2 = a^2 + 2*a*b + b^2",
                "rule": "algebra", "from_ids": ["g1"], "justification": "expand",
            })
            check("correct step verified", ok_step.get("verdict") == "verified")
            bad_step = await call(session, "proof_add_step", {
                "session_id": sid2, "statement": "(a+b)^2 = a^2 + b^2",
                "rule": "algebra", "from_ids": ["g1"], "justification": "deliberately wrong",
            })
            check("wrong step refuted", bad_step.get("verdict") == "refuted", str(bad_step)[:200])
            check("counterexample reported",
                  bool(bad_step.get("step", {}).get("detail")),
                  str(bad_step)[:200])
            v2 = await call(session, "proof_validate", {"session_id": sid2})
            check("audit verdict is flawed", v2.get("verdict") == "flawed")

            print("\n=== logic tools ===")
            mp = await call(session, "logic_check_step", {
                "premises": ["P -> Q", "P"], "conclusion": "Q", "rule": "modus_ponens"})
            check("modus ponens valid", mp.get("verdict") == "valid")
            mis = await call(session, "logic_check_step", {
                "premises": ["P -> Q", "Q"], "conclusion": "P", "rule": "modus_ponens"})
            check("affirming the consequent rejected", mis.get("verdict") == "invalid_shape")
            ent = await call(session, "logic_entails", {
                "premises": ["forall x in Z: 6 | x^3 - x"], "conclusion": "6 | 5^3 - 5"})
            check("entailment proven", ent.get("status") == "proven", str(ent)[:160])
            tt = await call(session, "logic_truth_table", {"conclusion": "P | ~P"})
            check("tautology detected", tt.get("tautology") is True)
            tt2 = await call(session, "logic_truth_table",
                             {"premises": ["P -> Q", "Q"], "conclusion": "P"})
            check("falsifying row found", tt2.get("falsifying_assignments") == [{"P": False, "Q": True}],
                  str(tt2.get("falsifying_assignments")))

            print("\n=== mathematics tools ===")
            ident = await call(session, "verify_identity", {
                "lhs": "sin(x)^2 + cos(x)^2", "rhs": "1"})
            check("identity proven", ident.get("verdict") == "proven")
            ident_bad = await call(session, "verify_identity",
                                   {"lhs": "(n+1)^2", "rhs": "n^2 + 1"})
            check("false identity refuted", ident_bad.get("verdict") == "refuted")
            ineq = await call(session, "verify_inequality", {
                "lhs": "x + 1/x", "relation": ">=", "rhs": "2",
                "variables": {"x": "real"}, "constraints": ["x > 0"]})
            check("inequality proven", ineq.get("verdict") == "proven", str(ineq)[:160])
            ind = await call(session, "verify_induction", {
                "predicate": "Sum(k, (k, 1, n)) = n*(n+1)/2", "variable": "n", "start": "0"})
            check("induction handled", ind.get("verdict") == "proven", str(ind)[:200])
            ind2 = await call(session, "verify_induction",
                              {"predicate": "6 | n^3 - n", "variable": "n", "start": "0"})
            check("divisibility induction proven", ind2.get("verdict") == "proven", str(ind2)[:200])
            lim = await call(session, "verify_limit", {
                "expression": "sin(x)/x", "variable": "x", "point": "0", "target": "1"})
            check("limit proven", lim.get("verdict") == "proven")
            cex = await call(session, "find_counterexample", {
                "claim": "forall n in Z: n^2 >= n"})
            check("counterexample found", cex.get("verdict") == "refuted", str(cex)[:160])
            sym = await call(session, "symbolic_eval", {
                "expression": r"\frac{x^2-1}{x-1}", "operation": "cancel"})
            check("symbolic_eval works", sym.get("result", {}).get("text") == "x + 1", str(sym)[:160])
            num = await call(session, "symbolic_numeric", {"expression": "3^100"})
            check("numeric_eval works", num.get("is_exact_integer") is True, str(num)[:160])
            nt = await call(session, "number_theory", {"operation": "bezout", "numbers": ["240", "46"]})
            check("bezout works", nt.get("check") == "2", str(nt)[:160])
            norm = await call(session, "expr_normalise", {"expression": "(x-1)(x+1)", "to": "latex"})
            check("normalise gives latex", "latex" in norm and norm["latex"],
                  str(norm)[:160])

            print("\n=== error handling ===")
            err = await call(session, "number_theory", {"operation": "nonsense_op"}, expect_error=True)
            check("unknown operation reported cleanly", err.get("error") is True)
            err2 = await call(session, "proof_status", {"session_id": "does_not_exist"},
                              expect_error=True)
            check("unknown session reported cleanly", err2.get("error") is True)
            err3 = await call(session, "symbolic_eval", {"expression": "x +", "operation": "simplify"},
                              expect_error=True)
            check("parse error reported cleanly", err3.get("error") is True)
            bad = await call(session, "verify_forall", {"statement": "forall x in Z: x^2",
                                                        "variables": {"x": "int"}})
            check("non-boolean statement handled without crashing",
                  bad.get("verdict") in ("inconclusive", "proven", "refuted", None)
                  or bad.get("error") is True, str(bad)[:160])
            vague = await call(session, "proof_add_step", {
                "session_id": sid, "statement": "n is an integer",
                "rule": "algebra", "from_ids": ["g1"]})
            check("domain declaration in a step is recorded, not crashed",
                  vague.get("verdict") == "invalid", str(vague)[:200])
            check("the explanation names the variables argument",
                  "variables" in json.dumps(vague), str(vague)[:240])
            v3 = await call(session, "proof_validate", {"session_id": sid})
            check("audit flags the malformed step",
                  vague.get("step", {}).get("id") in v3.get("invalid_ids", []), str(v3)[:200])

    print(f"\npassed {PASS}, failed {FAIL}")
    shutil.rmtree(TMP, ignore_errors=True)
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
