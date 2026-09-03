# Project Status

更新时间：2026-09-03

## 当前里程碑

评测框架基础骨架、调用事件完整率、完全重复调用率、必填参数完整率、Schema 合法率、
参数值准确率核心、路由 Precision、路由 Recall、子任务冗余率和意图识别准确率已经实现。

已完成：

- Python 3.11+ 项目和 pytest、Ruff、mypy 工具链配置。
- `BaseEvaluator`、`EvaluationResult`、`EvaluationStatus` 公共契约。
- 公共契约及九个指标行为测试，共 148 项；最近一次验证全部通过。
- `collectors`、`evaluators`、`reporters`、`storage` 包入口。
- 代码地图和 38 项逐指标开发顺序；时延、Token 消耗及成本类指标统一置于最后阶段。
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
- LLM 结合整体意图、全部可见子任务和 MCP Schema 生成标准能力集合，并通过共享
  TP/FP/FN 同时计算 `routing_precision` 与 `routing_recall`，版本均为 `1.0.0`。
- 去实体化的意图签名、子任务类型与能力集合 JSONL 映射表，支持去重和 Jaccard 相似检索；
  原始意图、实体、参数和响应不得持久化。
- 聊天子任务的确定性 NFKC、约束词元和字符 bigram 冗余规则；不调用 LLM，任务正文和
  中间指纹不进入结果；`subtask_redundancy_rate` 版本为 `1.0.0`。
- 聊天与研究模式的意图证据规范化、严格三态 Judge 契约和 OpenAI-compatible 适配器；
  `intent_recognition_accuracy` 版本为 `1.0.0`，原始 query、规划、回答和 Judge 响应不落盘。
- 集中式指标规格 `docs/specs/metrics.md`，当前记录九个已实现指标。

## 当前状态

- 最近完成并验收的具体指标：子任务冗余率。
- 已暂时跳过指标：调用成功率、异常率、重试成功率、重试率、平均重试尝试次数和
  实体映射准确率；相关实现、测试和专用规格已按用户要求删除或取消。
- 已取消指标：漏调用率、工具和 MCP 选择准确率、子任务覆盖率。漏调用率与
  `routing_recall` 高度重复；后两项缺少可靠的任务级标准或跨模式结构化关联字段。
- 已取消指标：依赖关系正确率；聊天与研究日志均缺少稳定的结构化任务 DAG 字段，
  不使用事件顺序推测依赖关系。
- 进行中的具体指标：意图识别准确率已按用户确认的 Judge 代理口径完成实现和自动化验证，
  等待第二次验收；验收前不进入下一指标。
- 下一指标：等待意图识别准确率验收后再按顺序进入关键实体提取准确率。
- 下一动作：展示聊天/研究模式的计算边界、测试结果和模拟计算，等待第二次确认。

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
pytest: 125 passed
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
路由双指标测试: 22 项通过
路由双指标真实前 10 条聊天日志: 3 条可评估、7 条路由前服务失败；可评估日志 TP=6，
  实际能力=9，标准能力=14；微平均 Precision 0.66667，微平均 Recall 0.42857；
  Judge/格式错误 0
安全意图路由映射表: 14 条去重记录，敏感字段与中文实体扫描命中 0
子任务冗余率测试: 15 项通过；全量 pytest 共 125 项通过
子任务冗余率聊天日志验算: 32/106 个 Trace 可计算，187 个有效子任务，规则命中 0 个冗余，
  微平均冗余率 0.0；其余 74 个 Trace 无子任务
子任务冗余率研究日志: 当前缺少确认的结构化子任务契约，返回 missing_structured_subtasks
意图识别准确率测试: 23 项通过；聊天与研究模式均为 CONDITIONALLY_COMPUTABLE
意图识别准确率聊天模式: 缺少 query 返回 missing_query；无行为证据返回 no_intent_evidence；
  固定前置服务失败返回 pre_intent_failure
意图识别准确率研究模式: 缺少 query 返回 missing_query；缺少调用 ID 返回 missing_call_id；
  同 ID 不同工具返回 conflicting_call_definition
```
