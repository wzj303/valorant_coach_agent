---
name: position-coordination-review
description: 复盘基础局内站位和团队配合。输入是小地图截图，输出人员分布、孤立站位、潜在支援关系和阵容适配度的评价。用户问"站位有没有问题""大家是不是太散""配合怎么样"时使用。
task_types: [position_review]
triggers: [站位, 小地图, 配合, 支援, 孤立, 分散, 控图, 区域, position, minimap, rotate, spacing]
allowed_tools: [validate_round_data, analyze_positions, lookup_tactics]
---

# 站位与团队配合复盘

目标：根据一张或几张小地图截图，指出站位分布和配合上的问题。单张截图只能说明**那一刻**的分布。

## 硬性限制

- 区域里的人数**不等于**该区域的控制权。不要写"我方控制了 B 中路"这类话。
- 不能从一张静态截图推断移动轨迹。只有多个时间点才能谈位置变化，而且只比较有记录的玩家。
- 不要推断没有看到的敌方位置。看不到的敌人就是未知。
- 没有可靠坐标就不要估距离。只有区域名称时，只分析区域分布。

## 工作流程

1. **观察小地图**，列出每个可见玩家：队伍、槽位、英雄（能识别时）、所在区域，以及坐标（只有能可靠读出时）。
2. **生成 PositionSnapshot**，按下面的规则填写。
3. **调用 `validate_round_data`**，参数 `{"position_snapshot": {...}}`。如果同时有该回合的 RoundSnapshot，一起传，工具会检查回合和地图修订是否一致。
   - `failed`：按字段路径修正，最多重试两次。仍失败就停止站位计算，并在 `missing_information` 说明。
   - `partial`：继续，带上 `warnings`。
4. **调用 `analyze_positions`**，参数使用 `normalized_position_snapshot`（只传了站位时是 `normalized_snapshot`）。多个时间点按 `timestamp_ms` 放进 `position_snapshots`。
5. **必要时调用 `lookup_tactics`**，查 Sunset 对应区域、英雄职责、默认站位的知识，注意 `side` 和 `map_revision` 要与快照一致。
6. 把工具结果、英雄职责和地图结构放在一起写复盘。

## 填写规则（PositionSnapshot）

- `map_id` 固定 `sunset`。`map_revision` 看不出来就填 `null`。
- `coordinate_system`：用归一化坐标写 `normalized`（x、y 都在 0–1），用像素写 `pixel`，只有区域名写 `region_only`。**有数字坐标就必须声明坐标系。**
- 看不出位置的玩家：`status: "unknown"`，不要填 `region` / `x` / `y`，也不要填 `confidence`。**不要为未知位置编造坐标。**
- 已知位置的玩家，`status` 填 `observed` 或 `inferred`，且至少有 `region` 或坐标之一。
- 证据的 `field_name` 形如 `players.ally.1.region`、`players.enemy.2.x`、`timestamp_ms`。
- `snapshot_id` 在同一次分析里唯一，`round_id` 与对应回合一致。

## 分析内容

- 人员分布与区域人数（只作分布描述）。
- 人员间距，前提是有可靠坐标。
- 潜在支援关系：谁离谁近到可能互相支援。只说"潜在"。
- 孤立站位：离队友都很远的玩家。
- 阵容与站位的适配：英雄职责（突破、先锋、控场、哨卫）是否站在合适的区域。
- 潜在的协同问题，并给出判断依据和不确定性。

## 写报告时

- `position_review` 写站位结论。没有做购买分析时 `buy_review` 留空。
- 每条 `finding` 引用 `evidence` 里的条目。
- 最多三条建议。
- 只有单个时间点时，在 `missing_information` 里写明"无法判断移动变化"。
- 玩家只识别出一部分时，说明样本不完整，结论降级。
