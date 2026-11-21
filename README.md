# valorant_coach_agent

瓦洛兰特战术复盘教练。

多模态单 Agent：看截图、调用 Python 工具、输出结构化教练报告。地图 V0 仅 Sunset。

| 模块 | 负责 | 说明 |
|---|---|---|
| Agent 与 Skills（`agent/`、`skills/`、`tools/registry.py`） | GPT-1 | [README-agent1.md](README-agent1.md) |
| 数据契约与校验（`schemas/`、`tools/validate_round_data.py`） | GPT-2 | [README-agent2.md](README-agent2.md) |
| 战术工具与服务（`tools/domain/`、`services/`、`knowledge/`） | GPT-3 | 本 README 的 Tactical Engine 说明 |

快速体验（离线，无需密钥）：

```text
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m agent --demo --task buy_review --text "x" --trace
```

## GPT-3 Tactical Engine

提供五个可独立调用的 Mapping-based Tool 入口：

- `tools.domain.analyze_buy_state`
- `tools.domain.simulate_buy_options`
- `tools.domain.get_game_rules`
- `tools.domain.lookup_tactics`
- `tools.domain.analyze_positions`

Service 层负责经济分析、购买方案、规则查询、知识检索、地图查询和区域级站位分析。Tool 负责输入校验、Service 组织和统一结果封装。结果包含 `status`、`data`、`unknown` 和 `metadata` 字段，便于与 GPT-2 的 `ToolResult` 对接。

GPT-3 测试可单独执行：

```powershell
python -m unittest discover -s tests/tactical_engine -v
```

开发依赖列在 [requirements-dev.txt](requirements-dev.txt)。完整项目测试和 pytest 配置见 [pyproject.toml](pyproject.toml)。

本地规则与 Sunset 数据位于 [knowledge](knowledge/)。当前数据标记为 `unverified`，价格、奖励、坐标、拓扑关系和战术内容仍待核实。未知值会保留为未知，避免被当作零或确定事实。
