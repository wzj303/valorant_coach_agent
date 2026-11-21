# 数据字段与判定规则 · 1.0.0

## 共同约定

JSON 使用 snake_case。必填 ID 为去除首尾空白的非空字符串。整数按严格类型校验，字符串数字、布尔值和浮点金币均会报错。输入对象拒绝未声明字段。未知观察用 null。数组中的玩家均为已建立记录的对象，校验器只在缺失清单里报告空槽。

## 比赛与回合

| 字段 | 含义与取值 |
|---|---|
| match_id / round_id / round_index | 比赛键、回合键、从 1 起算的回合编号。三者必填。 |
| map_id / map_revision | V0 地图枚举 sunset。修订键必填，可以显式为 null；文本 unknown 标准化为 null。 |
| game_patch | 游戏规则版本；未识别时为 null。由规则提供方识别支持的版本。 |
| side / round_phase | 己方 attack、defense；阶段 buy、live、post_round。 |
| score | ally 和 enemy 分别是己方与对方的非负整数比分，可分别未知。 |
| ally_players / enemy_players | 每方最多五条 PlayerSnapshot。槽号在队内唯一，数组归属与 team 必须一致。 |
| ally_loss_streak / enemy_loss_streak | 各队截至本回合的已知连败次数，非负整数。 |
| data_quality | completeness、missing_fields、warnings。校验器重算。 |
| source_images / user_notes / evidence | 输入截图标识列表、用户补充文本列表、回合级字段证据。比分路径为 score.ally 或 score.enemy。 |

## 玩家与证据

| 字段 | 含义与取值 |
|---|---|
| team / slot | ally 或 enemy，队内记录号 1–5。用两者联合寻址。 |
| agent / role | 英雄名。职责取 duelist、initiator、controller、sentinel。从英雄映射职责时提供 inferred 证据。 |
| credits_before_buy / credits_remaining | 购买前余额、购买后剩余余额。值域为非负整数或 null。 |
| credits_unclassified | 可以读出数额但无法确定时点。案例 C 的 4199 放在这里。买枪预算只读 credits_before_buy。 |
| credit_exception_note | 剩余金币大于购买前金币时需要提供该玩家的具体说明，校验器仍会提示复核。 |
| weapon / secondary_weapon | 每种武器栏最多一个名称。无法识别时为 null。确认空栏可使用字符串 none。武器名称与具体补丁的对应由规则提供方核验。 |
| armor | none、light、heavy、regen；null 表示未知。 |
| abilities | 技能名到 true、false、null 的映射，分别表示确认已买、确认未买、未知。某键缺失表示未报告。 |
| evidence | 相对玩家字段名的一组 FieldEvidence。技能可按 abilities 或 abilities.技能名记录。每个目标字段有一条生效记录。 |

FieldEvidence.field_name 标识字段。status 表示 observed（直接观察）、inferred（推测）、user_confirmed（用户确认）、derived（工具推导）或 unknown。source 取 screenshot、user_input、tool、model_inference 或 unknown。confidence 是可空的 0–1 数值，未知状态只能为 null。note 可写截图标识、推导依据或用户原话。截图是推测的依据时也可填 screenshot，并使用 inferred 标记推测。确认必须来自 user_input。模型推断来源必须标记 inferred。apply_player_observation 将值与证据一起更新，用户确认优先级高于推测置信度。

## 站位、上下文和报告

| 对象 | 字段及含义 |
|---|---|
| PositionSnapshot | snapshot_id 标识单帧，round_id 关联回合，map_id / map_revision 标识地图。timestamp_ms 为相对回合开始的非负毫秒数。coordinate_system 取 normalized、pixel、region_only 或 null。players 保存观察记录，evidence 保存单帧字段或玩家字段来源。 |
| PlayerPosition | team、slot 标识玩家，agent 是可空英雄名，x / y 与 region 分别表示坐标和区域。normalized 每个轴在闭区间 [0,1] 内；pixel 为非负像素值。坐标原点约定是所用地图图像左上角，x 向右、y 向下。不同图像的像素不能直接比较，宿主需确保底图和坐标系相同。只知道区域可仅填写 region。status 描述位置来源状态，confidence 可空。未知位置保留空区域和空坐标。玩家证据路径示例为 players.ally.1.region。 |
| AnalysisContext | task_id 标识任务，task_type 取 buy_review、position_review、match_review。match_id 约束 round_snapshots 的比赛归属。position_snapshots 的每个 round_id 应对应 round_snapshots 中的唯一回合。user_observations 是用户补充文本，previous_analysis 保存此前 CoachResponse，data_quality 保存上下文质量。 |
| CoachResponse | task_id、task_status 与当前任务对应。overall_judgement、buy_review、position_review、coach_summary 为可展示文本。findings、evidence、recommendations、missing_information 分别列出判断、证据、建议和信息缺口。 |
| Finding | title、severity（info/low/medium/high）、explanation、evidence_refs、confidence。evidence_refs 引用本报告 evidence 中唯一的 field_name，跨回合时请包含 round_id。 |
| Recommendation | priority 是从 1 起的优先级，action 是操作，reason 是原因，expected_benefit 是预期效果。单份 CoachResponse 最多三条。 |
| MatchReport | match_id 标识比赛，analyzed_rounds 保存回合 ID 列表。strengths、recurring_problems 列出优点和反复问题，improvement_plan 保存建议，final_summary 总结整场。 |
| ToolResult[T] | tool_name、status、data、warnings、errors、evidence。各业务工具使用已绑定的结果类型作为 T。failed 可以返回 null 或诊断对象；partial 必须提供数据及告警。 |
| ValidationResult | valid 表示能否按本次模式使用，completeness 可空。missing_fields 使用可定位的字段或槽号，warnings 说明疑点，errors 说明失败原因。normalized_snapshot、normalized_position_snapshot 保存标准化快照。结构错误时不提供半成品快照。 |

## 完整度

- 回合上下文占 20%。map_revision、game_patch、side、round_phase、score.ally、score.enemy、ally_loss_streak、enemy_loss_streak 八项等权。0 分比分和 0 连败都是已知值。
- 玩家信息占 80%。双方共十个槽，每槽八项等权，依次为 agent、role、credits_before_buy、credits_remaining、weapon、secondary_weapon、armor、abilities。每个普通字段对总分贡献 0.01。某槽缺失扣去该槽全部权重。时点不明的金币不能提高两个余额字段的得分。
- abilities 按已报告的键计分，贡献为非 null 键数除以所报键数。空映射或 null 贡献 0。false 是有效观察。此项不核对具体补丁下的完整技能目录。
- 单帧的 map_revision、timestamp_ms、coordinate_system 共占 25%，三项等权。其余 75% 为有区域或完整坐标的记录数除以已报告的记录数，无记录时按 0 计。单帧只评价可见或明确列出的玩家。
- 同时提交两类快照时取两个分数的算术平均数。分数保留六位小数。证据覆盖和地图匹配单独形成告警。没有可靠输入时返回 null。
- 必填键遗漏、非法类型、重复槽位、冲突的关联键和越界坐标为 error。字段未读出、来源缺失、金币时点未定或缺少参考版本为 warning。unknown 用于字段层面无法判断的观测。缺失清单指向快照和队内槽位，例如 round_snapshot.ally_players[slot=1].credits_remaining。

## 六个工具的公共契约

| 工具 | 参数模型 | ToolResult.data |
|---|---|---|
| validate_round_data | ValidateRoundInput | ValidationResult |
| analyze_buy_state | BuyStateInput | BuyStateAnalysis |
| simulate_buy_options | BuySimulationInput | BuyPlanComparison |
| get_game_rules | GameRulesInput | GameRules |
| lookup_tactics | TacticsInput | TacticQueryResult |
| analyze_positions | PositionsInput | PositionAnalysis |

五个业务载荷的字段名、可空性、嵌套和枚举详见 schemas/tool_contracts.py 与发布的 JSON Schema。经济聚合用 known_players 标识覆盖数，known_credits 是所选 time_basis 的已知总和，estimated 保留推测属性。方案按玩家保存 loadout 和 ability_status。区域人数和孤立玩家均关联 snapshot_id，以便区分多帧。
