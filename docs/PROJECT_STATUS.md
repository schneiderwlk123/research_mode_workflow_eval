# Project Status

更新时间：2026-09-04

## 当前里程碑

评测框架基础骨架、调用事件完整率、完全重复调用率、MCP 工具调用膨胀率、必填参数完整率、
Schema 合法率、
参数值准确率核心、路由 Precision、路由 Recall、子任务冗余率和意图识别准确率已经实现。
时间范围提取准确率、证据查准率和陈述支持率已验收。模型工具循环深度已实现并完成验证。

已完成：

- Python 3.11+ 项目和 pytest、Ruff、mypy 工具链配置。
- `BaseEvaluator`、`EvaluationResult`、`EvaluationStatus` 公共契约。
- 公共契约及十四个指标行为测试，共 258 项；最近一次验证全部通过。
- `collectors`、`evaluators`、`reporters`、`storage` 包入口。
- 代码地图和 25 项逐指标开发顺序；时延、执行效率及 Token 消耗类指标统一置于最后阶段。
- MCP 工具手册已复制到项目，当前文件名为 `mcp_tool_manual.json`。
- 聊天与研究工具调用事件的最小规范化切片，不保留工具参数或响应正文。
- `tool_call_event_completeness_rate` 评测器，版本 `1.0.0`。
- 聊天与研究调用的安全摘要比较规范化，不持久化参数、响应或摘要值。
- `exact_duplicate_tool_call_rate` 评测器，版本 `1.0.0`。
- `mcp_tool_call_inflation_rate` 评测器，版本 `1.0.0`；只统计手册内 MCP，同任务内相同能力
  与规范化入参的后续调用计为膨胀，响应正文不参与比较。
- `model_tool_loop_depth` 评测器，版本 `1.0.0`；研究模式按模型轮次统计完整的
  模型→工具→模型循环，并行工具只计一层，聊天模式缺少轮次边界时不估算。
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
- 聊天与研究模式的时间需求、工具参数和可复现时间锚点规范化，严格字段计数 Judge 契约
  及 OpenAI-compatible 适配器；`time_range_extraction_accuracy` 版本为 `1.0.0`，
  query、日期原文、参数值和 Judge 响应不落盘。
- 显式 MCP 证据工具目录、聊天/研究成功响应关联、来源原生结构拆分和内存证据去重；
  `evidence_precision_rate` 版本为 `1.0.0`，不可拆分文本不估算，query、证据正文、摘要和
  Judge 原始响应不落盘；显式证据工具范围生成稳定 `evidence_scope_version`。
- 聊天最终回答和研究模式 `turn.end` 前最后一个 assistant 文本提取、空证据零分入口、严格
  原子陈述 Judge 契约及 OpenAI-compatible 适配器；`statement_support_rate` 版本为 `1.0.0`，
  query、回答、原子陈述、证据正文和 Judge 原始响应不落盘。
- 集中式指标规格 `docs/specs/metrics.md`，当前记录十四个已实现指标。

## 当前状态

- 最近完成并验收的具体指标：陈述支持率。
- 已暂时跳过指标：调用成功率、异常率、重试成功率、重试率、平均重试尝试次数和
  实体映射准确率；相关实现、测试和专用规格已删除。
- 已删除指标：证据查全率、证据时效合格率、证据可追溯率、无依据陈述率、数值忠实度、
  硬编码率、循环引用率、需求覆盖率、严格任务完成率、信息保留率、信息坍缩率和
  中间结果与最终结果一致率、公式计算准确率、公式单元格覆盖率；这些指标不再保留在开发
  顺序和原始指标范围中。其中证据查全率依赖当前项目尚未具备的高标准 Golden Benchmark。
- 已取消指标：每个成功会话的平均成本；已从开发顺序和原始指标范围删除。
- 进行中的具体指标：模型工具循环深度，已实现并完成验证，等待验收。
- 下一指标：单次工具调用时延。
- 下一动作：展示模型工具循环深度的实现、测试和双模式真实日志结果，等待验收。

## 已知缺口

- 聊天模式 JSONL 与研究模式 JSON 的完整统一 Trace schema 尚未定义；当前只完成工具
  调用事件完整率所需的最小规范化切片。
- collectors、聚合器、报告器、配置加载器和可运行 CLI 尚未实现。
- Golden Benchmark 校准集和交付物读取能力尚未接入。
- 路由标准目前由 LLM + MCP Schema 生成，尚未经过人工校准集冻结；因此属于可追溯的
  Judge 代理标准，而非人工 Golden Benchmark。
- 聊天模式缺少完整 E2E 时间与 Token 字段，部分指标只能返回不可计算。
- 聊天模式缺少模型步骤和循环轮次边界，模型工具循环深度只能返回不可计算。

## 验证基线

```text
pytest: 236 passed
ruff check: passed
ruff format --check: passed
mypy src tests: passed
现有研究日志结构验算: 30/30 = 1.0, computed
完全重复调用率聊天日志验算: 35/907 ≈ 0.03859, computed
完全重复调用率研究日志验算: 1/30 ≈ 0.03333, computed
MCP 工具调用膨胀率测试: 21 项通过；只比较手册内 MCP 能力与规范化入参，不读取响应
MCP 工具调用膨胀率聊天日志验算: 35/106 个 Trace 可计算，643 个 MCP 调用中 45 个膨胀，
  微平均 45/643 ≈ 0.06998；598 个不同请求签名，264 个手册外调用被排除
MCP 工具调用膨胀率研究日志验算: 5 个 MCP 调用、5 个不同请求签名，0/5 = 0.0, computed；
  25 个手册外调用被排除，生命周期和重复定义按 call ID 合并
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
意图识别准确率真实单调用冒烟: 聊天模式 3 段行为证据、1 个 MCP 能力，Judge 返回
  correct / material_intents_aligned / confidence 0.95，最终结果 1.0, computed
时间范围提取准确率测试: 26 项通过；聊天与研究模式均为 CONDITIONALLY_COMPUTABLE
时间范围提取准确率聊天模式: 支持显式 reference_time 和首个工具请求 create_time；正常执行
  但未传递时间字段可由 Judge 计算为 0，固定前置服务失败返回 pre_time_extraction_failure
时间范围提取准确率研究模式: 支持事件 timestamp/create_time 或 part.state.time.start 锚点；
  缺少调用 ID、冲突定义和参数解析不完整分别显式返回安全原因码
时间范围提取准确率模拟计算: 正确字段 6、错误字段 1，结果 6/7 ≈ 0.85714, computed
时间范围提取准确率真实前 10 条聊天日志: 2 条可计算、1 条无时间需求、7 条前置模型服务
  失败；可计算日志正确字段 5、错误字段 2，微平均 5/7 ≈ 0.71429；Judge 失败 0
证据查准率测试: 24 项通过；聊天与研究模式均为 CONDITIONALLY_COMPUTABLE
证据查准率聊天日志结构验算: 106 条中 3 条形成 Judge 候选，共抽取 1263 个稳定结构化
  证据单元；15 条因同时存在不可拆分响应返回 incomplete_evidence_segmentation，81 条没有
  已登记证据调用，7 条仅有无法可靠拆分的响应
证据查准率研究日志结构验算: 5 个已登记证据调用中抽取 7 个稳定证据单元，但另有 3 个
  成功响应无稳定来源边界，因此返回 incomplete_evidence_segmentation，不缩小分母；未调用
  真实 Judge，未生成语义分数
证据查准率真实单会话冒烟: 聊天模式 20 个稳定证据单元，Judge 判定 relevant 20、
  irrelevant 0、not_assessable 0，结果 20/20 = 1.0, computed；Judge 模型 qwen-plus，
  prompt 版本 1.0.0，证据范围版本 evidence-scope-623429fd8dffba76
陈述支持率测试: 17 项通过；聊天与研究模式均为 CONDITIONALLY_COMPUTABLE
陈述支持率聊天模式: 顶层 query/answer 可形成 Judge 候选；没有证据或明确空结果仍可得到
  0 分，固定服务失败、缺少回答、证据缺终态和部分响应不可拆分均返回稳定安全原因码
陈述支持率研究模式: 通过 assistant message ID 选择 turn.end 前最后一个非空文本；成功产物
  无最终正文返回 missing_final_artifact_content，缺少证据终态和不完整拆分不估算
陈述支持率模拟计算: 2 个可验证原子陈述中 1 个 supported、1 个 unsupported，另有 1 个
  excluded 建议，结果 1/2 = 0.5, computed；聊天与研究 evaluator 路径均通过
模型工具循环深度测试: 22 项通过；并行工具只计一层，重复模型快照按 loop 去重
模型工具循环深度聊天模式: 缺少模型步骤和轮次边界，返回 missing_model_iteration_boundary
模型工具循环深度研究日志验算: 27 个唯一模型轮次，前 26 轮调用 30 个唯一工具且均有终态，
  第 27 轮 finishReason=stop；结果 26, computed
```
