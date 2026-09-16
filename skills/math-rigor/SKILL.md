---
name: math-rigor
description: Use for any mathematical task that demands a rigorous, auditable proof or derivation - proving or disproving a theorem, verifying an identity or inequality, checking an induction, establishing divisibility, parity or bounds, auditing a proof for gaps, or deciding whether a mathematical claim is true. Also use when asked to 数学证明, 严格证明, 证明题, 推导, 验证恒等式, 验证不等式, 数学归纳法, 找反例, 审查证明是否有漏洞, 形式化一个命题, or to prove/show/derive/verify a mathematical statement or check someone else's proof with the math-rigor MCP tools. Do NOT use for routine arithmetic a calculator would settle, or for LaTeX formatting alone.
---

# 严格数学证明流程

本技能把一个数学论证变成**可检查的结构**，而不是一段读起来有道理的文字。

配套的 `math-rigor` MCP 服务器提供三层能力：**证明会话**（可引用的步骤图 + 审计）、
**逻辑**（推理规则的语法与语义双重校验）、**数学**（符号计算、SMT 验证、归纳检查、
反例搜索、数论）。核心工具是 `proof_start` / `proof_add_step` / `proof_validate`。

## 不可违背的规则

1. **绝不把"看起来对"当作证明。** 只有工具返回 `proven` 才能称为已证明。
   `inconclusive` 不是证明，是"没决定"。`unknown`、超时、采样没找到反例——都不是证明。
2. **每个证明步骤必须引用恰好一条规则和它用到的步骤 id。** 不允许"显然"、"易得"、
   "同理"这类无引用的步骤。
3. **禁止循环论证。** 步骤只能引用**更早**的步骤；审计会检测前向引用与环。
4. **每条临时假设必须被解除**（`conditional_proof`、`reductio`、`disjunction_elim`、
   `case_analysis`、`existential_instantiation`）。审计会列出未解除的假设。
5. **每个引理要么被证明，要么显式声明为 `assumed=true`** 并在最终结论中披露。
5. **先形式化，再计算。** 没写清量词与定义域就动手化简，等于没有证明。
6. **反例是硬证据，但不是证明。** 找到一个反例就足以否定命题；没找到反例什么也说明不了。
7. **如实报告覆盖度。** 审计的 `sound_with_gaps` 判定就是为此存在的：结构没问题、
   没有被推翻的步骤，但有些步骤依赖人工论证。把这个区别写进最终报告，不要含糊成"已证明"。

## 六个阶段

按顺序执行。不要跳过形式化阶段。

### 阶段 1 — 形式化 `proof_start`

调用 `proof_start(problem, goal, kind, variables, functions, strategy)`。

- `goal` 必须是**精确的命题**，量词与定义域写全。`n^3-n 能被 6 整除` 不是命题；
  `forall n in Z: 6 | n^3 - n` 才是。
- `variables` 声明每个符号的定义域，例如 `{"n": "int", "x": "real"}`。
  支持 `int`、`nat`、`real`、`rat`。**定义域声明必须放在这里，不能当成步骤或前提。**
- 自定义的函数/谓词名放进 `functions`，否则 `f(x+1)` 会被当成乘法 `f*(x+1)`。
- 解题前先做三项检查，把结果记下来：
  - **含糊处**：题目里有没有未定义的符号、隐含的假设、正负号/零的情形？
  - **边界**：定义域端点、分母为零、空集、退化情形。
  - **真伪**：拿不准就先用 `find_counterexample` 探一下，别花力气证一个假命题。

### 阶段 2 — 策略 `strategy`

明确写出策略名，不要含糊地说"直接证明"。可选策略与选择依据见
`references/strategies.md`。把需要的引理在阶段 3 一次性登记。

### 阶段 3 — 分解 `proof_add_lemma`

用 `proof_add_lemma(session_id, name, statement, note, assumed)` 登记每个引理。
引理是**义务**，不是结论。之后用 `proof_add_step(..., proves_lemma=lemma_id)` 把它证掉。

### 阶段 4 — 逐步证明 `proof_add_step`

每一步调用一次 `proof_add_step(session_id, statement, rule, from_ids, justification)`，
并**立刻读它的返回**：

| 返回的 verdict | 含义 | 你该做什么 |
|---|---|---|
| `verified` | 已机器验证 | 继续 |
| `refuted` | 该步骤不成立，附反例 | **改掉这一步**，不要绕过 |
| `invalid` | 规则引用或语句形状不对 | 改规则或改语句 |
| `unchecked` | 依赖人工论证 | 设法改成可检查的引用；否则留待阶段 6 披露 |

选择规则的依据见 `references/inference-rules.md`。
**代数化简用 `algebra`、数值计算用 `arithmetic`**，并引用该变形实际用到的前提——
审计会用它引用的前提重新推导这一步，推不出来就报告反例。

### 阶段 5 — 审计 `proof_validate`

`proof_validate(session_id)` 返回：

- `verdict`：`verified` / `sound_with_gaps` / `flawed`
- `machine_coverage`：已验证、未检查、被推翻、无效的步骤数
- `flaws`：必须解决的问题（被推翻的步骤、无效引用、未解除假设、未证明引理、目标未导出）
- `next_actions`：下一步该做什么

**只有 `verified` 才能说"证明完成"。** 是 `sound_with_gaps` 就必须把未检查的步骤
逐条列出来。是 `flawed` 就必须回到阶段 4。

### 阶段 6 — 报告 `proof_export`

`proof_export(session_id, format)`，`format` 取 `markdown`、`latex` 或 `json`。
最终回答里必须清楚区分：

- 哪些结论由机器验证（引用了哪个工具、返回什么）
- 哪些步骤依赖人工论证（`unchecked` 列表）
- 是否使用了未证明的假设（`assumed` 的引理、题目给定的前提）

## 该用哪个工具

| 你要判断的事 | 工具 |
|---|---|
| 两个表达式是不是同一个函数 | `verify_identity` |
| 不等式在整个定义域上成立 | `verify_inequality`（必须给 `variables` 和 `constraints`） |
| 任何带量词的命题（整除、奇偶、界、逻辑） | `verify_forall` |
| 归纳法（基例 + 归纳步） | `verify_induction` |
| 极限值 | `verify_limit` |
| 命题是不是假的（找反例） | `find_counterexample` |
| 求导/积分/求和闭形式/解方程/化简 | `symbolic_eval` |
| 高精度数值 + 是否精确有理数 | `symbolic_numeric` |
| 整除、素数、最大公约数、贝祖系数、同余 | `number_theory` |
| 单步推理是否合规 | `logic_check_step` |
| 前提能否推出结论 | `logic_entails` |
| 纯命题逻辑的完全枚举 | `logic_truth_table` |
| 把表达式规范化成 LaTeX/纯文本 | `expr_normalise` |

### 结果语义（务必分清）

- `proven`：否定式不可满足。可以当结论用。
- `refuted`：给出了具体反例。命题是假的，必须改写。
- `inconclusive`：求解器没决定，且没找到反例。**不是证明。** 换路子或如实披露。
- `status: unknown`（`logic_entails` 等）：同上。

## 输入记法约定

解析器接受纯数学、LaTeX 和 sympy 风格三种写法。几个必须知道的约定：

- **连接词**：`&` 或 `and` 表合取；`||`、`or` 或 `v` 表析取；`~` 或 `not` 表否定；
  `->` 表蕴含；`<->` 表等价。
- **单个 `|` 是歧义的，按数学书写惯例消解**：两侧是命题（大写字母开头，如 `P | Q`）
  当析取；两侧是数（`6 | n`、`n | m`）当整除。
  拿不准就用 `||` 表析取、用 `divides(6, n)` 或 `n % 6 = 0` 表整除。
- **绝对值和整除**：`|x|` 是绝对值；`a ∣ b`（U+2223）一定是整除。
- **邻接即乘法**：`n(n+1)` 是乘积，不是函数调用。要让 `f(x+1)` 当函数调用，
  把 `f` 放进 `functions`。谓词 `P(x)`、`Q(x,y)` 会自动当函数调用。
- **幂**：`^` 和 `**` 等价，都表乘方（**不是**异或，也**不是**合取）。
- **符号常量**：`pi`、`e`、`oo`（无穷）、`falsum`/`⊥`（矛盾）、`verum`/`⊤`。
- **量词**：`forall n in Z: ...`、`exists x in R: ...`，集合可写
  `Z N R Q` 或 `\mathbb{Z}` 等。多变量用逗号：`forall x, y in R: ...`。
- **求和**：`Sum(k, (k, 1, n))` 或 `\sum_{k=1}^{n} k`；乘积用 `Product` / `\prod`。
- **整除判定**：`6 | n`、`divides(6, n)`、`n % 6 = 0`、`even(n)`、`odd(n)` 都可以。
- **假设与定义域**：符号性质写在 `assumptions`，形如 `"x: positive"`；
  逻辑约束形如 `"x > 0"`。两者可以混在一个列表里。
- **反例存在性写法**：`exists k in Z: A = 6*k` 会被自动改写成 `6 | A` 以求可判定。

完整记法见 `references/notation.md`。

## 常见失败及对策

- **`inconclusive` + 非线性/超越函数**：SMT 对 `2^n`、`sqrt`、`sin` 这类符号幂与超越函数
  常常无法判定。对策：先登记成引理，用 `symbolic_eval` 求闭形式或单调性，再作为
  `machine_verified` 步骤引用并说明依据。
- **整除命题超时**：把写法从 `n^3-n = 6*k` 换成 `6 | n^3 - n`，后者会走按模数分情形，
  常常能立刻证出。
- **恒等式 `inconclusive`**：多半缺假设。`sqrt(x^2) = x` 需要 `assumptions=["x: positive"]`。
  把需要的定义域条件补上再试。
- **`algebra` 步骤被判 `refuted`**：说明这一步并不从它引用的前提推出。
  把该变形真正用到的那条等式/不等式加进 `from_ids`。
- **`unchecked` 太多**：检查是不是把定义域声明写成了步骤（应该放进 `variables`），
  或者引用了不该引的前提。用 `proof_workflow` 和 `logic_rules` 复核规则名。

## 参考文件

- `references/inference-rules.md` — 全部 22 条逻辑规则与 19 种非逻辑理由，含形状与例子
- `references/strategies.md` — 策略选择表与每种策略的落地写法
- `references/notation.md` — 输入记法的完整约定与陷阱
- `references/worked-examples.md` — 三个完整范例：整除（归纳）、不等式、以及一个被审计
  抓出错误的证明
