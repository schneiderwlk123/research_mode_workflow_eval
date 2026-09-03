# Project Status

更新时间：2026-09-03

## 当前里程碑

评测框架基础骨架、调用事件完整率、完全重复调用率、必填参数完整率、Schema 合法率、
参数值准确率核心、路由 Precision 和路由 Recall 已经实现。

已完成：

- Python 3.11+ 项目和 pytest、Ruff、mypy 工具链配置。
- `BaseEvaluator`、`EvaluationResult`、`EvaluationStatus` 公共契约。
- 公共契约及七个指标行为测试，共 114 项；最近一次验证全部通过。
- `collectors`、`evaluators`、`reporters`、`storage` 包入口。
- 代码地图和 42 项逐指标开发顺序；时延、Token 消耗及成本类指标统一置于最后阶段。
- MCP 工具手册已复制到项目，当前文件名为 `mcp_tool_manual.json`。
- 聊天与研究工具调用事件的最小规范化切片，不保留工具参数或响应正文。
- `tool_call_event_completeness_rate` 评测器，版本 `1.0.0`。
- 聊天与研究调用的安全摘要比较规范化，不持久化参数、响应或摘要值。
- `exact_duplicate_tool_call_rate` 评测器，版本 `1.0.0`。
- 版本化 MCP 必填参数目录，以及聊天/研究调用的安全聚合规范化；参数值和摘要不进入结果。
- `required_parameter_completeness_rate` 评测器，版本 `1.1.0`；Schema 覆盖仅保留诊断计数。
- MCP 参数类型、枚举和嵌套结构的递归校验；额外字段只诊断，不重复检查顶层必填字段。
- `tool_argument_schema_validity_rate` 评测器，版本 `1.1.0`。
- 参数值语义目录、聊天/研究 Judge 候选提取，以及不持久化 query 和参数值的内存边界。
- 可注入、版本化 Judge 契约与 `parameter_value_accuracy_rate` 评测器，版本 `1.1.0`；
  支持逐字段三态判定和部分失败隔离，最终结果不计算或输出辅助率指标。
- OpenAI-compatible Judge 适配器与常用 dotenv 配置加载，远程端点强制 HTTPS，并对
  Judge 供应商异常做脱敏隔离。
- 参数与路由 Judge 已统一迁移到阿里云百炼 Batch File：输入 JSONL 仅在内存构造，完成
  上传、任务创建、轮询、结果下载和远端文件清理；默认 24 小时完成窗口、60 秒轮询、
  86400 秒整体等待上限；仅对幂等或安全操作执行有限重试，避免重复任务与重复计费，并显式
  关闭思考模式。
- LLM 结合整体意图、全部可见子任务和 MCP Schema 生成标准能力集合，并通过共享
  TP/FP/FN 同时计算 `routing_precision` 与 `routing_recall`，版本均为 `1.0.0`。
- 去实体化的意图签名、子任务类型与能力集合 JSONL 映射表，支持去重和 Jaccard 相似检索；
  原始意图、实体、参数和响应不得持久化。
- 集中式指标规格 `docs/specs/metrics.md`，当前记录前七个已实现指标。

## 当前状态

- 最近完成的具体指标：路由 Precision 与路由 Recall，等待展示测试和计算结果后进行
  二次验收。
- 已暂时跳过指标：调用成功率、异常率、重试成功率、重试率、平均重试尝试次数和
  实体映射准确率；相关实现、测试和专用规格已按用户要求删除或取消。
- 进行中的具体指标：路由 Precision 与路由 Recall 验收；参数值准确率的生产 Judge
  适配器已完成真实单调用冒烟测试。
- 下一指标：漏调用率。
- 下一动作：展示路由双指标的测试和模拟计算结果，等待二次确认；确认前不得进入漏调用率。

## 已知缺口

- 聊天模式 JSONL 与研究模式 JSON 的完整统一 Trace schema 尚未定义；当前只完成工具
  调用事件完整率所需的最小规范化切片。
- collectors、聚合器、报告器、配置加载器和可运行 CLI 尚未实现。
- Golden Benchmark 校准集、价格表和交付物读取能力尚未接入。
- 路由标准目前由 LLM + MCP Schema 生成，尚未经过人工校准集冻结；因此属于可追溯的
  Judge 代理标准，而非人工 Golden Benchmark。
- 聊天模式缺少完整 E2E 时间与 Token 字段，部分指标只能返回不可计算。

## 验证基线

```text
pytest: 88 passed
ruff check: passed
ruff format --check: passed
mypy src tests: passed
现有研究日志结构验算: 30/30 = 1.0, computed
完全重复调用率聊天日志验算: 35/907 ≈ 0.03859, computed
完全重复调用率研究日志验算: 1/30 ≈ 0.03333, computed
必填参数完整率聊天日志验算: 696/832 ≈ 0.83654
必填参数完整率研究日志验算: 10/11 ≈ 0.90909
Schema 合法率聊天日志验算: 642/643 ≈ 0.99844
Schema 合法率研究日志验算: 5/5 = 1.0
参数值准确率聊天日志候选: 643/907，106 个 Trace 均成功规范化
参数值准确率研究日志候选: 5/30，30 个重复观测已按 call ID 去重
参数值准确率真实单调用冒烟: 1 个正确字段、1 个不可判定字段，准确率 1.0，Judge 失败 0
路由规范化 MCP 目录: 77 个能力，手册版本 0.1.0
路由规范化聊天日志: 106/106 个 Trace 成功，35 个 Trace 含 MCP 路由，提取 185 个子任务片段
路由规范化研究日志: 1 个 Trace 成功，5 个 MCP 能力；当前日志只提供 1 个整体意图片段
路由双指标及 Batch File 测试: 26 项通过；全量 pytest 共 114 项通过
路由双指标真实前 10 条聊天日志: 3 条可评估、7 条路由前服务失败；可评估日志 TP=6，
  实际能力=9，标准能力=14；微平均 Precision 0.66667，微平均 Recall 0.42857；
  Judge/格式错误 0
安全意图路由映射表: 14 条去重记录，敏感字段与中文实体扫描命中 0
```
