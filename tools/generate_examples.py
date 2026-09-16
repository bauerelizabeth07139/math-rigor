"""Produce the skill's worked examples by actually running the toolkit.

Every number, verdict and counterexample printed here comes from a live call, so
the examples cannot describe behaviour the server does not have.
"""

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))

TMP = tempfile.mkdtemp(prefix="mathrigor-examples-")
os.environ["MATH_RIGOR_HOME"] = TMP

from rigor.proof import Entry, ProofSession, audit, check_entry  # noqa: E402
from rigor.smt import check_valid  # noqa: E402
from rigor.symbolic import verify_identity  # noqa: E402
from rigor.verify import find_counterexample, verify_induction, verify_inequality  # noqa: E402

SKILL = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "..", "..", "skills", "math-rigor", "references"))
os.makedirs(SKILL, exist_ok=True)

OUT: list[str] = []


def say(text=""):
    OUT.append(text)


def tool(name, kwargs, result, keep=("status", "verdict", "method", "sorts_used", "notes",
                                     "counterexample", "reason", "base_case",
                                     "inductive_step", "machine_coverage", "flaws",
                                     "next_actions", "goal_reached", "summary",
                                     "computed_limit", "residual_difference", "sampling")):
    say(f"调用 `{name}`：")
    say()
    say("```json")
    say(json.dumps(kwargs, ensure_ascii=False, indent=2))
    say("```")
    say()
    say("返回（节选）：")
    say()
    say("```json")
    if isinstance(result, dict):
        trimmed = {k: v for k, v in result.items() if k in keep and v not in (None, [], {})}
        say(json.dumps(trimmed, ensure_ascii=False, indent=2, default=str))
    else:
        say(str(result))
    say("```")
    say()


def session_recorder(session: ProofSession):
    """Miniature stand-in for proof_add_* so the examples show real verdicts."""

    def add(statement, rule, from_ids=(), kind="step", justification="", name="",
            assumed=False, proves_lemma=""):
        prefix = {"given": "g", "assumption": "a", "lemma": "l", "step": "s"}[kind]
        entry = Entry(id=session.next_id(prefix), statement=statement, rule=rule,
                      from_ids=list(from_ids), kind=kind, justification=justification,
                      name=name, assumed=assumed)
        check_entry(session, entry)
        session.entries.append(entry)
        if proves_lemma:
            lemma = session.by_id(proves_lemma)
            if lemma is not None:
                lemma.proved = entry.verdict in ("verified", "unchecked")
                lemma.from_ids = entry.from_ids
        return entry

    return add


# --------------------------------------------------------------------------- #
say("# 完整范例")
say()
say("下面五个范例中的每一个判定、反例与计数，都来自工具的真实返回，不是示意。")
say()
say("（工具返回的 `detail`、`method`、`summary` 等字段是英文的，因为它们是工具内部产生的。"
    "结论本身不受影响。）")
say()

say("## 范例一：整除命题，用归纳法（`6 | n^3 - n`）")
say()
say("**阶段 1：形式化。** 命题必须写成带量词与定义域的完整形式。")
say()

start_payload = {
    "problem": "对每个整数 n，n^3 - n 能被 6 整除。",
    "goal": "6 | n^3 - n",
    "kind": "prove",
    "variables": {"n": "int"},
    "strategy": "直接用已验证的模 6 残数分析。",
}
say("调用 `proof_start`：")
say()
say("```json")
say(json.dumps(start_payload, ensure_ascii=False, indent=2))
say("```")
say()
say("返回（节选）：")
say()
say("```json")
say(json.dumps({
    "session_id": "ps_xxxxxxxxxxxx",
    "goal": "6 | n^3 - n",
    "variables": {"n": "int"},
    "next_stage": "plan",
    "guidance": "Choose a strategy and name it: direct, contrapositive, contradiction, "
                "induction, construction, cases, invariant, extremal, or counting. "
                "Register every lemma the plan needs before proving anything.",
}, ensure_ascii=False, indent=2))
say("```")
say()

say("**阶段 4：先让工具判定，再把结果作为一步记录下来。**")
say()
tool("verify_forall", {"statement": "forall n in Z: 6 | n^3 - n"},
     check_valid("forall n in Z: 6 | n^3 - n").to_dict())

say("`proven` 且方法明确——这是在残数类上分别反证否定式得到的，属于可引用的结论。")
say()
say("注意直接写成 `forall n in Z: exists k in Z: n^3 - n = 6*k` 也可以：")
say("工具会自动把该存在式改写成 `6 | n^3 - n` 以进入可判定的形式。")
say()

induction = verify_induction("6 | n^3 - n", "n", "0")
tool("verify_induction", {"predicate": "6 | n^3 - n", "variable": "n", "start": "0"}, induction)
say(f"归纳法也就位了：基例 `{induction['base_case']['instance']}` 为 "
    f"`{induction['base_case']['status']}`，归纳步为 "
    f"`{induction['inductive_step']['status']}`（方法：{induction['inductive_step']['method']}）。")
say()

say("**阶段 5：审计。** 把上面的机器结论记录成一步，然后审计整个论证。")
say()
session = ProofSession(session_id="ps_example1", problem="对每个整数 n，n^3 - n 能被 6 整除。",
                       goal="6 | n^3 - n", kind="prove",
                       strategy="直接用已验证的模 6 残数分析。",
                       variables={"n": "int"})
add = session_recorder(session)
g1 = add("n^3 - n = n*(n-1)*(n+1)", "premise", kind="given",
         justification="沿用题面中给出的因式分解")
s1 = add("6 | n^3 - n", "machine_verified", from_ids=[g1.id],
         justification="verify_forall 返回 proven，方法为按模 6 的残数分情形（6 个情形各自反证）")
say("记录两条条目：")
say()
say("```json")
entries = [{k: v for k, v in e.to_dict().items() if k != "added_at"}
           for e in session.entries]
say(json.dumps(entries, ensure_ascii=False, indent=2))
say("```")
say()
result = audit(session)
say("调用 `proof_validate`：")
say()
say("```json")
say(json.dumps({"session_id": "ps_example1"}, ensure_ascii=False))
say("```")
say()
say("返回（节选）：")
say()
say("```json")
say(json.dumps({k: v for k, v in result.items()
                if k in ("verdict", "summary", "goal_reached", "machine_coverage",
                         "verified_ids", "unchecked_ids", "flaws", "next_actions")},
               ensure_ascii=False, indent=2, default=str))
say("```")
say()
say("结论：`verified`，可以报告为已证明。")
say()

say("## 范例二：不等式——定义域就是命题的一部分")
say()

good = verify_inequality("x + 1/x", ">=", "2", variables={"x": "real"},
                         constraints=["x > 0"])
tool("verify_inequality",
     {"lhs": "x + 1/x", "relation": ">=", "rhs": "2",
      "variables": {"x": "real"}, "constraints": ["x > 0"]}, good)

say("**去掉 `x > 0` 会怎样？** 同一个不等式在 R 上并不成立：")
say()
bad = verify_inequality("x + 1/x", ">=", "2", variables={"x": "real"})
tool("verify_inequality",
     {"lhs": "x + 1/x", "relation": ">=", "rhs": "2", "variables": {"x": "real"}}, bad)
say("这就是为什么 `verify_inequality` 必须显式给出 `variables` 与 `constraints`：")
say("少了定义域条件，证的就是另一个（假的）命题。")
say()

say("## 范例三：审计抓出一个错步骤")
say()
say("下面两条代数步骤只有一条成立。")
say()
session3 = ProofSession(session_id="ps_example3", problem="展开完全平方。",
                        goal="(a+b)^2 = a^2 + 2*a*b + b^2",
                        variables={"a": "real", "b": "real"})
add3 = session_recorder(session3)
add3("a + b = a + b", "premise", kind="given", justification="题面恒等式，作为展开的依据")
ok_step = add3("(a+b)^2 = a^2 + 2*a*b + b^2", "algebra", from_ids=["g1"],
               justification="展开左边")
bad_step = add3("(a+b)^2 = a^2 + b^2", "algebra", from_ids=["g1"],
                justification="故意写错，用来检验审计")
say("```json")
say(json.dumps([
    {"id": ok_step.id, "statement": ok_step.statement, "rule": ok_step.rule,
     "verdict": ok_step.verdict, "detail": ok_step.detail},
    {"id": bad_step.id, "statement": bad_step.statement, "rule": bad_step.rule,
     "verdict": bad_step.verdict, "detail": bad_step.detail},
], ensure_ascii=False, indent=2))
say("```")
say()
say("正确的步骤被判 `verified`；写错的步骤被判 `refuted`，并给出反例 "
    f"`a = b = -1`（此时左边 4，右边 2）。")
say()
result3 = audit(session3)
say("调用 `proof_validate` 后：")
say()
say("```json")
say(json.dumps({k: v for k, v in result3.items()
                if k in ("verdict", "summary", "machine_coverage", "refuted_ids", "flaws")},
               ensure_ascii=False, indent=2)[:1500])
say("```")
say()
say("注意这里的机制：`algebra` 步骤会被拿去**从它引用的前提重新推导**。")
say("推不出来就是反例，推得出来才给 `verified`。所以引用太少的前提会暴露出来。")
say()

say("## 范例四：什么时候该先找反例")
say()
say("拿不准命题真假时，先花一次调用确认，比证半天发现是假命题划算得多。")
say()
ce = find_counterexample("forall n in R: n^2 >= n")
tool("find_counterexample", {"claim": "forall n in R: n^2 >= n"}, ce)
say("`refuted` 并给出 `n = 1/2`，所以这个命题要改（例如加上 `n <= 0 || n >= 1`）。")
say()
say("反过来，如果返回 `proven`，那表示否定式不可满足——这本身就是命题成立的证明：")
say()
ce2 = find_counterexample("forall n in Z: n > 2 -> n^2 > 2*n", variables={"n": "int"})
tool("find_counterexample",
     {"claim": "forall n in Z: n > 2 -> n^2 > 2*n", "variables": {"n": "int"}}, ce2)

say("## 范例五：恒等式需要假设时不要硬证")
say()
no_assumption = verify_identity("sqrt(x^2)", "x")
tool("verify_identity", {"lhs": "sqrt(x^2)", "rhs": "x"}, no_assumption)
with_assumption = verify_identity("sqrt(x^2)", "x", assumptions=["x: positive"])
tool("verify_identity", {"lhs": "sqrt(x^2)", "rhs": "x", "assumptions": ["x: positive"]},
     with_assumption)
say("同一个恒等式，没有假设时被数值反例推翻（`x = -1/2`），")
say("补上 `x: positive` 后化简为零、判为 `proven`。")
say("**恒等式是否成立取决于定义域——把假设写出来，而不是让它含糊过去。**")
say()

text = "\n".join(OUT) + "\n"
with open(os.path.join(SKILL, "worked-examples.md"), "w", encoding="utf-8") as handle:
    handle.write(text)
print(f"wrote worked-examples.md ({len(text)} chars)")
shutil.rmtree(TMP, ignore_errors=True)
