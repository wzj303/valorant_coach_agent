# VALORANT AI Coach · Agent-2 数据契约

版本 1.0.0，Python 3.11+，Pydantic V2。Agent-1 和 Agent-3 共同导入 `schemas`。跨进程传输使用 UTF-8 JSON。解包得到本 README 所在的模块根目录，以下命令在该目录中运行。

## 安装与验收

```text
python -m pip install -r requirements-agent2.txt
python -m pytest -q tests/schemas
python -m examples.agent2_integration
python -m schemas.export
```

最后一条命令按照当前安装的 Pydantic 版本再生 JSON Schema。交付包已附 `schemas/json_schema/contracts-1.0.0.json`。其 `$defs` 保存共享定义，`x-models`、`x-tools` 分别索引模型和六个工具的输入、输出。单工具注册直接使用 `TOOL_CONTRACTS[name][0].model_json_schema()` 获取含完整引用的参数定义。跨字段规则由 Pydantic 执行，接收 JSON 后仍应调用 `model_validate_json`。

## 调用校验工具

```python
from schemas import RoundSnapshot, ValidationToolResult
from tools.validate_round_data import validate_round_data

raw = {
    "match_id": "demo", "round_id": "demo-r1", "round_index": 1,
    "map_id": "sunset", "map_revision": None,
}
result = validate_round_data(round_snapshot=raw)
assert result.status == "partial" and result.data.valid
snapshot = result.data.normalized_snapshot
assert isinstance(snapshot, RoundSnapshot)
assert snapshot.ally_players is None
assert ValidationToolResult.model_validate_json(result.model_dump_json()) == result
```

- 单回合买枪传 `round_snapshot`。单帧站位传 `position_snapshot`。同时传入可检查所属回合和地图修订一致性。第一份标准化对象在 `data.normalized_snapshot`。同时传入时，站位对象在 `data.normalized_position_snapshot`。
- `expected_map_revision` 由宿主从 Agent-3 的 Sunset 地图配置读取并注入。模型可见的参数只有两个快照和 `strict_mode`。样例中的 `fixture-r1` 仅用作测试。没有可信地图配置时，结果会列出无法核验的原因。涉及地图版本的业务计算应由调用方据此暂停。
- `success` 表示已检查的数据完整。`partial` 可继续处理已有字段，`missing_fields` 与 `warnings` 解释限制。`failed` 时停止业务计算。结构错误的标准化结果与完整度均为 null。严格模式把缺失和疑点升级为失败，保留通过类型校验的快照供修正。
- 数据完整度只度量字段覆盖。证据缺失或地图版本不符时，完整度可以为 1，而状态仍为 `partial`。校验器重算回合的 `data_quality`，保留来源记录和未知值，不修改调用者传入的数据。

## 集成

1. Agent-1 从 `schemas` 导入 `AnalysisContext` 和 `CoachResponse`。每次工具调用先按 `TOOL_CONTRACTS` 查找已注册的函数和参数模型。校验工具可直接接收原始 JSON 对象并返回字段路径。完整的注册参数生成、错误返回和报告序列化示例在 `examples/agent2_integration.py`。
2. Agent-3 从 `schemas.tool_contracts` 导入五个业务工具的输入和数据模型。经济工具读 `round_snapshot`；站位工具读 `position_snapshots`。按工具名取 `TOOL_CONTRACTS[name][1]` 构造具体的 `ToolResult[T]`。部分结果填写 warnings，失败填写 errors。推测的敌方余额必须在输入 evidence 中标为 inferred，聚合结果保留 estimated。
3. 可信来源更新单个玩家标量时使用 `apply_player_observation`。已确认的值只能由后续用户确认更新。相互冲突的证据记录在解析阶段报错。其他结构采用完整快照交接，调用方合并多帧或多个来源时，需要在对应字段保留人工确认值。
4. 合并进现有工程时按目录放入 `schemas/`、`tools/validate_round_data.py`、`tests/schemas/`。本模块的 README、依赖和 examples 可独立保留。

## 交付与联调边界

本包包含数据契约、校验器、各工具的参数及返回模型、JSON Schema、字段说明、5 份测试数据及独立测试。A 和 B 的数据均为合成输入，C 仅记录已给出的四项事实，D 用于验证错误，E 演示区域与未知位置。字段释义和评分公式见 `schemas/FIELDS.md`。

1.0.0 为首版接口，尚待 Agent-1、Agent-3 在真实工程中确认。真实截图识别、经济工具、运行时地图配置以及端到端测试由对应模块联调。未来公共字段或语义变动需记录变更原因、影响模块、兼容策略并更新版本及导出的 Schema。
