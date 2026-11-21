---
name: coach-feedback
description: 汇总整场比赛的教练反馈。输入是多个回合的快照、单回合分析结果和用户补充信息，输出主要优点、反复出现的问题和最重要的三个改进方向。用户问"整场打得怎么样""帮我做赛后总结""下场该练什么"时使用。
task_types: [match_review]
triggers: [整场, 赛后, 总结, 总体, 复盘整场, 训练建议, 改进, match review, summary, overall]
allowed_tools: [validate_round_data, analyze_buy_state, simulate_buy_options, get_game_rules, lookup_tactics, analyze_positions]
---

# 整场比赛教练反馈

目标：把多个回合的信息合成一份有优先级的教练反馈。

## 输入

- 多回合 RoundSnapshot / PositionSnapshot（可能在对话上下文里，也可能只有截图）。
- 此前的单回合分析结果（`previous_analysis`）。
- 用户补充的比赛信息。

## 工作流程

1. 先盘点手上有哪些回合，每回合是否有购买信息、站位信息，各自的数据质量如何。
2. **已有分析结果的回合直接引用**，不要重新分析，更不要改动它的数字。
3. 只有缺少关键数字、而这个数字应当来自工具时，才补调用工具：
   - 经济和装备数字 → `analyze_buy_state`
   - 站位 → `analyze_positions`
   - 补调用前先用 `validate_round_data` 校验快照。
4. 找**重复出现**的模式，而不是罗列每个回合。
5. 写出优点、问题和三个改进方向。

## 判断规则

- 只出现一次的问题叫"本场出现的问题"，不叫"反复出现"。至少两个回合支持才算重复。
- 样本只有一两个回合时，不要下"整场"层面的结论，只说"已分析的回合显示……"。
- 不要只看回合胜负评价决策。赢下的回合可能买得不合理，输掉的回合也可能决策正确。
- 经济数字只能引用工具结果或之前的分析，不能自己重新算。
- 购买问题和站位问题分开写，不要混在一起。

## 写报告时

CoachResponse 的对应关系：

- 主要优点 → `findings`，`severity: "info"`，标题以"优点："开头。
- 反复出现的问题 → `findings`，按影响选 `low` / `medium` / `high`，标题写明涉及的回合。
- 购买决策总结 → `buy_review`。站位和协同总结 → `position_review`。
- 最重要的三个改进方向 → `recommendations`（最多三条，`priority` 为 1、2、3）。
- 下一场的训练建议 → 写进相应 recommendation 的 `action`，并在 `coach_summary` 里点出。
- 涉及多回合的 `evidence` 条目，`field_name` 里带上 `round_id`，例如 `r3.analyze_buy_state.buy_type`。
- 哪些回合没有分析、哪些数据缺失，写进 `missing_information`。
