# Code Map

评测流水线方向：`collectors -> evaluators -> reporters`，数据和产物访问由 `storage` 提供。

- `docs/START_HERE.md`：每次新会话的稳定阅读入口和不可破坏约束。
- `docs/PROJECT_STATUS.md`：当前里程碑、下一动作、已知缺口和验证基线。
- `docs/specs/metric-development-order.md`：25 项指标的唯一开发顺序；新增最终数值与召回证据
  一致率、MCP 工具调用膨胀率、
  工具间空窗时延、首次工具决策时延和研究模式上下文膨胀率。
- `.cursor/rules/metric-calculability.mdc`：要求每个指标在编码和验收前分别说明聊天与研究
  模式的可计算性、缺失条件和稳定不可计算原因，禁止估算或静默省略模式。
- `.env.example`：本地运行配置模板；Judge 使用 `JUDGE_API_URL`、`JUDGE_API_KEY` 和
  `JUDGE_MODEL`，真实密钥只允许写入被忽略的 `.env`。
- `src/collectors/tool_call_events.py`：按 `traceId + toolId` 合并新版聊天 `canvas` 中跨消息的
  普通工具请求与结果，并规范化研究 `message.part.updated`；旧版聊天 `tool_call_queue` 不再
  支持。孤立 Agent 返回只进入安全诊断，不反向生成调用，也不保留请求、参数或响应正文。
- `src/collectors/exact_duplicate_calls.py`：按 `traceId + toolId` 关联新版聊天 canvas 请求与
  跨消息结果，在内存中规范化工具名、参数和响应并比较摘要；旧版聊天结构不再支持，孤立
  Agent 返回、重复观察和冲突定义只输出安全诊断，不保留原始正文或摘要。
- `src/collectors/mcp_tool_call_inflation.py`：按版本化 MCP 目录过滤聊天/研究调用，以调用 ID
  去重并对能力 ID 与入参生成不可逆签名，输出调用、唯一签名和膨胀调用聚合计数。
- `src/collectors/required_parameters.py`：从版本化 MCP 手册构建顶层必填参数目录；聊天模式
  按 `traceId + toolId` 扫描新版 canvas 并排除子 Agent 调用，研究模式按 `toolCallId` 去重，
  统计 Schema 覆盖和非空顶层必填参数；旧版聊天结构不再支持，输出不含参数值或摘要。
- `src/collectors/tool_argument_schema.py`：把 MCP 手册类型、枚举和嵌套定义转换为递归
  Schema；聊天模式按 `traceId + toolId` 扫描新版 canvas 并排除子 Agent 调用，研究模式按
  `toolCallId` 去重；旧版聊天结构不再支持，额外字段仅诊断，不使调用非法。
- `src/collectors/parameter_value_accuracy.py`：从 MCP 手册构建工具与字段语义目录；聊天模式
  按 `traceId + toolId` 扫描新版 canvas 并排除子 Agent 调用，研究模式按 `toolCallId` 去重，
  生成只在内存流转的 Judge 候选；旧版聊天结构不再支持，长期结果不得保留 query 或参数值。
- `src/collectors/routing.py`：从 MCP 手册构建版本化能力目录；聊天模式扫描新版 canvas，
  按 `traceId + toolId` 去重普通工具请求，并从子 Agent 的 `arguments.query/task` 提取任务；
  研究模式按调用 ID 去重。框架工具只进入范围外诊断，旧版聊天结构不再支持，明确的路由前
  模型服务失败会在调用 Judge 前标记为不可计算。
- `src/collectors/subtask_redundancy.py`：扫描新版聊天 canvas 中跨 Agent/message 的子 Agent
  调用，从对象或 JSON 字符串参数提取任务正文，使用 NFKC、约束词元和字符 bigram 规则
  计算高置信度冗余聚合计数；旧版聊天结构不再支持，研究模式在结构化子任务契约确认前
  显式返回不可计算，任务正文和中间指纹不进入结果。
- `src/collectors/intent_recognition.py`：从新版聊天 canvas 跨 Agent/message 提取 query、
  子 Agent 任务、最终回答和按调用 ID 去重的 MCP 能力，研究模式继续提取规划与调用证据；
  规则负责证据去重、前置失败、缺失字段和冲突诊断，旧版聊天结构不再支持，正文不进入
  持久化结果。
- `src/collectors/time_range_accuracy.py`：复用参数语义目录，聊天模式从新版 canvas 提取
  query、去重工具参数、最终回答和 `meta.create_time` 锚点，研究模式读取事件流；时间正文与
  参数值仅保留在内存 Judge 候选中。
- `src/collectors/evidence_quality.py`：使用显式 MCP 证据工具目录连接新版 canvas/研究调用与
  成功响应；聊天模式按 `traceId + toolId` 跨 Agent/message 关联请求和结果，隔离子 Agent、
  孤立结果及冲突生命周期，旧版 `tool_call_queue` 不再支持；证据按来源原生结构拆分并在
  内存去重，无边界长文本和派生工具不进入指标。
- `src/collectors/statement_support.py`：复用证据单元抽取，提取聊天最终回答或研究模式
  `turn.end` 前最后一个 assistant 文本；区分空证据、缺终态和不完整证据拆分。
- `src/collectors/numeric_consistency.py`：复用陈述支持率的最终回答选择和来源原生证据单元，
  构建不落盘正文的数值一致性 Judge 候选。
- `src/collectors/first_tool_decision_latency.py`：提取聊天请求到最早子 Agent/工具决策，以及
  研究回合到最早工具 pending 事件的时间边界；不保留请求或工具正文。
- `src/collectors/tool_timeout_rate.py`：构建版本化工具 SLA 与别名目录，按聊天工具对象或研究
  `callID` 规范化显式超时、SLA 超限和缺失时间超时；不保留参数或响应正文。
- `src/collectors/__init__.py`：导出工具调用事件规范化类型与入口。
- `src/evaluators/base.py`：`BaseEvaluator` 抽象接口、统一结果对象和计算状态。
- `src/evaluators/tool_call_event_completeness.py`：按唯一调用计算请求与终态同时存在的
  调用事件完整率，并输出重复请求、重复结果和孤立结果计数；聊天调用定义冲突及研究模式
  缺少 `callID` 时严格返回不可计算。
- `src/evaluators/exact_duplicate_tool_calls.py`：使用安全聚合计数计算完全重复调用率，并输出
  不可比较调用、重复请求、重复结果及孤立结果诊断。
- `src/evaluators/mcp_tool_call_inflation.py`：校验 MCP 调用与请求签名计数，计算同任务内相同
  MCP 且相同入参的后续调用占比。
- `src/evaluators/required_parameter_completeness.py`：在 Schema 已覆盖范围内计算必填参数
  微平均完整率；覆盖调用数仅作为诊断，不派生辅助指标。
- `src/evaluators/tool_argument_schema_validity.py`：在 Schema 已覆盖调用上计算参数类型、
  枚举和嵌套结构合法率，仅保留安全诊断计数。
- `src/evaluators/parameter_value_accuracy.py`：定义可注入的版本化 Judge 接口，校验逐字段
  三态输出，隔离单调用失败，并仅输出参数值准确率主指标和安全诊断计数。
- `src/evaluators/openai_compatible_parameter_judge.py`：从 dotenv/环境变量加载 Judge 配置，
  通过 HTTPS OpenAI-compatible Chat Completions 接口请求结构化逐字段判定。
- `src/evaluators/routing.py`：定义路由 Judge 与安全映射表契约，一次生成标准能力集合和
  TP/FP/FN 共享计数，并分别计算 `routing_precision` 与 `routing_recall`；两个指标的新版
  canvas 适配版本均为 `1.1.0`。
- `src/evaluators/subtask_redundancy.py`：校验规则版本、固定阈值和安全聚合计数，计算
  `subtask_redundancy_rate`；新版 canvas 适配版本为 `1.1.0`。
- `src/evaluators/openai_compatible_routing_judge.py`：把整体意图、全部子任务、MCP Schema
  和历史安全映射提交给 OpenAI-compatible Judge，生成严格结构化标准路由。
- `src/evaluators/intent_recognition.py`：定义意图对齐 Judge 契约，严格校验三态 verdict、
  reason code 和 confidence，并把可判定会话转换为二元 `intent_recognition_accuracy`；
  新版 canvas 适配版本为 `1.1.0`。
- `src/evaluators/openai_compatible_intent_judge.py`：把不可信 query 与最小代理行为证据提交给
  OpenAI-compatible Judge；使用 JSON 响应模式，并以通用异常隔离供应商响应和密钥。
- `src/evaluators/time_range_accuracy.py`：严格校验时间字段聚合计数、三态可计算边界和安全
  reason code，接受 `provided/canvas_meta/event/missing` 时间锚点来源并计算
  `time_range_extraction_accuracy`；新版 canvas 适配版本为 `1.1.0`。
- `src/evaluators/openai_compatible_time_range_judge.py`：将 query、相对时间锚点、工具参数及
  MCP 字段说明提交给 OpenAI-compatible Judge，并对供应商异常做脱敏隔离。
- `src/evaluators/evidence_precision.py`：定义证据相关性 Judge 契约，严格校验逐单元三态
  判断，在完整证据分母上计算 `evidence_precision_rate`；新版 canvas 适配版本为 `1.1.0`。
- `src/evaluators/openai_compatible_evidence_judge.py`：把不可信 query 与来源原生证据单元
  提交给 OpenAI-compatible Judge，并以固定 reason code 和通用异常隔离敏感正文。
- `src/evaluators/statement_support.py`：定义原子陈述支持 Judge 契约，严格校验证据引用、
  三态可验证结论和排除项，并计算 `statement_support_rate`。
- `src/evaluators/numeric_consistency.py`：定义直接召回数值对齐 Judge 契约，以 `Decimal` 统一
  单位倍率和展示精度，确定性计算 `retrieved_numeric_consistency_rate`。
- `src/evaluators/openai_compatible_numeric_consistency_judge.py`：将最终回答和来源证据提交给
  OpenAI-compatible Judge，只返回结构化数值对齐信息并隔离供应商异常。
- `src/evaluators/first_tool_decision_latency.py`：校验规范化时间边界，以毫秒确定性计算
  `first_tool_decision_latency_ms`，并隔离缺失、非法和负时延。
- `src/evaluators/tool_timeout_rate.py`：校验策略版本、唯一调用及分类诊断计数，计算
  `tool_timeout_rate`；缺少工具 SLA 时不估算。
- `src/evaluators/openai_compatible_statement_support_judge.py`：把不可信 query、最终回答和
  证据单元提交给 OpenAI-compatible Judge，禁止输出陈述正文并隔离供应商异常。
- `src/evaluators/__init__.py`：导出公共契约和具体评测器。
- `src/reporters/`：跨 Trace 聚合与报告输出；尚待实现。
- `src/storage/intent_route_mappings.py`：以 JSONL 去重保存不含原始文本和实体的意图签名、
  子任务类型与能力集合，并支持 Jaccard 相似映射检索。
- `src/storage/__init__.py`：导出安全意图路由映射存储入口。
- `tests/evaluators/test_base_evaluator.py`：评测器公共契约的行为测试。
- `tests/collectors/test_tool_call_events.py`：新版 canvas 和研究工具事件规范化、跨消息关联、
  重复观察、孤立结果、严格调用 ID、定义冲突和敏感正文隔离测试。
- `tests/collectors/test_exact_duplicate_calls.py`：新版 canvas/研究完全重复识别、跨消息关联、
  请求和结果冲突、孤立结果、生命周期去重、缺失字段及敏感正文隔离测试。
- `tests/collectors/test_mcp_tool_call_inflation.py`：MCP 范围过滤、规范化入参签名、调用 ID
  去重、冲突隔离及双模式敏感正文隔离测试。
- `tests/collectors/test_required_parameters.py`：必填参数目录解析、新版 canvas/研究参数计数、
  子 Agent 排除、跨消息去重、冲突定义、缺失字段、非法结构、占位值、未知 Schema、零必填
  参数及旧版聊天结构拒绝测试。
- `tests/collectors/test_tool_argument_schema.py`：新版 canvas/研究 Schema 校验、子 Agent
  排除、跨消息去重、冲突定义、缺失字段、非法结构、旧版聊天拒绝、类型、数组元素、枚举、
  嵌套必填、额外字段、未知工具和参数解析测试。
- `tests/collectors/test_parameter_value_accuracy.py`：新版 canvas/研究 query 与 Judge 候选提取、
  子 Agent 排除、跨消息去重、冲突定义、缺失字段、非法结构、旧版聊天拒绝、手册描述目录、
  参数解析失败和内存参数不可变测试。
- `tests/collectors/test_routing.py`：新版 canvas 的跨 Agent/message 意图提取、普通工具请求
  去重、缺失字段、非法容器、旧版结构拒绝、前置失败，以及研究前缀映射和冲突定义测试。
- `tests/collectors/test_subtask_redundancy.py`：新版 canvas 跨 Agent/message 扫描、精确与
  高相似冗余、约束差异、JSON 参数、非法事件、旧结构拒绝、敏感正文隔离及研究模式
  不可计算测试。
- `tests/collectors/test_intent_recognition.py`：新版 canvas 的 query、子 Agent、最终回答与
  MCP 能力提取，JSON 参数、证据与调用去重、前置失败、非法结构、旧结构拒绝，以及研究
  模式回归测试。
- `tests/collectors/test_time_range_accuracy.py`：新版 canvas/研究时间候选、锚点优先级、调用
  去重、缺失调用字段、非法结构、旧结构拒绝、无调用零分入口、前置失败和不完整参数观察测试。
- `tests/collectors/test_evidence_quality.py`：显式证据工具范围、新版 canvas 跨 Agent/message
  关联、子 Agent 与孤立结果排除、重复和冲突生命周期、旧结构拒绝、结构化单元拆分、内存
  去重、不可拆分响应及研究回归测试。
- `tests/collectors/test_statement_support.py`：聊天/研究最终回答选择、空证据可计算、前置失败、
  缺终态、部分不可拆分响应和成功产物无正文测试。
- `tests/collectors/test_numeric_consistency.py`：双模式最终回答与证据复用及不完整证据隔离测试。
- `tests/collectors/test_first_tool_decision_latency.py`：聊天乱序决策、子 Agent/工具时间选择、
  研究 pending 去重及缺失时间边界测试。
- `tests/collectors/test_tool_timeout_rate.py`：超时策略校验、聊天/研究字段映射、生命周期去重、
  显式超时、SLA 超限、缺失时间计超时及错误隔离测试。
- `tests/evaluators/test_tool_call_event_completeness.py`：完整率正常、边界、不可计算和
  非法输入隔离测试。
- `tests/evaluators/test_exact_duplicate_tool_calls.py`：完全重复率计算、零值、空调用、
  规范化错误和非法计数测试。
- `tests/evaluators/test_mcp_tool_call_inflation.py`：膨胀率、零膨胀、零分母、规范化失败和非法
  聚合计数测试。
- `tests/evaluators/test_required_parameter_completeness.py`：必填参数完整率、辅助指标排除、
  零分母、规范化错误和非法聚合计数测试。
- `tests/evaluators/test_tool_argument_schema_validity.py`：Schema 合法率、辅助指标排除、
  零分母、规范化错误和非法聚合计数测试。
- `tests/evaluators/test_parameter_value_accuracy.py`：Judge 三态字段聚合、辅助指标排除、
  部分失败隔离、非法 Judge 输出和不可计算边界测试。
- `tests/evaluators/test_openai_compatible_parameter_judge.py`：dotenv 配置、HTTPS 边界、请求
  结构、认证头、结构化响应解析和供应商异常脱敏测试。
- `tests/evaluators/test_routing.py`：共享 Judge 调用、Precision/Recall 集合计数、零分母、
  Judge 非法输出、失败脱敏和安全映射写入测试。
- `tests/evaluators/test_subtask_redundancy.py`：冗余率、单任务、零分母、规范化错误和非法
  聚合计数测试。
- `tests/evaluators/test_openai_compatible_routing_judge.py`：整体/子任务意图、MCP Schema 与
  历史映射的结构化请求测试。
- `tests/evaluators/test_intent_recognition.py`：二元会话得分、不可判定、Judge 失败、严格
  输出白名单、规范化错误和敏感正文隔离测试。
- `tests/evaluators/test_openai_compatible_intent_judge.py`：意图证据请求结构、确定性采样、
  JSON 响应解析和供应商异常脱敏测试。
- `tests/evaluators/test_time_range_accuracy.py`：字段准确率、正常零分、不可计算边界、严格
  Judge 输出校验和敏感异常隔离测试。
- `tests/evaluators/test_openai_compatible_time_range_judge.py`：时间需求、锚点、Schema 语义与
  工具参数的结构化请求及供应商异常脱敏测试。
- `tests/evaluators/test_evidence_precision.py`：查准率、完整分母、三态 Judge、严格输出校验、
  规范化错误和敏感正文隔离测试。
- `tests/evaluators/test_openai_compatible_evidence_judge.py`：证据单元请求结构、确定性采样、
  JSON 响应解析和供应商异常脱敏测试。
- `tests/evaluators/test_statement_support.py`：支持率、正常零分、无可验证陈述、不可判定、
  Judge 完整性、证据引用和敏感正文隔离测试。
- `tests/evaluators/test_numeric_consistency.py`：单位倍率、正常舍入、缺失召回值、排除项、
  不可判定、非法 Judge 输出和敏感正文隔离测试。
- `tests/evaluators/test_first_tool_decision_latency.py`：双模式毫秒计算、不可计算原因、非法
  规范化输入和负时延隔离测试。
- `tests/evaluators/test_tool_timeout_rate.py`：双模式超时率、超时分类计数、缺失 SLA、非法
  规范化输入和错误隔离测试。
- `tests/evaluators/test_openai_compatible_numeric_consistency_judge.py`：数值对齐请求结构、
  确定性采样、JSON 响应和供应商异常脱敏测试。
- `tests/evaluators/test_openai_compatible_statement_support_judge.py`：最终回答与证据请求结构、
  JSON 响应解析和供应商异常脱敏测试。
- `tests/storage/test_intent_route_mappings.py`：映射表字段安全、记录去重、加载和相似签名
  检索测试。
- `docs/specs/metrics.md`：按开发顺序集中记录所有指标的定义和原始字段映射。
- `tests/`：测试包根目录；子目录结构与 `src/` 对应。
- `configs/evaluation.example.toml`：提交安全的评测配置样例。
