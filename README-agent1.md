# VALORANT AI Coach · Agent-1：Agent Core 与 Skills

单回合 Agent 直接接收截图和文字，选择 Skill，调用已注册的 Python 工具，输出符合 `schemas.CoachResponse` 的教练报告。整场连续复盘通过 `agent/match_graph.py` 的 LangGraph 工作流管理逐回合校验、计算、报告和 SQLite 检查点，见 [工作流说明](docs/match-workflow.md)。不含经济/地图算法（GPT-3）和数据契约（GPT-2）。

## 安装与运行

需要 Python 3.11+。建议 3.12。Windows 上的 Python 3.14 alpha 与 `pydantic_core` 会在 pytest 下崩溃（access violation），请不要用。

```text
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pytest -q                      # 全部测试，不联网
.venv\Scripts\python -m agent --demo --task buy_review --text "x" --trace   # 离线演示
```

接真实模型：复制 `.env.example` 为 `.env`，填 `VALORANT_LLM_API_KEY`、`VALORANT_LLM_MODEL`，按需填 `VALORANT_LLM_BASE_URL` 和 `VALORANT_LLM_FORMAT`（`openai` 或 `anthropic`）。

```text
python -m agent --check-llm                            # 先探测：文字 / 看图 / 工具调用
python -m agent --task buy_review --image shot.png --text "这回合该怎么买？"
python -m agent --task position_review --image minimap.png --json
```

`--check-llm` 的看图探测会发一张纯蓝图并核对答案。不少兼容网关接受图片字段却丢掉图片，这一步能发现。通不过时不要接入。

Python 调用：

```python
from agent import AgentLoop, AgentRequest, RealLlmAdapter, LlmConfig, SkillLoader
from tools.registry import build_registry

loop = AgentLoop(RealLlmAdapter(LlmConfig.from_env()), build_registry(), SkillLoader())
report = await loop.run(AgentRequest(text="...", images=["shot.png"], task_type="buy_review"))
```

`run_with_trace` 额外返回工具调用、token 用量、修复次数和降级说明。

## 结构

| 文件 | 作用 |
|---|---|
| `agent/agent_loop.py` | AgentLoop：多模态输入、选 Skill、多轮 Tool Calling、输出校验与修复、确定性兜底规则 |
| `agent/llm_adapter.py` | `ILlmAdapter`、`LlmConfig`、`RealLlmAdapter`（官方 `openai` / `anthropic` SDK） |
| `agent/llm_convert.py` | 中立消息格式与 OpenAI / Anthropic 报文互转（纯函数，不联网） |
| `agent/llm_types.py` | `Message`、`ImagePart`、`ToolCall`、`LlmRequest`、`LlmResponse` |
| `agent/mock_llm.py` | `MockLlmAdapter`：按脚本回放，测试和演示用 |
| `agent/tool_registry.py` | `ToolRegistry`：参数校验、调用、异常和超时隔离、输出契约检查 |
| `agent/skill_loader.py` | `SkillLoader`：解析 `skills/*/SKILL.md` |
| `agent/prompts.py` | 系统提示词（推理规则、CoachResponse 填写说明） |
| `agent/diagnostics.py`、`report.py`、`__main__.py` | 模型能力探测、Markdown 报告、命令行 |
| `tools/registry.py` | 注册数据校验、五个战术工具与 `get_coaching_advice` 知识库检索 |
| `skills/` | `buy-decision-review`、`position-coordination-review`、`coach-feedback` |

复用的开源件：`openai`、`anthropic`（请求、重试、错误类型）、`pydantic`（参数与输出校验）、`PyYAML`（SKILL.md 头部）。单回合 AgentLoop 保持轻量；整场编排使用 `langgraph` 与 `langgraph-checkpoint-sqlite`，直接复用中立模型适配器和战术工具，不需要更换为 LangChain 模型封装。

## 行为约定

- **图片**：以 base64 图片块发给模型，按文件头识别格式（不信扩展名），单张最大 20 MB，单次最多 10 张。路径不会被当文字发送。适配器声明不支持视觉时，带图请求在发出前就报 `LlmCapabilityError`，不会悄悄丢图。
- **Skill 选择**：显式 `task_type` 优先（`buy_review` / `position_review` / `match_review`）。没有时按关键词匹配，平局或无匹配则让模型调用 `load_skill` 自己选。Skill 的 `allowed_tools` 同时限制模型看到的工具和注册表愿意执行的工具。
- **工具失败**：未知工具、参数错误、工具抛异常、超时、返回值不合契约，都会变成 `status=failed` 的 ToolResult 回给模型，附字段路径，模型可修正后重试。Agent 不会因此崩溃。
- **兜底规则（模型绕不过）**：任一工具最终结果为 `failed` 或 `partial` 时，模型写的 `task_status=success` 会被降为 `partial`；失败的工具自动补进 `missing_information`；`task_id` 以请求为准；建议超过三条时按 `priority` 保留前三条。
- **输出修复**：最终回复不是合法 CoachResponse 时，把具体字段错误发回模型，最多修复 2 次。仍失败则返回 `task_status=failed` 的报告，不抛异常。
- **限制**：默认最多 12 轮、24 次工具调用。用完后强制模型直接给结论。

## 与 GPT-2 的集成

直接 `from schemas import ...`，不重复定义任何结构。

- 工具参数 JSON Schema 来自 `TOOL_CONTRACTS[name][0]`，结果校验用 `TOOL_CONTRACTS[name][1]`。
- `validate_round_data` 的 `expected_map_revision` 由宿主注入（`build_registry(expected_map_revision=...)`），模型传这个参数会被拒绝。
- 后续计算使用校验返回的 `normalized_snapshot`。专业建议检索接受场景关键词、地图名或条目编号。

## 与 GPT-3 的集成

五个业务工具均通过 `tools/domain/contracts.py` 接入。可通过 `tools/domain/<工具名>.py` 的同名函数覆盖默认适配器。注册表支持以下两种签名。

```python
def analyze_buy_state(request: BuyStateInput) -> ToolResult[BuyStateAnalysis]: ...
def analyze_buy_state(round_snapshot: RoundSnapshot) -> ...:    # 按字段名传入类型化对象
```

- 返回值必须通过 `TOOL_CONTRACTS[name][1]` 校验，否则注册表把它当作失败。用参数化类型构造结果（`ToolResult[BuyStateAnalysis](...)`），不要用裸 `ToolResult`。
- 模块还不存在时，该工具被跳过，Skill 会告诉模型这个工具不可用并降低结论强度。`build_registry(strict=True)` 在缺工具时直接报错，用于集成验收。
- 测试可用 `build_registry(handlers={"analyze_buy_state": fn})` 注入替身，参见 `tests/agent/helpers.py`。

## 未完成与已知限制

- **在线模型待验证**：OpenAI 兼容 SDK 已通过本机回环服务验证图片传输、两轮工具调用和报告返回。实际模型的截图识别能力需要配置服务后使用 `--check-llm` 和真实截图检验。
- 离线端到端测试使用真实战术工具；价格、地图修订或局内观察不足时会产生部分结果。
- **截图读取质量取决于模型**：本模块不做 OCR，Skill 只约束填写规则。读错的字段靠 `validate_round_data` 的一致性检查和用户确认纠正。
- 单回合入口没有流式输出和跨请求记忆。整场入口通过 SQLite 保存连续回合状态，Web 页面轮询进度；尚未提供逐 token 流式输出。
- Windows 上若系统临时目录无写权限，pytest 的 `tmp_path` 会失败，`pyproject.toml` 已把 `--basetemp` 指到仓库内的 `.pytest_tmp`。
