---
description: 按严格流程证明一个数学命题：形式化 → 策略 → 引理分解 → 逐步证明 → 机器审计 → 如实报告
argument-hint: <要证明的命题，例如：对每个整数 n，n^3-n 能被 6 整除>
skills: math-rigor
---

对下面的命题执行完整的严格证明流程。**必须实际调用 math-rigor MCP 工具**，
不要只在文字上描述流程。

命题：$ARGUMENTS

按以下六个阶段推进，每个阶段都在回答里明确交代做了什么、工具返回了什么：

**阶段 1 · 形式化**
- 调用 `proof_workflow` 对齐流程与规则词汇表。
- 用精确的数学语言重述命题：量词写全、定义域写全、所有符号的含义明确。
- 若命题写法含糊（未说明定义域、隐含了正性/非零假设、边界情形未定），
  **先把含糊处列出来并给出你的澄清选择**，不要默默补假设。
- 调用 `proof_start(problem, goal, kind, variables, functions, strategy)` 登记。
  定义域声明只能放进 `variables`，不能写成步骤或前提。

**阶段 2 · 策略**
从直接证明 / 反证 / 逆否 / 归纳 / 构造 / 分情形 / 不变量 / 极端原理 / 双计数 中
明确选出策略，并说明为什么它适合这个命题的形状。
若对命题真假没把握，先用 `find_counterexample` 探一次。

**阶段 3 · 引理分解**
把需要的中间结论用 `proof_add_lemma` 登记为义务。引理是待证事项，不是结论。

**阶段 4 · 逐步证明**
- 对每个可判定的子命题，选择正确的工具（见 skill 的"该用哪个工具"表）：
  `verify_identity` / `verify_inequality` / `verify_forall` / `verify_induction` /
  `verify_limit` / `symbolic_eval` / `number_theory`。
- 每一步用 `proof_add_step` 记录，引用恰好一条规则和它用到的 id，
  **然后读它的返回 verdict**：
  - `verified` 继续；
  - `refuted` 时必须改掉这一步（工具给了反例，不要绕过）；
  - `invalid` 时改规则或改语句形状；
  - `unchecked` 时设法改成可检查的引用，否则记下来待阶段 6 披露。
- 不允许出现"显然""易得""同理"这类没有引用的步骤。

**阶段 5 · 机器审计**
调用 `proof_validate`，逐条处理 `flaws` 与 `next_actions`，直到没有任何
被推翻 / 无效 / 未解除假设 / 未证明引理的条目。

**阶段 6 · 如实报告**
- 调用 `proof_export` 导出证明。
- 在最终回答里明确区分三类内容：
  1. **机器验证的结论**——哪个工具、返回什么状态、什么方法；
  2. **依赖人工论证的步骤**——即 `unchecked` 列表，逐条列出；
  3. **用到的假设**——题面前提、`assumed=true` 的引理、以及所有被解除的临时假设。
- 只有审计返回 `verified` 才能说"证明完成"。返回 `sound_with_gaps` 就必须
  把未检查的步骤讲清楚，不要含糊成"已证明"。
