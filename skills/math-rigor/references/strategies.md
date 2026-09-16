# 证明策略选择

策略不是装饰，它决定了你要向工具提哪些问题。**先命名策略，再登记引理。**

## 选择表

| 命题形态 | 首选策略 | 对应的工具用法 |
|---|---|---|
| `forall x: A(x) -> B(x)`，A 是等式/不等式 | 直接证明 | `verify_forall` / `verify_inequality` 直接判定 |
| 结论是蕴含 `P -> Q`，从 P 不易下手 | 反证或逆否 | `logic_check_step` 用 `contraposition`，再证 `~Q -> ~P` |
| 结论是否定 `~P` | 反证 | `proof_add_assumption` 记 P，导出 `falsum`，用 `reductio` 解除 |
| 关于全体自然数/整数 n | 归纳 | `verify_induction(predicate, variable, start)` |
| 整除、奇偶、同余 | 模残数分情形 | `verify_forall` 会按公式里出现的模数自动分情形 |
| `exists x: P(x)` | 构造 | 给出具体对象，用 `existential_generalization` |
| 结论含 `||`，或条件天然分叉 | 分情形 | `disjunction_elim`，每个分支给一条 `->` 步骤 |
| 命题依赖参数的正负/零 | 分情形 | `proof_add_assumption` 逐个记分支条件，`case_analysis` 解除 |
| 几何/组合中的极值 | 极端原理 | 取达到极值的对象，登记为引理后证矛盾 |
| 计数命题 | 双计数 | 建立两个计数，用 `verify_identity` 证相等 |
| 序列/递推的不变量 | 不变量 | 登记"该量在每步不变"为引理，归纳证之 |
| 命题可能是假的 | 先找反例 | `find_counterexample` |

## 每种策略的落地写法

### 直接证明

把命题原样交给 `verify_forall`。若返回 `proven`，直接用 `machine_verified`
记录一步，引用工具方法与前提 id。

若返回 `inconclusive`，说明求解器不够用（常见于非线性、超越函数、符号幂）。
此时**不要**降级成"显然"，而是：

1. 拆出可机器判定的部分，登记为引理；
2. 不可判定的部分用 `symbolic_eval` 求闭形式或单调性，作为 `symbolic_computation` 步骤，
   并写明依据；
3. 在最终报告中把它列入 `unchecked`。

### 反证法

```
proof_add_assumption(session_id, "~目标")     → a1
... 一系列步骤 ...
proof_add_step(..., statement="falsum", rule="contradiction_intro", from_ids=[...])
proof_add_step(..., statement="目标", rule="reductio", from_ids=["a1", "s_k"])
```

审计会检查：确实存在矛盾前提，且结论确实是某条前提（即那条假设）的否定。

### 逆否

用 `logic_check_step(premises=["P -> Q"], conclusion="~Q -> ~P", rule="contraposition")`
一步搞定。之后按 `~Q -> ~P` 走直接证明。

### 归纳

`verify_induction` 把义务拆成两块：

- **基例**：把 `P(start)` 拿出来判定；
- **归纳步**：判定 `k >= start & P(k) -> P(k+1)`。

符号求和会先用 sympy 求闭形式。如果闭形式让两边变得相同，会直接判 `proven` 并说明
"这是代数恒等式，不需要归纳"。

归纳步 `refuted` 会给出破坏它的 k。常见原因是**归纳假设太弱**——例如证
`n^2 < 2^n` 时若只假设 `P(k)`，在 k 较小时不成立，需要加强假设（改成
`forall j <= k: P(j)` 或加下界）。

### 分情形

```
proof_add_given(session_id, "n > 0 || n = 0 || n < 0")
proof_add_step(..., statement="n > 0 -> 结论", rule="conditional_proof", ...)
proof_add_step(..., statement="n = 0 -> 结论", rule="conditional_proof", ...)
proof_add_step(..., statement="n < 0 -> 结论", rule="conditional_proof", ...)
proof_add_step(..., statement="结论", rule="disjunction_elim", from_ids=[g1, s1, s2, s3])
```

要点：**穷尽性本身必须是被证明或被明确给出的**。审计会检查
`disjunction_elim` 引用了析取式且每个分支都有对应的蕴含步骤，但"这三个情形确实穷尽"
需要你自己在那条析取式上站得住脚。

## 反模式

- **先算后想**：还没写清量词就开始 `symbolic_eval`，证出来的往往是另一个命题。
- **假设漂移**：中途悄悄引入"x 是正的"。所有假设都要显式记录并被解除。
- **以例代证**：`symbolic_numeric` 在几个点上相等，不构成恒等式证明。
  只有 `verify_identity` 的 `proven` 才是。
- **回避反例**：工具报 `refuted` 时，正确反应是改命题或改步骤，不是换个写法重试。
- **引理空转**：登记了引理却从不用它证明任何东西，或用了却没证。
  审计会把两者都列为问题。
