# Project Status

更新时间：2026-09-03

## 当前里程碑

评测框架基础骨架、调用事件完整率、完全重复调用率、必填参数完整率、Schema 合法率和
参数值准确率核心已经实现。

已完成：

- Python 3.11+ 项目和 pytest、Ruff、mypy 工具链配置。
- `BaseEvaluator`、`EvaluationResult`、`EvaluationStatus` 公共契约。
- 公共契约及五个指标行为测试，共 81 项；最近一次验证全部通过。
- `collectors`、`evaluators`、`reporters`、`storage` 包入口。
- 代码地图和 43 项逐指标开发顺序；时延、Token 消耗及成本类指标统一置于最后阶段。
- MCP 工具手册已复制到项目，当前文件名为 `mcp_tool_manual.json`。
- 聊天与研究工具调用事件的最小规范化切片，不保留工具参数或响应正文。
- `tool_call_event_completeness_rate` 评测器，版本 `1.0.0`。
- 聊天与研究调用的安全摘要比较规范化，不持久化参数、响应或摘要值。
- `exact_duplicate_tool_call_rate` 评测器，版本 `1.0.0`。
- 版本化 MCP 必填参数目录，以及聊天/研究调用的安全聚合规范化；参数值和摘要不进入结果。
- `required_parameter_completeness_rate` 评测器，版本 `1.0.0`，同时报告 Schema 覆盖率。
- MCP 参数类型、枚举和嵌套结构的递归校验；额外字段只诊断，不重复检查顶层必填字段。
- `tool_argument_schema_validity_rate` 评测器，版本 `1.0.0`。
- 参数值语义目录、聊天/研究 Judge 候选提取，以及不持久化 query 和参数值的内存边界。
- 可注入、版本化 Judge 契约与 `parameter_value_accuracy_rate` 评测器，版本 `1.0.0`；
  支持逐字段三态判定、部分失败隔离、完全匹配率和可判定覆盖率。
- 集中式指标规格 `docs/specs/metrics.md`，当前记录前五个已实现指标。

## 当前状态

- 最近完成并通过二次验收的具体指标：Schema 合法率。
- 已暂时跳过指标：调用成功率、异常率、重试成功率、重试率、平均重试尝试次数；
  相关实现、测试和专用规格已按用户要求删除。
- 进行中的具体指标：参数值准确率核心已实现并通过验证，等待用户二次验收；生产 Judge
  模型适配器尚未配置，因此不生成真实日志语义得分。
- 下一指标：实体映射准确率。
- 下一动作：展示参数值准确率测试、模拟计算和真实日志候选覆盖；收到二次确认前不得进入
  实体映射准确率设计或实现。

## 已知缺口

- 聊天模式 JSONL 与研究模式 JSON 的完整统一 Trace schema 尚未定义；当前只完成工具
  调用事件完整率所需的最小规范化切片。
- collectors、聚合器、报告器、配置加载器和可运行 CLI 尚未实现。
- 生产 Judge 模型适配器、Golden Benchmark 校准集、价格表和交付物读取能力尚未接入。
- 聊天模式缺少完整 E2E 时间与 Token 字段，部分指标只能返回不可计算。

## 验证基线

```text
pytest: 81 passed
ruff check: passed
ruff format --check: passed
mypy src tests: passed
现有研究日志结构验算: 30/30 = 1.0, computed
完全重复调用率聊天日志验算: 35/907 ≈ 0.03859, computed
完全重复调用率研究日志验算: 1/30 ≈ 0.03333, computed
必填参数完整率聊天日志验算: 696/832 ≈ 0.83654, Schema 覆盖 643/907 ≈ 0.70893
必填参数完整率研究日志验算: 10/11 ≈ 0.90909, Schema 覆盖 5/30 ≈ 0.16667
Schema 合法率聊天日志验算: 642/643 ≈ 0.99844, Schema 覆盖 643/907 ≈ 0.70893
Schema 合法率研究日志验算: 5/5 = 1.0, Schema 覆盖 5/30 ≈ 0.16667
参数值准确率聊天日志候选: 643/907，106 个 Trace 均成功规范化
参数值准确率研究日志候选: 5/30，30 个重复观测已按 call ID 去重
参数值准确率真实语义得分: 未配置生产 Judge，当前不可计算
```
