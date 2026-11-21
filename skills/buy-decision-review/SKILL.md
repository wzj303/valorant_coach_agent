---
name: buy-decision-review
description: 复盘 VALORANT 购买阶段的决策。输入是购买阶段截图（记分板/商店/装备栏），输出双方经济、购买类型、团队协调和替代方案的评价。用户问"这回合该不该起枪""这波购买对不对""经济怎么样"时使用。
task_types: [buy_review]
triggers: [购买, 买枪, 起枪, 半起, 全起, eco, 经济, 金币, 护甲, 技能购买, buy, economy, credits]
allowed_tools: [validate_round_data, get_game_rules, analyze_buy_state, simulate_buy_options, lookup_tactics]
---

# 购买决策复盘

目标：基于**购买当时可获得的信息**评价购买决策。回合结果只能作为事后验证，不能替代事前评价。

## 工作流程

1. **观察截图**。只记录截图里能看到的内容。没看清的字段一律填 `null`，不要补默认值，不要从常识里猜。
2. **生成 RoundSnapshot**，按下面的"填写规则"填写，附上每个字段的证据。
3. **调用 `validate_round_data`**，参数 `{"round_snapshot": {...}}`。
   - `status = failed`：按 `errors` 的字段路径修正后重试，最多两次。仍失败就停止业务计算，在 `missing_information` 里说明原因。
   - `status = partial`：可以继续，但把 `missing_fields` 和 `warnings` 带进最终报告，并降低结论强度。
   - 之后的工具一律使用返回的 `data.normalized_snapshot`，不要再用自己手写的版本。
4. **调用 `get_game_rules`**（需要价格、连败奖励等规则时）。`game_patch` 必须来自截图或用户，**不得自己编一个版本号**。工具返回不支持该版本或规则未验证时，如实写进报告，不要改用记忆里的价格。
5. **调用 `analyze_buy_state`**，拿到双方经济、武器/护甲分布、购买类型、团队协调度。所有经济数字都取自这个结果。
6. **必要时调用 `simulate_buy_options`**，比较 `full_buy` / `half_buy` / `eco`。只有存在明确的疑点（花钱过多、团队不一致、影响下一回合经济）时才需要。
7. **必要时调用 `lookup_tactics`**，把购买放进 Sunset 的攻防情境里评价。
8. 综合以上结果写报告。

## 填写规则（RoundSnapshot）

- 队内槽位 `slot` 取 1–5，`ally_players` 和 `enemy_players` 各最多 5 人，`team` 要与所在数组一致。
- `credits_before_buy` 只填**能确认是购买前**的余额。能读出数额但分不清购买前还是购买后，放进 `credits_unclassified`，两个字段不要同时填同一个数。
- `credits_remaining` 是购买后的余额。不要把两者混用。
- `armor` 只能是 `none` / `light` / `heavy` / `regen`。空武器栏确认为空时写 `"none"`，没看清写 `null`。
- `abilities`：`true` 已买，`false` 确认没买，`null` 未知。没看清的技能写 `null`，**不要写成 false**。
- `role` 由英雄推导时，证据必须标 `inferred`、来源 `model_inference`。
- 敌方经济多半是估算，证据必须标 `inferred`。
- `map_id` 固定 `sunset`。`map_revision`、`game_patch` 截图里没有就填 `null`。
- 每个填了值的字段加一条证据 `{field_name, status, source, confidence?}`：
  - 直接看到：`observed` + `screenshot`
  - 推测：`inferred` + `screenshot` 或 `model_inference`
  - 用户明确告知：`user_confirmed` + `user_input`
  - 字段为 `null` 时，证据只能是 `status: unknown`，且不带 `confidence`
- 玩家级证据的 `field_name` 相对该玩家，如 `weapon`、`abilities.Flash`。回合级证据用 `map_revision`、`score.ally` 这类名称。
- 用户给出的数据优先于你的读图结果。冲突时保留用户的值，并在报告里点明。

## 分析内容

- 双方经济差异，并区分完整统计和部分统计。缺失的金币不是 0。
- 团队购买类型，和队内是否协调（有人全起有人 eco 要指出）。
- 武器、护甲、技能的配置是否匹配当前战术目标。
- 是否有更合理的替代方案（引用 `simulate_buy_options` 的结果）。
- 对下一回合经济的影响。

## 写报告时

- `buy_review` 写购买决策的结论和理由。没有做站位分析时 `position_review` 留空。
- 每条 `finding` 必须引用 `evidence` 里的条目，说明依据来自截图、用户还是工具。
- 一个回合最多三条建议，按重要性排序。
- 数据不足（`partial`、缺金币、规则版本未验证）时，用"可能""需要确认"这类措辞，并把缺口写进 `missing_information`。
- 敌方数字是估算时，直接写"估算"。
