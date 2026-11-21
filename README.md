# valorant_coach_agent

瓦洛兰特战术复盘教练。

多模态单 Agent：看截图、调用 Python 工具、输出结构化教练报告。地图 V0 仅 Sunset。

| 模块 | 负责 | 说明 |
|---|---|---|
| Agent 与 Skills（`agent/`、`skills/`、`tools/registry.py`） | GPT-1 | [README-agent1.md](README-agent1.md) |
| 数据契约与校验（`schemas/`、`tools/validate_round_data.py`） | GPT-2 | [README-agent2.md](README-agent2.md) |
| 战术工具与服务（`tools/domain/`、`services/`、`knowledge/`） | GPT-3 | 本 README 的 Tactical Engine 说明 |
| 本地 Web 前端（`webapp/`） | 共用 | 见下方「Web 前端」 |

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

## 本地 Web 前端

零依赖的浏览器界面：一个人启动服务，队友用浏览器打开地址即可使用，无需安装任何东西。

```powershell
python -m webapp                    # 监听所有网卡，默认端口 8765
python -m webapp --port 9000        # 换端口
python -m webapp --host 127.0.0.1   # 仅本机
```

启动后控制台会打印本机地址和局域网地址（例如 `http://192.168.x.x:8765/`），把局域网地址发给同一网络下的队友即可。首次启动若 Windows 弹出防火墙提示，选择允许。

页面功能：

- 选择任务类型（购买 / 站位 / 整场 / 自动），填写问题或补充说明，上传截图（可拖拽、多张、带预览），或直接粘贴快照 JSON（内置完整回合、部分回合、站位、非法数据四类样例一键载入）
- 「开始分析」后展示教练报告：状态、总体判断、购买/站位结论、发现（带严重度和置信度）、建议、证据表、缺失信息、总结，以及工具调用 trace（可复制 JSON / Markdown）
- 未配置模型密钥时自动使用离线演示模式（不联网、不需要任何密钥）

### 模型配置（真实模型）

页面顶部的「模型配置」面板支持三种用法：

1. **保存到服务器（全队共用）**：填写接口格式（OpenAI 兼容 / Anthropic）、Base URL、模型名和 API Key，点「保存到服务器」。配置写入服务器本地 `.env`（已被 .gitignore 排除，不会上传 GitHub），保存后密钥不回显，全队共用这一个账号。
2. **仅本次分析使用**：勾选「仅本次分析使用」后，本次请求带上你填的密钥，不写入服务器。适合队友使用自己的密钥，用完即走。
3. **测试连接**：点「测试连接」会对当前填写的配置跑三项探测（纯文字、看图、工具调用），全部通过再开始分析，能发现"网关悄悄丢弃图片"这类兼容性问题。

也可不用界面，直接在服务器上手动写 `.env`（参考 `.env.example`）。

**安全提醒**：服务器监听所有网卡，同一局域网内任何能打开页面的人都可以修改服务器保存的配置。只在可信网络中使用「保存到服务器」；密钥不会回显给任何人。

接口：`GET /api/health`、`GET /api/config`、`POST /api/config`、`POST /api/config/test`、`GET /api/samples`、`POST /api/analyze`。请求体上限 64 MB，单张图片 ≤ 20 MB、最多 10 张，模型输出全部按纯文本渲染，不会执行任何上传内容。

跨网络访问（队友不在同一局域网）需要内网穿透或路由器端口转发，例如 frp / ngrok 等工具，把外网端口映射到本机的 8765。

## GPT-3 Tactical Engine

提供五个可独立调用的战术工具入口（经 GPT-2 契约适配）：

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
