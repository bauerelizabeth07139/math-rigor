# 推理规则与理由参考

本文件由工具注册表自动生成，与 `math-rigor` 服务器的实际行为一致。

每一条逻辑规则都会做**两项**检查：

1. **形状（shape）**——前提与结论是否构成该规则的模式实例；
2. **语义（semantics）**——前提是否真的语义蕴含结论（交给 SMT 求解）。

两项都通过才是 `valid`。形状不符报 `invalid_shape`，语义不成立报 `invalid_semantics`。

## 规则总表

| 规则名 | 类别 | 语义自足 | 说明 |
|---|---|---|---|
| `biconditional_elim` | 命题逻辑规则 | 是 | From phi <-> psi conclude phi -> psi (or psi -> phi). |
| `biconditional_intro` | 命题逻辑规则 | 是 | Prove both directions to conclude phi <-> psi. |
| `conditional_proof` | 命题逻辑规则 | 是 | Assume phi and derive psi, then conclude phi -> psi. |
| `conjunction_elim` | 命题逻辑规则 | 是 | From phi & psi conclude phi (or psi). |
| `conjunction_intro` | 命题逻辑规则 | 是 | From phi and psi conclude phi & psi. |
| `contradiction_intro` | 命题逻辑规则 | 是 | From phi and ~phi conclude the contradiction symbol. |
| `contraposition` | 命题逻辑规则 | 是 | From phi -> psi conclude ~psi -> ~phi. |
| `disjunction_elim` | 命题逻辑规则 | 是 | Proof by cases: from phi | psi, phi -> chi and psi -> chi conclude chi. |
| `disjunction_intro` | 命题逻辑规则 | 是 | From phi conclude phi | psi. |
| `disjunctive_syllogism` | 命题逻辑规则 | 是 | From phi | psi and ~phi conclude psi. |
| `double_negation` | 命题逻辑规则 | 是 | ~~phi and phi are interchangeable. |
| `equality_substitution` | 等式规则 | 是 | Replace equals by equals inside a cited formula. |
| `ex_falso` | 命题逻辑规则 | 是 | From a contradiction conclude anything (explosion). |
| `excluded_middle` | 命题逻辑规则 | 是 | phi | ~phi holds with no premises. |
| `existential_generalization` | 谓词逻辑规则 | 是 | From phi(t) conclude exists x. phi(x). |
| `existential_instantiation` | 谓词逻辑规则 | 否（靠副作用条件） | From exists x. phi(x) introduce a fresh witness c and conclude phi(c). Not semantically valid on its own: freshness is checked. |
| `hypothetical_syllogism` | 命题逻辑规则 | 是 | Chain two implications: phi -> psi, psi -> chi, hence phi -> chi. |
| `modus_ponens` | 命题逻辑规则 | 是 | From phi and phi -> psi conclude psi. |
| `modus_tollens` | 命题逻辑规则 | 是 | From phi -> psi and ~psi conclude ~phi. |
| `reductio` | 命题逻辑规则 | 是 | Assume ~phi, derive a contradiction, conclude phi. |
| `universal_generalization` | 谓词逻辑规则 | 否（靠副作用条件） | From phi(a) with a arbitrary conclude forall x. phi(x). Not semantically valid on its own: the freshness condition is checked. |
| `universal_instantiation` | 谓词逻辑规则 | 是 | From forall x. phi(x) conclude phi(t) for any term t. |

## 命题逻辑规则

### `biconditional_elim`

From phi <-> psi conclude phi -> psi (or psi -> phi).

- 例子：`premises: P <-> Q  =>  conclusion: P -> Q`
- 语义自足：是。形状匹配后还会用 SMT 复核前提是否蕴含结论。

### `biconditional_intro`

Prove both directions to conclude phi <-> psi.

- 例子：`premises: P -> Q, Q -> P  =>  conclusion: P <-> Q`
- 语义自足：是。形状匹配后还会用 SMT 复核前提是否蕴含结论。

### `conditional_proof`

Assume phi and derive psi, then conclude phi -> psi.

- 例子：`premises: P, Q  =>  conclusion: P -> Q`
- 语义自足：是。形状匹配后还会用 SMT 复核前提是否蕴含结论。

### `conjunction_elim`

From phi & psi conclude phi (or psi).

- 例子：`premises: P & Q  =>  conclusion: P`
- 语义自足：是。形状匹配后还会用 SMT 复核前提是否蕴含结论。

### `conjunction_intro`

From phi and psi conclude phi & psi.

- 例子：`premises: P, Q  =>  conclusion: P & Q`
- 语义自足：是。形状匹配后还会用 SMT 复核前提是否蕴含结论。

### `contradiction_intro`

From phi and ~phi conclude the contradiction symbol.

- 例子：`premises: P, ~P  =>  conclusion: falsum`
- 语义自足：是。形状匹配后还会用 SMT 复核前提是否蕴含结论。

### `contraposition`

From phi -> psi conclude ~psi -> ~phi.

- 例子：`premises: P -> Q  =>  conclusion: ~Q -> ~P`
- 语义自足：是。形状匹配后还会用 SMT 复核前提是否蕴含结论。

### `disjunction_elim`

Proof by cases: from phi | psi, phi -> chi and psi -> chi conclude chi.

- 例子：`premises: P | Q, P -> R, Q -> R  =>  conclusion: R`
- 语义自足：是。形状匹配后还会用 SMT 复核前提是否蕴含结论。

### `disjunction_intro`

From phi conclude phi | psi.

- 例子：`premises: P  =>  conclusion: P | Q`
- 语义自足：是。形状匹配后还会用 SMT 复核前提是否蕴含结论。

### `disjunctive_syllogism`

From phi | psi and ~phi conclude psi.

- 例子：`premises: P | Q, ~P  =>  conclusion: Q`
- 语义自足：是。形状匹配后还会用 SMT 复核前提是否蕴含结论。

### `double_negation`

~~phi and phi are interchangeable.

- 例子：`premises: ~~P  =>  conclusion: P`
- 语义自足：是。形状匹配后还会用 SMT 复核前提是否蕴含结论。

### `ex_falso`

From a contradiction conclude anything (explosion).

- 例子：`premises: falsum  =>  conclusion: Q`
- 语义自足：是。形状匹配后还会用 SMT 复核前提是否蕴含结论。

### `excluded_middle`

phi | ~phi holds with no premises.

- 例子：`conclusion: P | ~P`
- 语义自足：是。形状匹配后还会用 SMT 复核前提是否蕴含结论。

### `hypothetical_syllogism`

Chain two implications: phi -> psi, psi -> chi, hence phi -> chi.

- 例子：`premises: P -> Q, Q -> R  =>  conclusion: P -> R`
- 语义自足：是。形状匹配后还会用 SMT 复核前提是否蕴含结论。

### `modus_ponens`

From phi and phi -> psi conclude psi.

- 例子：`premises: P -> Q, P  =>  conclusion: Q`
- 语义自足：是。形状匹配后还会用 SMT 复核前提是否蕴含结论。

### `modus_tollens`

From phi -> psi and ~psi conclude ~phi.

- 例子：`premises: P -> Q, ~Q  =>  conclusion: ~P`
- 语义自足：是。形状匹配后还会用 SMT 复核前提是否蕴含结论。

### `reductio`

Assume ~phi, derive a contradiction, conclude phi.

- 例子：`premises: ~P, falsum  =>  conclusion: P`
- 语义自足：是。形状匹配后还会用 SMT 复核前提是否蕴含结论。

## 谓词逻辑规则

### `existential_generalization`

From phi(t) conclude exists x. phi(x).

- 例子：`premises: 2 > 1  =>  conclusion: exists n in Z: n > 1`
- 语义自足：是。形状匹配后还会用 SMT 复核前提是否蕴含结论。

### `existential_instantiation`

From exists x. phi(x) introduce a fresh witness c and conclude phi(c). Not semantically valid on its own: freshness is checked.

- 例子：`premises: exists n in Z: n^2 = 4  =>  conclusion: c^2 = 4`
- 语义自足：**否**。这类规则只在副作用条件（新鲜性）下可靠，工具会检查该条件，而不会去做语义蕴含检查。

### `universal_generalization`

From phi(a) with a arbitrary conclude forall x. phi(x). Not semantically valid on its own: the freshness condition is checked.

- 例子：`premises: a^2 >= 0  =>  conclusion: forall x in R: x^2 >= 0`
- 语义自足：**否**。这类规则只在副作用条件（新鲜性）下可靠，工具会检查该条件，而不会去做语义蕴含检查。

### `universal_instantiation`

From forall x. phi(x) conclude phi(t) for any term t.

- 例子：`premises: forall x: x^2 >= 0  =>  conclusion: 9 >= 0`
- 语义自足：是。形状匹配后还会用 SMT 复核前提是否蕴含结论。

## 等式规则

### `equality_substitution`

Replace equals by equals inside a cited formula.

- 例子：`premises: x = y, x + 1 > 0  =>  conclusion: y + 1 > 0`
- 语义自足：是。形状匹配后还会用 SMT 复核前提是否蕴含结论。

## 会解除假设的规则

引用了某个 `assumption` 步骤的这些规则，会把该假设标记为已解除：

- `case_analysis`
- `conditional_proof`
- `contradiction`
- `disjunction_elim`
- `existential_instantiation`
- `reductio`

未被解除的假设会被审计列为 `flawed`。

## 非逻辑理由

这些不是形式化推理规则，而是数学写作中的正当理由。审计的处理方式：

- 先用被引用的前提尝试**自动重推**这一步；推得出来就升级为 `verified`；
- 推不出来就记为 `unchecked`，并保留 `justification` 供人工复核。

以下理由**必须**从被引用的前提推出，否则判为 `refuted`（并附反例）：`algebra`、`arithmetic`、`computation`。

| 理由名 | 含义 |
|---|---|
| `algebra` | term rewriting justified by the field axioms |
| `arithmetic` | concrete arithmetic evaluation |
| `assumption` | an explicit hypothesis of the argument |
| `case_analysis` | a case distinction whose cases are cited as premises |
| `computation` | machine-verified evaluation (cite tool output) |
| `construction` | exhibiting an object to satisfy an existential |
| `contradiction` | alias of contradiction_intro / reductio depending on the shape |
| `counterexample_search` | refuted by an explicit counterexample (find_counterexample) |
| `definition` | unfolding a definition stated in the problem |
| `induction` | a verified induction (verify_induction): state the base case and the step |
| `induction_base` | the base case of an induction |
| `induction_step` | the inductive step of an induction |
| `lemma` | a lemma proved inside this session |
| `machine_verified` | checked by an external tool; quote the tool output in the justification |
| `premise` | a hypothesis taken from the problem statement |
| `smt` | decided by the SMT solver (verify_forall / logic_entails) |
| `symbolic_computation` | decided by symbolic computation (sympy_eval / verify_identity) |
| `theorem_citation` | a previously established theorem (name it in the justification) |
| `verify_inequality` | verified as a global inequality (verify_inequality) |

其中以下理由允许不引用任何前提：

`arithmetic`、`assumption`、`computation`、`construction`、`counterexample_search`、`definition`、`excluded_middle`、`induction_base`、`induction_step`、`lemma`、`machine_verified`、`premise`、`smt`、`symbolic_computation`、`theorem_citation`、`verify_inequality`

