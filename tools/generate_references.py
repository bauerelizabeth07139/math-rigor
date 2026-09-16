"""Generate the skill's reference files from the live implementation.

Anything documented here is read back out of the running toolkit, so the
reference cannot drift away from what the code actually does.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))

from rigor.logic import RULES, NON_LOGICAL_RULES  # noqa: E402
from rigor.proof import ENTAILMENT_REQUIRED, DISCHARGING_RULES, PREMISELESS_OK  # noqa: E402

SKILL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..",
                     "skills", "math-rigor", "references")
SKILL = os.path.abspath(SKILL)
os.makedirs(SKILL, exist_ok=True)

CATEGORY_TITLES = {
    "propositional": "命题逻辑规则",
    "predicate": "谓词逻辑规则",
    "equality": "等式规则",
}

lines: list[str] = []
lines.append("# 推理规则与理由参考")
lines.append("")
lines.append("本文件由工具注册表自动生成，与 `math-rigor` 服务器的实际行为一致。")
lines.append("")
lines.append("每一条逻辑规则都会做**两项**检查：")
lines.append("")
lines.append("1. **形状（shape）**——前提与结论是否构成该规则的模式实例；")
lines.append("2. **语义（semantics）**——前提是否真的语义蕴含结论（交给 SMT 求解）。")
lines.append("")
lines.append("两项都通过才是 `valid`。形状不符报 `invalid_shape`，语义不成立报 `invalid_semantics`。")
lines.append("")
lines.append("## 规则总表")
lines.append("")
lines.append("| 规则名 | 类别 | 语义自足 | 说明 |")
lines.append("|---|---|---|---|")
for name, rule in sorted(RULES.items()):
    lines.append(
        f"| `{name}` | {CATEGORY_TITLES.get(rule.category, rule.category)} | "
        f"{'是' if rule.semantically_valid else '否（靠副作用条件）'} | {rule.description} |"
    )
lines.append("")

for category, title in CATEGORY_TITLES.items():
    members = {name: rule for name, rule in sorted(RULES.items()) if rule.category == category}
    if not members:
        continue
    lines.append(f"## {title}")
    lines.append("")
    for name, rule in members.items():
        lines.append(f"### `{name}`")
        lines.append("")
        lines.append(rule.description)
        lines.append("")
        lines.append(f"- 例子：`{rule.example}`")
        if rule.semantically_valid:
            lines.append("- 语义自足：是。形状匹配后还会用 SMT 复核前提是否蕴含结论。")
        else:
            lines.append(
                "- 语义自足：**否**。这类规则只在副作用条件（新鲜性）下可靠，"
                "工具会检查该条件，而不会去做语义蕴含检查。"
            )
        lines.append("")

lines.append("## 会解除假设的规则")
lines.append("")
lines.append("引用了某个 `assumption` 步骤的这些规则，会把该假设标记为已解除：")
lines.append("")
for name in sorted(DISCHARGING_RULES):
    lines.append(f"- `{name}`")
lines.append("")
lines.append("未被解除的假设会被审计列为 `flawed`。")
lines.append("")

lines.append("## 非逻辑理由")
lines.append("")
lines.append("这些不是形式化推理规则，而是数学写作中的正当理由。审计的处理方式：")
lines.append("")
lines.append("- 先用被引用的前提尝试**自动重推**这一步；推得出来就升级为 `verified`；")
lines.append("- 推不出来就记为 `unchecked`，并保留 `justification` 供人工复核。")
lines.append("")
if ENTAILMENT_REQUIRED:
    lines.append(
        "以下理由**必须**从被引用的前提推出，否则判为 `refuted`（并附反例）："
        + "、".join(f"`{name}`" for name in sorted(ENTAILMENT_REQUIRED))
        + "。"
    )
    lines.append("")
lines.append("| 理由名 | 含义 |")
lines.append("|---|---|")
for name, description in sorted(NON_LOGICAL_RULES.items()):
    lines.append(f"| `{name}` | {description} |")
lines.append("")
lines.append("其中以下理由允许不引用任何前提：")
lines.append("")
lines.append("、".join(f"`{name}`" for name in sorted(PREMISELESS_OK)))
lines.append("")

text = "\n".join(lines) + "\n"
with open(os.path.join(SKILL, "inference-rules.md"), "w", encoding="utf-8") as handle:
    handle.write(text)
print(f"wrote inference-rules.md ({len(text)} chars, {len(RULES)} rules)")
