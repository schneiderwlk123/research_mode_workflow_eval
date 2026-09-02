# Project Status

更新时间：2026-09-02

## 当前里程碑

评测框架基础骨架、调用事件完整率和调用成功率已经实现。

已完成：

- Python 3.11+ 项目和 pytest、Ruff、mypy 工具链配置。
- `BaseEvaluator`、`EvaluationResult`、`EvaluationStatus` 公共契约。
- 公共契约及首个指标行为测试，共 23 项；最近一次验证全部通过。
- `collectors`、`evaluators`、`reporters`、`storage` 包入口。
- 代码地图和 48 项逐指标开发顺序。
- MCP 工具手册已复制到项目，当前文件名为 `mcp_tool_manual.json`。
- 聊天与研究工具调用事件的最小规范化切片，不保留工具参数或响应正文。
- `tool_call_event_completeness_rate` 评测器，版本 `1.0.0`。
- 可版本化工具成功规则、聊天/研究工具名关联和不保留响应正文的三态成功判定。
- `tool_call_success_rate` 严格全量评测器，版本 `1.0.0`。
- 集中式指标规格 `docs/specs/metrics.md`，当前记录调用事件完整率与调用成功率。

## 当前状态

- 进行中的具体指标：调用成功率已实现并通过验证，等待用户二次验收。
- 下一指标：异常率。
- 下一动作：向用户展示调用成功率的测试和计算结果；收到二次确认前不得进入异常率设计或实现。

## 已知缺口

- 聊天模式 JSONL 与研究模式 JSON 的完整统一 Trace schema 尚未定义；当前只完成工具
  调用事件完整率所需的最小规范化切片。
- collectors、聚合器、报告器、配置加载器和可运行 CLI 尚未实现。
- Golden Benchmark、Judge、价格表和交付物读取能力尚未接入。
- MCP 工具手册未定义响应 schema 或业务成功码；工具成功规则需要按工具逐步配置，
  未配置或无法判定的调用会使严格成功率返回不可计算。
- 聊天模式缺少完整 E2E 时间与 Token 字段，部分指标只能返回不可计算。

## 验证基线

```text
pytest: 37 passed
ruff check: passed
ruff format --check: passed
mypy src tests: passed
现有研究日志结构验算: 30/30 = 1.0, computed
调用成功率模拟验算: 2/4 = 0.5, computed
```
