# valorant_coach_agent
瓦洛兰特战术复盘教练

多模态单 Agent：看截图、调用 Python 工具、输出结构化教练报告。地图 V0 仅 Sunset。

| 模块 | 负责 | 说明 |
|---|---|---|
| Agent 与 Skills（`agent/`、`skills/`、`tools/registry.py`） | GPT-1 | [README-agent1.md](README-agent1.md) |
| 数据契约与校验（`schemas/`、`tools/validate_round_data.py`） | GPT-2 | [README-agent2.md](README-agent2.md) |
| 战术工具与服务（`tools/domain/`、`services/`、`knowledge/`） | GPT-3 | 待合入 |

快速体验（离线，无需密钥）：

```text
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m agent --demo --task buy_review --text "x" --trace
```
