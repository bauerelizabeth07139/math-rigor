# 输入记法完整约定

解析器接受三种写法混用：**纯数学**、**LaTeX**、**sympy 风格函数调用**。
下面每一条都经过实际测试。

## 逻辑连接词

| 含义 | 可接受的写法 |
|---|---|
| 合取 | `&`、`&&`、`∧`、`and` |
| 析取 | `\|\|`、`∨`、`or`、以及两侧为命题时的单个 `\|` |
| 否定 | `~`、`!`、`¬`、`not` |
| 蕴含 | `->`、`=>`、`→`、`==>`、`-->` |
| 等价 | `<->`、`<=>`、`↔`、`iff` |
| 矛盾 / 恒真 | `falsum`、`false`、`⊥` / `verum`、`true`、`⊤` |

优先级（由低到高）：`<->` → `->` → `∨` → `∧` → `~` → 比较 → `+ -` → `* / %` → 幂。

蕴含是**右结合**：`P -> Q -> R` 等于 `P -> (Q -> R)`。

### `|` 的三重含义及消解规则

单个 `|` 在数学写作里有三种意思，工具按下面的顺序消解：

1. **绝对值**：`|` 出现在表达式起始位置（或紧跟运算符、逗号、左括号）时，
   与其配对的 `|` 之间是绝对值。`|x| + 1`、`|x|` 都正确。
2. **析取**：两侧都是"命题形状"时——标识符以**大写字母开头**（`P | Q`、`P | ~Q`、
   `P(x) | Q(y)`）——当析取。
3. **整除**：其余情形当整除。`6 | n`、`3 | n^3 - n`、`n | m` 都当整除。

拿不准时用无歧义写法：

- 析取写 `||` 或 `or`：`P || Q`
- 整除写 `divides(6, n)` 或 `n % 6 = 0`

`∣`（U+2223）永远表示整除，`∤`（U+2225）表示不整除。

> 这条规则来自数学书写惯例（命题用大写、数用小写）。如果你的谓词名小写，
> 例如 `f(x) | g(x)`，会被读成整除——请改用 `||`。

## 幂与乘法

- `x^2` 与 `x**2` 等价，都是乘方。**`^` 不是异或，也不是合取。**
- 幂右结合：`2^3^2` = `2^(3^2)` = 512。
- 一元负号优先级低于幂：`-x^2` = `-(x^2)`。
- **邻接即乘法**：`2x`、`n(n+1)`、`(x+1)(x-1)`、`2(x+1)` 都是乘积。
- 取整幂：`\sqrt[3]{x}`、`x^(1/3)`。

### 函数调用与乘法的歧义

`f(x)` 和 `n(n+1)` 在裸记法里结构相同。判定规则：

- 内建函数名（`sin`、`cos`、`exp`、`log`、`sqrt`、`abs`、`min`、`max`、`gcd`、
  `lcm`、`floor`、`ceil`、`Sum`、`Product`、`even`、`odd`、`divisible`、
  `divides`、`binomial`、`factorial`、`Abs` 等）**永远是函数调用**；
- 其他名字，若**每个参数都是简单项**（变量、数、函数调用），当函数调用——
  于是 `P(x)`、`Q(x,y)`、`isPrime(n)` 都是谓词；
- 否则当乘法——于是 `n(n+1)`、`a(x-1)`、`f(x+1)`（f 未声明时）是乘积。

**要让 `f(x+1)` 当函数调用，把 `f` 放进 `functions` 参数。** 反之也可以直接用 `*`
把乘法写明。

## 关系与集合

| 含义 | 写法 |
|---|---|
| 相等 | `=`、`==` |
| 不等 | `!=`、`≠` |
| 序 | `<`、`<=`、`>`、`>=`、`≤`、`≥` |
| 整除 | `d \| n`、`divides(d, n)`、`n % d = 0`、`even(n)`、`odd(n)` |
| 链式不等式 | `a < b < c` 展开为 `a < b & b < c` |
| 集合 | `Z`、`N`、`R`、`Q`、`\mathbb{Z}`、`\mathbb{R}` 等 |

## 量词

```
forall n in Z: 6 | n^3 - n
forall x, y in R: (x+y)^2 = x^2 + 2*x*y + y^2
exists n in Z: n^2 = 4
\forall x \in \mathbb{R}: x^2 \geq 0
forall n: 6 | n^3 - n            # 未写集合，由公式推断（含取模/整除 ⇒ 整数）
```

未显式给集合时，工具按公式推断：出现 `%`、`|`、`even`、`odd`、`divisible`
⇒ 整数；出现小数 ⇒ 实数；否则实数。**推断结果会在返回的 `sorts_used` 里说明**，
所以定义域从不偷偷决定。

更好的做法是显式声明。在 `proof_start` 里用 `variables`，
在一次性调用里用 `variables={"n": "int"}`。

## 求和与乘积

```
Sum(k, (k, 1, n))           # 求和 k 从 1 到 n
Product(i, (i, 1, n))
\prod_{i=1}^{n} i
\sum_{k=1}^{n} k^2
Sum(k^2, (k, 1, n)) = n*(n+1)*(2*n+1)/6
```

符号求和会先用 sympy 求闭形式。若闭形式求不出来，会明确说明"需要显式给出闭形式"，
而不是假装成功。

## 假设的写法

`assumptions` 接受两类字符串，可以混在一个列表里：

- **符号性质**：`"x: positive"`、`"n: integer"`、`"y: nonzero"`、`"n: natural"`、
  `"k: even"`
  （支持 `positive`、`negative`、`nonnegative`、`nonpositive`、`nonzero`、`real`、
  `integer`、`rational`、`complex`、`natural`、`even`、`odd`）
- **逻辑约束**：`"x > 0"`、`"n >= 1"`、`"a != b"`

区别很重要：符号性质会建到 sympy 的 Symbol 上（让化简能做对），
逻辑约束会变成命题里的前提。

例：

```json
{"lhs": "sqrt(x^2)", "rhs": "x", "assumptions": ["x: positive"]}
{"lhs": "x + 1/x", "relation": ">=", "rhs": "2",
 "variables": {"x": "real"}, "constraints": ["x > 0"]}
```

## 常量与函数

- 常量：`pi`、`e`（自然常数）、`oo`（无穷）、`I`（虚数单位）。
  **注意**：`pi` 与 `e` 在符号层（`symbolic_eval`、`verify_limit`、`verify_identity`）被识别为
  数学常数；若要把它们当普通变量用，在 `variables` / `variable` 里显式声明即可覆盖。
  SMT 层（`verify_forall`、`logic_entails`、`find_counterexample`）没有精确的超越常数，
  遇到 `pi` / `e` 会明确报错并提示改用符号工具，而不是把它当自由变量默默处理。
- 反三角：`asin`、`acos`、`atan`
- 双曲：`sinh`、`cosh`、`tanh`
- 其他：`exp`、`ln`、`log`（双参数 `log(x, b)` 表以 b 为底）、`sqrt`、`abs`、
  `floor`、`ceil`、`sign`、`min`、`max`、`gcd`、`lcm`、`binomial`、`factorial`
- LaTeX 简写：`\frac{a}{b}`、`\sqrt{a}`、`\sqrt[n]{a}`、`\cdot`、`\times`、
  `\le`、`\ge`、`\neq`、`\land`、`\lor`、`\lnot`、`\to`、`\iff`、`\infty`
- 希腊字母：`\alpha` … `\omega`、`\Gamma` … `\Omega`

## 常见坑

| 写法 | 实际含义 | 建议 |
|---|---|---|
| `n is an integer` | 不是公式，会被读成乘积 `n*is*an*integer` | 用 `variables={"n": "int"}` |
| `P ^ Q` | `P` 的 `Q` 次幂 | 合取用 `P & Q` |
| `P \| Q`（P 小写） | 整除 | 析取用 `P \|\| Q` |
| `n(n+1)` | 乘积（正确） | 无需修改 |
| `f(x+1)` | 乘积（f 未声明） | `functions=["f"]` |
| `3 \| n^3-n` 中的空格 | 无关 | 空格随意，不影响 |
| `x = 2`（作前提） | 等式，可用于 `algebra` 重写 | 正确用法 |
| `n in Z`（作前提） | 不是公式，会报错并提示放进 `variables` | 用 `variables` |
