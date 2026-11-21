# valorant_coach_agent

瓦洛兰特战术复盘教练。

## GPT-3 Tactical Engine

当前实现位于 `feature/gpt3-tactical-engine`，提供五个可独立调用的 Mapping-based Tool 入口：

- `tools.domain.analyze_buy_state`
- `tools.domain.simulate_buy_options`
- `tools.domain.get_game_rules`
- `tools.domain.lookup_tactics`
- `tools.domain.analyze_positions`

Service 层负责经济分析、购买方案、规则查询、知识检索、地图查询和区域级站位分析。Tool 只负责输入校验、Service 组织和统一结果封装。所有结果包含 `status`、`data`、`unknown` 和 `metadata` 字段，便于后续接入 GPT-2 的 `ToolResult`。

### 当前运行方式

```powershell
python -m unittest discover -s tests/tactical_engine -v
```

开发依赖列在 [requirements-dev.txt](requirements-dev.txt)，pytest 配置位于 [pyproject.toml](pyproject.toml)。当前环境可使用标准库 unittest 运行测试，安装 pytest 后也可执行 `python -m pytest`。

### 数据与边界

本地规则和 Sunset 数据位于 [knowledge](knowledge/)。当前数据明确标记为 `unverified`，价格、奖励、坐标和拓扑关系为未知值。系统不会把未知值静默转换为零，也不会在未知价格下生成确定的可行购买结论。

GPT-3 不创建或替代 `schemas/`、`agent/`、`skills/` 和 `validate_round_data.py`。待 GPT-2 Schema 发布后，再增加适配层并冻结最终 Tool 契约。
