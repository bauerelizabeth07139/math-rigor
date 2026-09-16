# 完整范例

下面五个范例中的每一个判定、反例与计数，都来自工具的真实返回，不是示意。

（工具返回的 `detail`、`method`、`summary` 等字段是英文的，因为它们是工具内部产生的。结论本身不受影响。）

## 范例一：整除命题，用归纳法（`6 | n^3 - n`）

**阶段 1：形式化。** 命题必须写成带量词与定义域的完整形式。

调用 `proof_start`：

```json
{
  "problem": "对每个整数 n，n^3 - n 能被 6 整除。",
  "goal": "6 | n^3 - n",
  "kind": "prove",
  "variables": {
    "n": "int"
  },
  "strategy": "直接用已验证的模 6 残数分析。"
}
```

返回（节选）：

```json
{
  "session_id": "ps_xxxxxxxxxxxx",
  "goal": "6 | n^3 - n",
  "variables": {
    "n": "int"
  },
  "next_stage": "plan",
  "guidance": "Choose a strategy and name it: direct, contrapositive, contradiction, induction, construction, cases, invariant, extremal, or counting. Register every lemma the plan needs before proving anything."
}
```

**阶段 4：先让工具判定，再把结果作为一步记录下来。**

调用 `verify_forall`：

```json
{
  "statement": "forall n in Z: 6 | n^3 - n"
}
```

返回（节选）：

```json
{
  "status": "proven",
  "method": "residue case split on n mod 6 (6 cases, each refuted)",
  "sorts_used": {
    "n": "int"
  },
  "notes": [
    "each residue class was proved separately because the direct SMT query was inconclusive"
  ]
}
```

`proven` 且方法明确——这是在残数类上分别反证否定式得到的，属于可引用的结论。

注意直接写成 `forall n in Z: exists k in Z: n^3 - n = 6*k` 也可以：
工具会自动把该存在式改写成 `6 | n^3 - n` 以进入可判定的形式。

调用 `verify_induction`：

```json
{
  "predicate": "6 | n^3 - n",
  "variable": "n",
  "start": "0"
}
```

返回（节选）：

```json
{
  "base_case": {
    "instance": "6 | 0**3-0",
    "status": "proven",
    "reason": null,
    "counterexample": null
  },
  "inductive_step": {
    "obligation": "forall n:int. n >= 0 & 6 | n**3-n -> 6 | (n+1)**3-(n+1)",
    "status": "proven",
    "method": "residue case split on n mod 6 (6 cases, each refuted)",
    "reason": null,
    "counterexample": null,
    "elapsed_ms": 40052
  },
  "verdict": "proven"
}
```

归纳法也就位了：基例 `6 | 0**3-0` 为 `proven`，归纳步为 `proven`（方法：residue case split on n mod 6 (6 cases, each refuted)）。

**阶段 5：审计。** 把上面的机器结论记录成一步，然后审计整个论证。

记录两条条目：

```json
[
  {
    "id": "g1",
    "statement": "n^3 - n = n*(n-1)*(n+1)",
    "rule": "premise",
    "from_ids": [],
    "justification": "沿用题面中给出的因式分解",
    "kind": "given",
    "verdict": "assumed",
    "detail": "taken as a hypothesis; it is not proved here and the audit tracks whether it was discharged",
    "machine_checked": false,
    "note": "",
    "name": "",
    "proved": false,
    "assumed": false
  },
  {
    "id": "s1",
    "statement": "6 | n^3 - n",
    "rule": "machine_verified",
    "from_ids": [
      "g1"
    ],
    "justification": "verify_forall 返回 proven，方法为按模 6 的残数分情形（6 个情形各自反证）",
    "kind": "step",
    "verdict": "verified",
    "detail": "the cited premises were machine-verified to entail this statement (residue case split on n mod 6 (6 cases, each refuted))",
    "machine_checked": true,
    "note": "",
    "name": "",
    "proved": false,
    "assumed": false
  }
]
```

调用 `proof_validate`：

```json
{"session_id": "ps_example1"}
```

返回（节选）：

```json
{
  "verdict": "verified",
  "summary": "every step was machine-checked and the goal is derived",
  "goal_reached": true,
  "machine_coverage": {
    "verified_steps": 1,
    "unchecked_steps": 0,
    "refuted_steps": 0,
    "invalid_steps": 0,
    "checked_fraction": 1.0
  },
  "verified_ids": [
    "s1"
  ],
  "unchecked_ids": [],
  "flaws": [],
  "next_actions": [
    "The proof is ready to export with proof_export."
  ]
}
```

结论：`verified`，可以报告为已证明。

## 范例二：不等式——定义域就是命题的一部分

调用 `verify_inequality`：

```json
{
  "lhs": "x + 1/x",
  "relation": ">=",
  "rhs": "2",
  "variables": {
    "x": "real"
  },
  "constraints": [
    "x > 0"
  ]
}
```

返回（节选）：

```json
{
  "status": "proven",
  "method": "refuted the negated goal with z3",
  "sorts_used": {
    "x": "real"
  },
  "verdict": "proven"
}
```

**去掉 `x > 0` 会怎样？** 同一个不等式在 R 上并不成立：

调用 `verify_inequality`：

```json
{
  "lhs": "x + 1/x",
  "relation": ">=",
  "rhs": "2",
  "variables": {
    "x": "real"
  }
}
```

返回（节选）：

```json
{
  "status": "refuted",
  "method": "z3 produced an assignment falsifying the goal",
  "sorts_used": {
    "x": "real"
  },
  "counterexample": {
    "x": "0"
  },
  "verdict": "refuted",
  "notes": [
    "no constraints were given, so the claim was tested over the whole declared domain"
  ]
}
```

这就是为什么 `verify_inequality` 必须显式给出 `variables` 与 `constraints`：
少了定义域条件，证的就是另一个（假的）命题。

## 范例三：审计抓出一个错步骤

下面两条代数步骤只有一条成立。

```json
[
  {
    "id": "s1",
    "statement": "(a+b)^2 = a^2 + 2*a*b + b^2",
    "rule": "algebra",
    "verdict": "verified",
    "detail": "the cited premises were machine-verified to entail this statement (refuted the negated goal with z3)"
  },
  {
    "id": "s2",
    "statement": "(a+b)^2 = a^2 + b^2",
    "rule": "algebra",
    "verdict": "refuted",
    "detail": "this algebra step does not follow from the cited premises; counterexample: {'a': '-1', 'b': '-1'}. Either cite the premises the rewrite actually uses, or fix the step."
  }
]
```

正确的步骤被判 `verified`；写错的步骤被判 `refuted`，并给出反例 `a = b = -1`（此时左边 4，右边 2）。

调用 `proof_validate` 后：

```json
{
  "verdict": "flawed",
  "summary": "the proof has flaws that must be resolved before it can be accepted",
  "machine_coverage": {
    "verified_steps": 1,
    "unchecked_steps": 0,
    "refuted_steps": 1,
    "invalid_steps": 0,
    "checked_fraction": 0.5
  },
  "refuted_ids": [
    "s2"
  ],
  "flaws": [
    {
      "id": "s2",
      "issue": "REFUTED: this algebra step does not follow from the cited premises; counterexample: {'a': '-1', 'b': '-1'}. Either cite the premises the rewrite actually uses, or fix the step."
    },
    {
      "id": "s2",
      "issue": "the last step states `(a+b)^2 = a^2 + b^2` but the declared goal is `(a+b)^2 = a^2 + 2*a*b + b^2`; the goal is not derived"
    }
  ]
}
```

注意这里的机制：`algebra` 步骤会被拿去**从它引用的前提重新推导**。
推不出来就是反例，推得出来才给 `verified`。所以引用太少的前提会暴露出来。

## 范例四：什么时候该先找反例

拿不准命题真假时，先花一次调用确认，比证半天发现是假命题划算得多。

调用 `find_counterexample`：

```json
{
  "claim": "forall n in R: n^2 >= n"
}
```

返回（节选）：

```json
{
  "verdict": "refuted",
  "counterexample": {
    "n": "1/2"
  },
  "method": "the SMT solver produced a model of the negation"
}
```

`refuted` 并给出 `n = 1/2`，所以这个命题要改（例如加上 `n <= 0 || n >= 1`）。

反过来，如果返回 `proven`，那表示否定式不可满足——这本身就是命题成立的证明：

调用 `find_counterexample`：

```json
{
  "claim": "forall n in Z: n > 2 -> n^2 > 2*n",
  "variables": {
    "n": "int"
  }
}
```

返回（节选）：

```json
{
  "verdict": "proven",
  "method": "the negation is unsatisfiable, so no counterexample exists and the claim holds over the declared domains"
}
```

## 范例五：恒等式需要假设时不要硬证

调用 `verify_identity`：

```json
{
  "lhs": "sqrt(x^2)",
  "rhs": "x"
}
```

返回（节选）：

```json
{
  "verdict": "refuted",
  "method": "a numerical sample falsifies the identity",
  "counterexample": {
    "x": "-1/2"
  }
}
```

调用 `verify_identity`：

```json
{
  "lhs": "sqrt(x^2)",
  "rhs": "x",
  "assumptions": [
    "x: positive"
  ]
}
```

返回（节选）：

```json
{
  "verdict": "proven",
  "method": "the difference reduces to zero by simplify"
}
```

同一个恒等式，没有假设时被数值反例推翻（`x = -1/2`），
补上 `x: positive` 后化简为零、判为 `proven`。
**恒等式是否成立取决于定义域——把假设写出来，而不是让它含糊过去。**

