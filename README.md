# valorant_coach_agent

瓦洛兰特战术复盘教练。

多模态单 Agent：看截图、调用 Python 工具、输出结构化教练报告。地图 V0 仅 Sunset。

| 模块 | 负责 | 说明 |
|---|---|---|
| Agent 与 Skills（`agent/`、`skills/`、`tools/registry.py`） | GPT-1 | [README-agent1.md](README-agent1.md) |
| 数据契约与校验（`schemas/`、`tools/validate_round_data.py`） | GPT-2 | [README-agent2.md](README-agent2.md) |
| 战术工具与服务（`tools/domain/`、`services/`、`knowledge/`） | GPT-3 | 本 README 的 Tactical Engine 说明 |

## 运行 Demo

在仓库根目录使用 Python 3.11 或更新版本。以下为 Windows PowerShell 命令。

```text
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m agent --demo --trace
.venv\Scripts\python -m agent --demo --task position_review --trace
.venv\Scripts\python -m agent --demo --task match_review --trace
```

离线模式使用脚本选择工具，从校验器返回的快照调用战术引擎，再用工具结果生成教练报告。默认购买样例会依次经过数据校验、经济分析、规则查询和购买模拟。站位样例查询区域分布，整场样例对比两个回合。加 `--json` 可取得符合 CoachResponse 契约的 JSON。

也可以直接复盘自己的 RoundSnapshot 或 PositionSnapshot。下面的样例有双方各五人的完整阵容，己方购买前余额合计为 22500。

```powershell
.venv\Scripts\python -m agent --demo --snapshot tests/schemas/fixtures/complete_round.json --json
.venv\Scripts\python -m pytest -q
```

### 用真实截图运行

将 `.env.example` 复制为本地 `.env`，填写模型服务提供的 `VALORANT_LLM_API_KEY`、`VALORANT_LLM_MODEL`、`VALORANT_LLM_BASE_URL` 和对应的 `VALORANT_LLM_FORMAT`。模型需支持图像输入和工具调用。运行以下命令前把图片路径改为自己的截图。

```powershell
.venv\Scripts\python -m agent --check-llm
.venv\Scripts\python -m agent --task buy_review --image "round.png" --text "复盘这回合的购买" --trace
```

当前知识库的价格和地图修订尚待核验。工具仍能统计已观察的金币、装备和区域人数；依赖缺失价格或地图拓扑的判断会标记为 `partial` 并给出原因。

## GPT-3 Tactical Engine

提供五个可独立调用的 Mapping-based Tool 入口：

- `tools.domain.analyze_buy_state`
- `tools.domain.simulate_buy_options`
- `tools.domain.get_game_rules`
- `tools.domain.lookup_tactics`
- `tools.domain.analyze_positions`

Service 层负责经济分析、购买方案、规则查询、知识检索、地图查询和区域级站位分析。`tools/domain/contracts.py` 把通过公共 Schema 校验的参数传入上述入口，将计算结果、缺失项和来源整理为对应的 `ToolResult[T]`。注册表在把结果交还 Agent 前校验返回类型。

GPT-3 测试可单独执行：

```powershell
python -m unittest discover -s tests/tactical_engine -v
```

开发依赖列在 [requirements-dev.txt](requirements-dev.txt)。完整项目测试和 pytest 配置见 [pyproject.toml](pyproject.toml)。

本地规则与 Sunset 数据位于 [knowledge](knowledge/)。当前数据标记为 `unverified`，价格、奖励、坐标、拓扑关系和战术内容仍待核实。未知值会保留为未知，避免被当作零或确定事实。
