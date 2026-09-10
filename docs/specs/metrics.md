# 指标定义与字段映射

本文集中记录所有评测指标的定义和原始日志字段映射。后续指标按
`docs/specs/metric-development-order.md` 的顺序追加，不再为单个指标创建独立规格文件。

### 新版聊天模式统一口径与重算范围

本文件中的聊天模式字段映射以新版 JSON 数组日志为准。重算样本为 336 条 Trace，原始日志
只用于本地验算，不进入仓库。统一边界如下：

- 单次执行标识：`canvas.meta.traceId`；多轮会话标识：`canvas.meta.sessionId`。指标默认以
  `traceId` 为单次评测边界，不使用可能重复的 `chatId` 或 `recordId`。
- 普通工具请求：`canvas.agents[].messages[].content.tool_call[]` 中同时具有非空
  `toolId` 和 `toolName` 的对象；唯一调用键为 `traceId + toolId`。
- 子 Agent 调用：同一数组中具有 `agentName` 且没有 `toolId` 的对象；它只进入子任务和
  编排行为指标，不进入普通工具调用分母。
- 工具结果：`canvas.agents[].messages[].content.tool_result[]`，在同一 Trace 内按
  `toolId` 与请求关联。只出现于结果侧的 `direct_answer`、`worker_*` 返回是 Agent 返回，
  不能反向生成普通工具调用。
- 用户需求、请求时间和最终回答分别读取 `canvas.meta.query`、
  `canvas.meta.create_time` 和 `canvas.answer.answer`。
- MCP 范围只按 `tool_call[].toolName -> mcp_tool_manual.json.tools[].name` 精确解析；
  `agentName` 和 `agentId` 都不是 MCP 工具标识。

本次只对结构确定且不依赖外部 Judge 或生产 SLA 的指标给出真实数值。需要 Judge、显式证据
工具目录或生产 SLA 的指标只重算候选范围和可计算性，不伪造最终得分。

### OpenAI-compatible Judge 统一执行约定

参数值准确率、路由 Precision/Recall、意图识别准确率、时间范围提取准确率和陈述支持率
共用同一 Judge 运行时：

- 首选严格 `response_format.type = json_schema`；仅当供应商以 HTTP 400 拒绝该格式时，回退
  一次 `json_object`。无论供应商是否执行 Schema，evaluator 都必须再次校验业务约束。
- 默认单个请求体上限为 512 KiB，超过上限不发送网络请求，返回
  `judge_input_too_large`。默认最多尝试 2 次，对超时、连接失败、HTTP 429 和 HTTP 5xx
  执行指数退避重试；其他 HTTP 错误不重试。
- `finish_reason = length` 显式归类为 `judge_output_truncated`，不得把截断 JSON 混同为普通
  Schema 错误。传输和响应错误使用稳定原因码：`judge_timeout`、`judge_rate_limited`、
  `judge_http_5xx`、`judge_http_error`、`judge_connection_error`、`judge_transport_error`、
  `judge_invalid_json`、`judge_invalid_response`、`judge_empty_response`。
- 持久化诊断仅允许请求/响应字节数、尝试次数、请求上限和 HTTP 状态码；不得记录请求正文、
  响应正文、密钥或供应商错误正文。

## 1. 完全重复调用率

### 指标定义

稳定指标名：`exact_duplicate_tool_call_rate`

```text
完全重复调用率 =
  工具名称、规范化参数和响应内容均与此前调用相同的额外调用数
  ÷ 已识别的唯一工具调用总数
```

- 第一次出现的调用不计重复，后续与任一更早调用完全相同的调用各计一次。
- 参数不同、分页不同或时间范围不同的调用不属于完全重复。
- 相同请求产生不同响应时视为获得新内容，不计入分子。
- 缺少响应内容的调用计入分母，但不计入分子，并记录不可比较数量。
- 没有工具调用时返回 `NOT_COMPUTABLE`，原因 `no_tool_calls`。
- 缺少工具名称或参数时返回 `NOT_COMPUTABLE`，原因 `missing_duplicate_key`。
- 本指标是可审计的完全重复下界，不推断调用在业务上是否必要。

### 字段映射

聊天模式：

- 单次执行标识：`canvas.meta.traceId`。
- 调用候选：`canvas.agents[].messages[].content.tool_call[]` 中同时具有非空 `toolId` 和
  `toolName` 的普通工具请求；具有 `agentName` 且没有 `toolId` 的子 Agent 调用不进入范围。
- 唯一调用：按 `traceId + toolId` 去重。同一 `toolId` 的重复请求定义只计一次；工具名称或
  规范化参数不一致时返回 `NOT_COMPUTABLE / conflicting_call_definition`。
- 工具名称和参数：普通请求的 `toolName/arguments`。缺少 `toolId`、`toolName` 或
  `arguments` 时分别返回 `missing_call_id`、`missing_tool_name` 或
  `missing_call_arguments`，不得使用数组位置补造调用 ID。
- 响应关联：扫描同一 Trace 的全部 `content.tool_result[]`，按 `toolId` 跨 Agent、跨 message
  关联请求；结果侧非空 `toolName` 与请求不一致时返回
  `conflicting_call_definition`。
- 响应内容：优先使用具有实际内容的 `result`，否则使用具有实际内容的 `rawResult`。`null`、
  空字符串、空数组和空对象均视为没有可比较响应正文。
- 同一调用存在多个结果时，内容摘要相同的重复观察合并并记录诊断；多个非空响应内容不一致
  时返回 `NOT_COMPUTABLE / conflicting_call_result`，不得任意选择其中一个。
- 找到结果事件但没有可比较正文，或请求没有匹配结果时，该调用进入总分母但不进入可比较
  分母。只出现于结果侧的 Agent 返回只记录 `orphan_result_count`，不反向生成普通工具调用。

研究模式：

- 会话标识：`sessionID + userMessageID`。
- 唯一调用：模型请求中的 `result.toolCalls[].toolCallId` 与工具状态事件中的
  `part.callID` 的并集，并按 `callID` 去重。
- 工具名称和参数：`result.toolCalls[].toolName/input`。
- 响应内容：终态 `part.state.output`，为空时使用 `part.state.raw`；终态状态参与比较，
  避免把相同正文但不同执行状态误判为完全重复。
- 相同 `callID + status` 的重复生命周期事件只记录诊断，不增加调用数。

参数和响应会在内存中按稳定键序列化并比较 SHA-256 摘要。JSON 对象字符串会先解析，
因此字段顺序和 JSON 表示形式不同但内容相同的结果仍视为重复。摘要及原始正文均不进入
规范化结果或评测元数据。

### 模拟计算

```text
唯一调用数 = 4
可比较调用数 = 3
完全重复的额外调用数 = 1
完全重复调用率 = 1 ÷ 4 = 0.25
```

真实日志微平均验算：新版聊天模式共有 4,266 次调用，其中 3,389 次响应可比较，完全重复的
额外调用为 26 次，`26 / 4266 ≈ 0.00609`；877 次因缺少可比较响应正文只进入总分母。
研究模式仍为 `1 / 30 ≈ 0.03333`。

## 2. 必填参数完整率

### 指标定义

稳定指标名：`required_parameter_completeness_rate`

```text
必填参数完整率 =
  Schema 已覆盖调用中已填充的必填参数总数
  ÷ Schema 已覆盖调用要求的必填参数总数
```

- 采用微平均口径；每个必填参数位置贡献一次分子或分母计数。
- 仅检查顶层必填参数是否存在且非空。类型、枚举、格式和嵌套字段约束由后续
  `Schema 合法率` 判断。
- 数值 `0` 和布尔值 `false` 是有效已填值。
- 缺失、`null`、空白字符串、空数组、空对象，以及完整值为 `null`、`none`、
  `undefined`、`n/a`、`na`、`todo`、`tbd`、`unknown`、`待补充` 或 `未知` 的
  不区分大小写字符串均视为未填。
- 参数 JSON 无法解析为对象时，该调用要求的必填参数全部进入分母但不进入分子，
  并记录 `malformed_arguments_call_count`。
- 未知工具 Schema 不进入分子或分母；Schema 覆盖调用数和总调用数仅作为诊断计数保留，
  不派生或输出独立覆盖率指标。
- Schema 没有顶层必填参数的调用不进入分子或分母，并记录
  `zero_required_parameter_call_count`。
- 没有工具调用、没有 Schema 覆盖调用，或覆盖范围内没有必填参数时分别返回
  `NOT_COMPUTABLE`，原因是 `no_tool_calls`、`no_schema_covered_calls` 或
  `no_required_parameters_in_scope`。
- 结果元数据保留 MCP 手册的 `manual_version`，保证计算口径可追溯。

### 字段映射

Schema：

- 工具名：`mcp_tool_manual.json` 的 `tools[].name`。
- 顶层必填参数：对应工具 `parameters[]` 中 `required == true` 的 `name`。
- 手册版本：`manual_version`。

聊天模式：

- 单次执行标识：`canvas.meta.traceId`。
- 调用候选：扫描 `canvas.agents[].messages[].content.tool_call[]`。具有 `agentName` 且没有
  `toolId` 的对象属于子 Agent 调用，不进入范围；其余具有 `toolId`、`toolName` 或
  `arguments` 任一普通请求字段的对象按普通工具请求校验完整性。
- 唯一调用：按 `traceId + toolId` 去重。同一 `toolId` 的重复请求定义中，工具名称和
  规范化参数一致时合并为一次调用并记录重复定义；两者任一不一致时返回
  `NOT_COMPUTABLE / conflicting_call_definition`。
- 工具名称和参数：普通请求的 `toolName/arguments`。参数为对象或可解析为对象的 JSON
  字符串时可计算；不能解析为对象时，该 Schema 已覆盖调用要求的必填参数全部进入分母，
  但不进入分子，并记录 `malformed_arguments_call_count`。
- 缺少 `traceId`、`toolId`、`toolName` 或 `arguments` 时分别返回
  `NOT_COMPUTABLE / missing_trace_id`、`missing_call_id`、`missing_tool_name` 或
  `missing_call_arguments`；不得使用数组位置补造调用 ID。
- `canvas`、`agents`、`messages`、`content` 或 `tool_call` 的容器类型不符合约定时返回
  `NOT_COMPUTABLE / invalid_chat_trace_structure`。本指标不读取 `tool_result`。

研究模式：

- 会话标识：`sessionID + userMessageID`。
- 唯一调用：`result.toolCalls[].toolCallId`，按 `toolCallId` 去重。
- 工具名称和参数：`result.toolCalls[].toolName/input`。
- 缺少 `toolCallId` 时无法可靠去重，返回 `NOT_COMPUTABLE`，原因
  `missing_call_id`；相同 ID 的重复定义只记录安全诊断，冲突定义返回不可计算。

规范化阶段只输出调用数、覆盖数、必填参数分子分母、手册版本和安全诊断计数。
研究模式仅在扫描期间保留调用参数，长期去重状态是 `callID` 对应的 SHA-256 摘要；
参数值、参数 JSON 和摘要均不进入规范化结果或评测元数据。

### 模拟计算

```text
总调用数 = 5
Schema 覆盖调用数 = 4
其中无必填参数调用数 = 1
要求的必填参数总数 = 6
已填充的必填参数总数 = 4

必填参数完整率 = 4 ÷ 6 ≈ 0.66667
```

真实日志微平均验算：新版聊天模式 4,266 次调用中，2,911 次被当前 MCP 手册覆盖，2,450 次
涉及至少一个顶层必填参数；已填 3,382 个、要求 4,015 个，
`3382 / 4015 ≈ 0.84234`。另有 1,355 次手册外调用和 461 次零必填参数调用。研究模式仍为
`10 / 11 ≈ 0.90909`。

## 3. Schema 合法率

### 指标定义

稳定指标名：`tool_argument_schema_validity_rate`

```text
Schema 合法率 =
  参数结构通过对应版本 Schema 校验的唯一调用数
  ÷ Schema 已覆盖的唯一调用数
```

- 顶层必填参数缺失不在本指标重复扣分，由 `required_parameter_completeness_rate` 负责。
- 已出现字段必须符合手册声明的类型；`integer` 和数值数组元素不接受布尔值，`number`
  及其数组元素必须是有限数值。
- 标量枚举值必须位于 `enum` 中；数组参数的每个元素都必须位于对应枚举中。
- `object` 与 `object[]` 按 `nested` 递归校验；父对象存在时，嵌套必填字段必须存在。
- 手册未声明 `additionalProperties: false`，因此额外字段不使调用非法，但记录
  `undeclared_argument_call_count`。
- 描述文本中的日期格式、数值范围和业务约束不是机器可执行 Schema，本指标不解析；
  这些规则由后续参数值准确率评测。
- 未知工具 Schema 不进入分子或分母；覆盖调用数和总调用数仅作为诊断计数保留。
- 结果保留 `manual_version`，并只输出调用级聚合计数和违规类别，不输出参数值。

### 字段映射

Schema：

- 工具名：`mcp_tool_manual.json` 的 `tools[].name`。
- 参数结构：递归读取 `parameters[]` 与 `nested[]` 的 `name`、`type`、`required` 和
  `enum`。
- 支持类型：`string`、`integer`、`number`、`boolean`、`object`、`array`、
  `string[]`、`integer[]`、`number[]`、`object[]`。
- 手册版本：`manual_version`。

聊天模式：

- 单次执行标识：`canvas.meta.traceId`。
- 调用候选：扫描 `canvas.agents[].messages[].content.tool_call[]`。具有 `agentName` 且没有
  `toolId` 的对象属于子 Agent 调用，不进入范围；其余具有 `toolId`、`toolName` 或
  `arguments` 任一普通请求字段的对象按普通工具请求校验完整性。
- 唯一调用：按 `traceId + toolId` 去重。同一 `toolId` 的重复请求定义中，工具名称和
  规范化参数一致时合并为一次调用并记录重复定义；两者任一不一致时返回
  `NOT_COMPUTABLE / conflicting_call_definition`。
- 工具名称和参数：普通请求的 `toolName/arguments`。参数为对象或可解析为对象的 JSON
  字符串时进入 Schema 校验；不能解析为对象时，该 Schema 已覆盖调用计为非法并记录
  `malformed_arguments_call_count`。
- 缺少 `traceId`、`toolId`、`toolName` 或 `arguments` 时分别返回
  `NOT_COMPUTABLE / missing_trace_id`、`missing_call_id`、`missing_tool_name` 或
  `missing_call_arguments`；不得使用数组位置补造调用 ID。
- `canvas`、`agents`、`messages`、`content` 或 `tool_call` 的容器类型不符合约定时返回
  `NOT_COMPUTABLE / invalid_chat_trace_structure`。本指标不读取 `tool_result`。

研究模式：

- 会话标识：`sessionID + userMessageID`。
- 唯一调用：`result.toolCalls[].toolCallId`，按 `toolCallId` 去重。
- 工具名称和参数：`result.toolCalls[].toolName/input`。
- 缺少调用 ID 返回 `NOT_COMPUTABLE / missing_call_id`；相同 ID 定义冲突返回
  `NOT_COMPUTABLE / conflicting_call_definition`。

参数无法解析为对象时，该 Schema 已覆盖调用计为非法并记录
`malformed_arguments_call_count`。没有调用或没有 Schema 覆盖调用时分别返回
`NOT_COMPUTABLE / no_tool_calls` 和 `NOT_COMPUTABLE / no_schema_covered_calls`。

### 模拟计算

```text
全部唯一调用数 = 5
Schema 覆盖调用数 = 4

结构合法调用数 = 2
类型非法调用数 = 1
嵌套必填字段缺失调用数 = 1
未知 Schema 调用数 = 1

Schema 合法率 = 2 ÷ 4 = 0.5
```

真实日志微平均验算：新版聊天模式 2,911 次手册覆盖调用中 2,880 次合法、31 次非法，
`2880 / 2911 ≈ 0.98935`；违规诊断为 27 次类型违规、4 次枚举违规、0 次嵌套必填缺失，
另有 2 次调用携带额外字段但按当前口径仍合法。研究模式仍为 `5 / 5 = 1.0`。

## 4. 参数值准确率

### 指标定义

稳定指标名：`parameter_value_accuracy_rate`

```text
参数值准确率 =
  Judge 判定为 correct 的字段数
  ÷ Judge 可判定字段数

Judge 可判定字段数 = correct 字段数 + incorrect 字段数
```

- Pydantic 与 `tool_argument_schema_validity_rate` 负责类型、枚举和结构合法性；本指标
  不重复判断输入格式，只判断参数值是否忠实于用户 query。
- Judge 对一个调用整体判断一次，但程序先为每个实际参数字段分配稳定 `field_id`；Judge
  只能对这些 ID 返回 `correct`、`incorrect` 或 `not_assessable`，不得生成或改写字段路径。
- query 明确约束且工具字段能够承载的遗漏参数通过独立的 `omitted_schema_fields` 候选识别；
  Judge 只返回缺失字段 ID，程序固定映射为 `incorrect / missing_query_required`。
- 实际出现的每个参数叶子字段都必须得到 Judge 判定。非空对象递归展开为点分路径；数组
  作为一个整体字段判断，避免元素级计数放大长数组权重。
- query 未约束、无法从安全上下文证明的默认值或运行参数标记为 `not_assessable`，不进入
  准确率分母；该状态只保留诊断计数，不派生额外指标。
- 工具选择错误由后续路由指标负责，不得因为 Judge 认为工具不合适而把全部参数自动判错。
- 若参数依赖前序工具结果而原始 query 不足以判断，第一版必须返回 `not_assessable`，不得
  猜测；后续可增加经过脱敏且版本化的调用意图上下文。

### Judge 契约

每个 Judge 请求包含：

- `trace_id`、`mode`、稳定 `call_id`；这些字段仅用于调用关联，不作为语义证据。
- 原始用户 `query`。
- 工具名称和工具描述。
- 由程序生成的 `actual_fields`：稳定字段 ID、原始字段路径、对应字段说明和实际值。数组作为
  一个整体字段传递，不允许 Judge 展开索引或内部对象路径。
- 由程序生成的 `omitted_schema_fields`：仅包含本次未传入的 Schema 字段 ID、路径和说明。

Judge 适配器必须声明稳定的 `judge_name`、`judge_version` 和 `prompt_version`，并返回：

```json
{
  "field_0001": "correct",
  "field_0002": "not_assessable",
  "missing_required_field_ids": ["omitted_0001"]
}
```

动态 JSON Schema 将每个实际字段 ID 固定为必填顶层键；Judge 只返回三态 verdict，不返回
字段路径、置信度或原因文本。适配器把 ID 映射回原始路径，并按 verdict 生成稳定安全原因码。
`missing_required_field_ids` 只能引用程序提供的遗漏候选；供应商兼容模式在没有遗漏字段时若
省略该键或返回 `null`，按空列表处理。未知、重复或缺失的实际字段 ID 仍按单调用
`invalid_judge_output` 隔离。

### 字段映射

MCP 手册：

- 工具名称和用途：`mcp_tool_manual.json` 的 `tools[].name/description`。
- 参数语义：递归读取 `parameters[]/nested[]` 的 `name` 和 `description`。
- 手册版本：`manual_version`。

聊天模式：

- 用户需求：`canvas.meta.query`。
- 单次执行标识：`canvas.meta.traceId`。
- 调用候选：扫描 `canvas.agents[].messages[].content.tool_call[]`。具有 `agentName` 且没有
  `toolId` 的对象属于子 Agent 调用，不进入范围；其余具有 `toolId`、`toolName` 或
  `arguments` 任一普通请求字段的对象按普通工具请求校验完整性。
- 唯一调用：普通请求的 `toolId` 与 `traceId` 组成复合键。同一 `toolId` 的工具名称和
  规范化参数一致时合并并记录重复定义；两者任一不一致时返回
  `NOT_COMPUTABLE / conflicting_call_definition`。
- 工具名称和参数：普通请求的 `toolName/arguments`。参数为对象或可解析为对象的 JSON
  字符串时可构造 Judge 候选；不能解析为对象时记录 `malformed_arguments_call_count`，
  且该调用不提交 Judge。
- 缺少 `traceId`、query、`toolId`、`toolName` 或 `arguments` 时分别返回
  `NOT_COMPUTABLE / missing_trace_id`、`missing_query`、`missing_call_id`、
  `missing_tool_name` 或 `missing_call_arguments`；不得使用数组位置补造调用 ID。
- `canvas`、`meta`、`agents`、`messages`、`content` 或 `tool_call` 的容器类型不符合约定时
  返回 `NOT_COMPUTABLE / invalid_chat_trace_structure`。本指标不读取 `tool_result`。

研究模式：

- 用户需求：优先读取事件顶层 `query`，兼容首个请求事件的 `body.query`。
- 会话标识：`sessionID + userMessageID`。
- 调用标识：`result.toolCalls[].toolCallId`，按相同定义去重。
- 工具名称和参数：`result.toolCalls[].toolName/input`。

缺少 query、调用 ID 或调用定义冲突时分别返回 `NOT_COMPUTABLE / missing_query`、
`missing_call_id` 或 `conflicting_call_definition`。没有调用、没有手册覆盖调用或没有可判定
字段时分别返回 `no_tool_calls`、`no_schema_covered_calls` 或 `no_assessable_fields`。

单个 Judge 超时、异常或返回非法结构时增加 `judge_failed_call_count`，其他调用继续计算；
全部候选调用失败且原因唯一时返回对应稳定原因码；混合失败时返回
`ERROR / judge_failure`。非法业务输出记为 `invalid_judge_output`。结果只保留聚合计数、
失败原因计数及版本，不得
持久化 query、参数值、Judge 原始响应或模型推导出的标准答案。

### 模拟计算

```text
Schema 已覆盖且提交 Judge 的调用数 = 3
Judge 成功调用数 = 2
Judge 失败调用数 = 1

correct 字段数 = 2
incorrect 字段数 = 2
not_assessable 字段数 = 1

参数值准确率 = 2 ÷ (2 + 2) = 0.5
```

最终 `EvaluationResult` 只包含 `parameter_value_accuracy_rate` 这一项指标。调用完全匹配率、
Judge 可判定覆盖率和 Schema 覆盖率不计算，也不得写入最终结果；调用数、字段计数、失败数
和原因类别仅作为诊断元数据保留。

OpenAI-compatible 适配器使用 `JUDGE_API_URL`、`JUDGE_API_KEY` 和 `JUDGE_MODEL`，并记录
固定模型与版本化 prompt。远程 URL 必须使用 HTTPS；query 和参数按不可信数据处理，禁止
其中的文本覆盖系统评测规则。未配置适配器或真实请求未成功时不得伪造语义得分。
evaluator 版本为 `1.4.0`，Judge prompt 版本为 `1.2.0`。

新版聊天日志重算状态：4,266 次普通调用中可构造 2,911 个手册覆盖 Judge 候选，覆盖 271 条
Trace。当前未执行 336 条 Trace 的真实全量 Judge，因此不产生全量正式基线。

真实前 30 条新版聊天 Trace 使用 `qwen-flash` 验证字段 ID 契约：493 次普通调用中 359 次
被手册覆盖，359/359 次 Judge 成功，0 次非法输出；可判定字段 1,032 个，其中 868 个正确、
164 个错误，微平均 `868 / 1032 ≈ 0.84109`。25 条 Trace 得到分数，1 条全部字段不可判定，
2 条无工具调用，2 条无手册覆盖调用。研究日志 30 次唯一调用中 5 次被手册覆盖，5/5 次
Judge 成功；可判定字段 19 个，其中 12 个正确、7 个错误，结果 `12 / 19 ≈ 0.63158`。

## 5-6. 路由 Precision 与路由 Recall

### 指标定义

稳定指标名：`routing_precision`、`routing_recall`

两项指标共享一次 LLM 路由标准生成和同一组集合计数：

```text
正确路由能力集合 = 实际能力集合 ∩ 标准能力集合

路由 Precision = 正确路由能力数 ÷ 实际能力数
路由 Recall = 正确路由能力数 ÷ 标准能力数
```

- 采用会话级能力集合口径，同一能力调用多次只计一次；重复调用由
  `exact_duplicate_tool_call_rate` 单独评估，避免重复惩罚或加分。
- 手册内标准能力集合不是从实际调用反推。路由 Judge 必须综合整体用户意图、所有可见子任务
  意图、完整 MCP 能力目录以及历史安全映射，选择完成需求所需的最小充分能力集合。
- 两项指标都只评估路由选择层。调用是否成功属于执行质量，不混入路由选择判断。
- 日志自身明确声明为 MCP、但尚未进入当前手册的工具，按用户确认口径视为符合意图要求：
  同一工具在 Trace 内去重后同时补入实际能力数、标准能力数和 TP，并记录
  `assumed_matching_mcp_route_count`。聊天模式只信任 `canvas.meta.tools` 声明；研究模式只
  信任 `agent.mcp[].tools` 声明。无法由这些结构证明为 MCP 的 `respond`、`excel` 等框架或
  环境工具仍不进入集合，只记录 `out_of_scope_tool_call_count`，不得根据名称模糊猜测。
- 实际能力集合为空而标准集合非空时，Precision 返回
  `NOT_COMPUTABLE / no_actual_routes`，Recall 等于 `0`。
- 标准集合为空而实际集合非空时，Precision 等于 `0`，Recall 返回
  `NOT_COMPUTABLE / no_expected_routes`；两者均为空时两项均不可计算。
- 缺少意图、调用 ID、工具名或存在冲突调用定义时返回对应的 `NOT_COMPUTABLE`；Judge
  异常或非法输出分别返回 `judge_failure` 或 `invalid_judge_output`，不得估算。
- 当聊天日志没有任何工具调用，且最终回答明确为模型服务暂时不可用时，判定为路由前
  基础设施失败；两项均返回 `NOT_COMPUTABLE / pre_routing_failure`，且不得调用 Judge。
  正常执行但没有调用能力、同时标准能力集合非空的日志，Recall 才按 `0` 计算。

### LLM 与 MCP Schema 契约

Judge 请求包含：

- 顶层用户意图和日志中全部可见子任务意图；这些正文只在单次调用内存中存在。
- MCP 手册中每个能力的 `capability_id`、工具描述、参数名称、类型、必填标记和描述。
- 最多 50 条不含原始文本的历史意图路由映射，作为可选参考而非强制答案。

Judge 只返回子任务：

```json
{
  "subtasks": [
    {
      "intent_signature": ["market_data", "valuation"],
      "subtask_type": "company_analysis",
      "required_capability_ids": ["pricePerformance"]
    }
  ]
}
```

- `intent_signature` 和 `subtask_type` 只能使用小写 snake_case，禁止包含公司、人名、
  证券代码、日期、金额、地区或用户原文。
- 标准能力集合由程序对所有子任务的 `required_capability_ids` 求去重并集，不要求 Judge
  重复返回顶层集合，避免两个等价字段产生不一致。
- 所有能力 ID 必须存在于当前 MCP 目录；评测器会重新校验，不信任 Judge 输出。
- `intent_signature` 必须包含 1 至 8 个唯一的小写 ASCII `snake_case` 标签，`subtask_type`
  也必须符合相同安全格式。JSON Schema 使用 `pattern/minItems/maxItems/uniqueItems` 尽量在
  生成阶段约束，评测器仍执行二次校验。
- Judge 返回可解析 JSON 但业务校验失败时，允许携带稳定且不含正文的错误码重试一次；第二次
  仍存在非法意图签名或子任务类型时，只跳过对应的可复用映射写入，已通过目录校验的能力
  集合仍可用于指标计算，并在安全元数据记录具体原因；能力 ID 或主体结构仍非法时返回
  `NOT_COMPUTABLE / invalid_judge_output`。
- Judge 适配器记录 `judge_name`、`judge_version`、`prompt_version` 和 MCP
  `schema_version`，保证生成口径可追溯。
- OpenAI-compatible 请求固定 `temperature = 0` 以降低重复运行波动；在人工校准集冻结前，
  结果仍属于 Judge 代理标准，不声明为完全确定的 Golden Benchmark。

### 字段映射

MCP 手册：

- 能力 ID：`mcp_tool_manual.json` 的 `tools[].name`。
- 能力语义：`tools[].description` 和 `parameters[]` 的名称、类型、必填标记及描述。
- 研究模式的 `mcp__<server>__<tool>` 名称可按末段工具名映射到同一能力 ID；其他别名
  必须显式登记。

聊天模式：

- 整体意图：`canvas.meta.query`。
- 调用候选：扫描 `canvas.agents[].messages[].content.tool_call[]`。
- 子任务意图：具有 `agentName` 且没有 `toolId` 的子 Agent 调用；从对象或可解析为对象的
  `arguments.query/arguments.task` 提取，并在 Trace 内按正文去重。`agentName` 只表示 worker
  类型，不能单独作为任务语义；没有可靠任务文本的子 Agent 调用只记录安全诊断。
- 实际能力：同时具有 `toolId` 和 `toolName` 的普通工具请求；按 `traceId + toolId` 去重后，
  将 `toolName` 通过 MCP 手册解析为能力 ID，并在 Trace 内形成集合。同一 `toolId` 重复出现
  且工具名一致时合并；工具名不一致时返回
  `NOT_COMPUTABLE / conflicting_call_definition`。参数差异不影响路由能力集合。手册解析失败
  但 `canvas.meta.tools` 明确声明的工具按唯一名称计入自动匹配 MCP 数量。
- 缺少 `traceId`、整体意图、普通调用 ID 或工具名时分别返回
  `NOT_COMPUTABLE / missing_trace_id`、`missing_intent`、`missing_call_id` 或
  `missing_tool_name`；不得使用数组位置补造调用 ID。
- `canvas`、`meta`、`agents`、`messages`、`content` 或 `tool_call` 的容器类型不符合约定时
  返回 `NOT_COMPUTABLE / invalid_chat_trace_structure`。本指标不读取 `tool_result`。
- 没有普通工具请求且 `canvas.answer.answer` 明确为固定模型服务失败文案时返回
  `NOT_COMPUTABLE / pre_routing_failure`，并跳过 Judge。

研究模式：

- 整体意图：优先读取事件顶层 `query`，兼容 `body.query`。
- 可见步骤意图：`body.task` 和 `body.description`。
- 实际能力：`result.toolCalls[].toolName`，按 `toolCallId` 去重；同一 ID 对应不同工具名
  时返回 `conflicting_call_definition`。手册解析失败但可由 `agent.mcp[].tools` 的完整名称、
  三下划线命名空间后缀或明确服务名前缀映射确认的工具，按唯一名称计入自动匹配 MCP 数量。

### 可复用意图路由映射

通过 `JsonlIntentRouteMappingStore` 将每条成功判定日志中的去实体化子任务映射写入被忽略的
运行产物目录。每条记录只包含：

- `intent_signature`、`subtask_type` 和 `capability_ids`。
- MCP Schema、Judge、模型和 prompt 版本。

存储会去除完全重复记录，并支持按意图签名 Jaccard 相似度查找历史映射。映射表不得保存
原始 query、子任务正文、实体、参数、响应、Trace ID 或 Judge 原始输出。读取或写入映射表
失败只记录安全诊断，不改变已经生成的标准能力集合和指标结果。

新版聊天日志重算状态：312/336 条 Trace 可构造路由 Judge 候选，24 条为明确路由前失败；
可评估候选中实际 MCP 能力集合按 Trace 去重后合计 950 项，意图片段合计 1,507 项。全部
336 条规范化对象共提取 1,531 个意图片段，其中 24 个属于前置失败且不会提交 Judge。由于
本次未运行路由 Judge，没有标准能力集合，因此不得计算 Precision 或 Recall。

新版聊天适配按两个批次分别验收 Precision 与 Recall；两者共用一次 Judge 和 TP/FP/FN
计数，`routing_precision` 与 `routing_recall` 的 evaluator 版本现均为 `1.3.0`；Judge
prompt 版本为 `1.2.0`。共享运行时失败会保留对应稳定原因码，不再统一折叠为
`judge_failure`。

新版聊天前 30 条加指定研究日志的真实复验：31 条 Trace 中 2 条为
`pre_routing_failure`；日志声明的手册外 MCP 共形成 13 个唯一自动匹配路由。Precision
可计算 26 条，微平均 `50 / (50 + 41) ≈ 0.54945`；Recall 可计算 27 条，微平均
`50 / (50 + 74) ≈ 0.40323`。11 条触发一次业务校验重试，其中 4 条重试后仍有非法映射标签，
但合法能力集合正常计分且不写入映射表；2 条因连续返回目录外且日志未声明的能力 ID 而保持
`NOT_COMPUTABLE / invalid_judge_output`。研究日志结果为 Precision `0.5`、Recall `0.75`。

### 模拟计算

```text
标准能力集合 = {行情, 新闻, 研报}
实际能力集合 = {行情, 新闻, 日历}

TP = 2
FP = 1
FN = 1

路由 Precision = 2 ÷ 3 ≈ 0.66667
路由 Recall = 2 ÷ 3 ≈ 0.66667
```

## 7. 子任务冗余率

### 指标定义

稳定指标名：`subtask_redundancy_rate`

```text
子任务冗余率 =
  规则识别的高置信度冗余子任务数 ÷ 实际生成的有效子任务总数
```

- 本指标使用确定性规则，不调用 LLM，也不增加第三方依赖。
- 第一次出现的子任务不计冗余；后续子任务最多计一次冗余。
- 规则先执行 Unicode NFKC、英文小写化、空白与标点消除，再比较规范化文本。
- 规范化文本完全相同的后续子任务直接判为精确重复。
- 非精确重复只有在数字、日期、百分比和字母数字标识等约束词元集合完全一致，字符
  bigram Jaccard 相似度不低于 `0.90`，且后续任务新增 bigram 比例不高于 `0.10` 时，
  才判为高相似冗余。
- 该结果是高置信度冗余下界，不推断规则无法可靠识别的同义改写。阈值和算法变化必须
  升级 `evaluator_version`。
- 没有子任务时返回 `NOT_COMPUTABLE / no_subtasks`；只有一个有效子任务时结果为 `0`。
- 子任务事件存在但 `arguments` 或任务正文非法时返回
  `NOT_COMPUTABLE / invalid_subtask_event`，不得静默缩小分母。
- 缺少 `canvas.meta.traceId` 时返回 `NOT_COMPUTABLE / missing_trace_id`；Canvas 各层容器
  类型非法时返回 `NOT_COMPUTABLE / invalid_chat_trace_structure`。
- 研究模式尚无确认的结构化子任务事件契约，返回
  `NOT_COMPUTABLE / missing_structured_subtasks`，不得从普通描述文本推测。

### 字段映射

聊天模式：

- 单次执行标识：`canvas.meta.traceId`。
- 子任务候选：扫描 `canvas.agents[].messages[].content.tool_call[]`，其中具有 `agentName`
  且没有 `toolId` 的调用视为子 Agent；即使同时存在 `toolName`，仍按子 Agent 处理。
- 一个候选事件对应一个实际子任务，不在比较前去重。
- 子任务文本：`arguments` 支持对象或可解析为对象的 JSON 字符串；优先读取
  `arguments.query`，兼容 `arguments.task`。`agentName` 仅表示
  `worker_search/worker_validation/...` 类型，不作为子任务正文。两者均为空时事件非法。
- 普通工具请求和 `tool_result` 不参与本指标；旧版 `tool_call_queue` 不再支持。

研究模式：

- 会话标识：`sessionID + userMessageID`。
- 当前 `body.task` 和 `body.description` 只作为可见步骤意图使用，尚未证明每项都对应唯一
  规划子任务，因此本版本不将其作为分母。

规范化只输出子任务总数、冗余数、精确重复数、高相似冗余数、非法事件数、算法版本和
固定阈值。子任务正文、规范化文本、约束词元和 bigram 集合均不得进入结果或持久化介质。

### 模拟计算

```text
T1 = 查询公司 2023—2025 年营业收入、同比增长率和年度变化趋势，并整理为年度表格
T2 = 查询该公司 2023—2025 年营业收入、同比增长率和年度变化趋势，并整理为年度表格
T3 = 查询公司 2023—2026 年营业收入、同比增长率和年度变化趋势，并整理为年度表格

T2 与 T1 规范化后满足高置信度冗余规则。
T3 的时间约束不同，不属于冗余。

实际子任务数 = 3
冗余子任务数 = 1
子任务冗余率 = 1 ÷ 3 ≈ 0.33333
```

新版聊天日志微平均验算：336 条 Trace 中 253 条可计算、81 条没有子任务、2 条包含非法
子任务事件。1,199 次子 Agent 调用中 1,197 次具有结构化任务文本；两条非法 Trace 中另有
3 个有效子任务，因整条 Trace 不可计算而不进入分母。最终在 1,194 个有效子任务中识别出
2 次精确重复和 1 次高相似冗余，`3 / 1194 ≈ 0.00251`。两次非法调用只有图片参数，不得把
图片内容猜测为任务文本。evaluator 版本为 `1.1.0`。

## 8. 意图识别准确率

### 指标定义

稳定指标名：`intent_recognition_accuracy`

```text
单会话意图识别得分 =
  Σ（可用分项权重 × 分项得分）÷ Σ（可用分项权重）

跨会话意图识别准确率 = 可计算会话得分的算术平均值
```

- 固定分项权重为：实质意图覆盖 `0.45`、子任务/规划对齐 `0.20`、MCP 工具合理性
  `0.15`、最终回答对齐 `0.20`。LLM 只返回固定枚举，权重映射、硬规则和汇总全部由代码完成。
- 实质意图覆盖：`complete=1`、`partial=0.5`、`missing=0`、`contradicted=0`。
  子任务/规划与最终回答：`aligned=1`、`partially_aligned=0.5`、`misaligned=0`。
  MCP：`reasonable=1`、`partially_reasonable=0.5`、`unreasonable=0`。
- `not_present` 只允许用于规划、MCP 和最终回答，表示该行为类型在日志中不存在；相应权重从
  分母剔除后重新归一化，避免无需 MCP 或允许直接回答的任务被机械扣分。实质意图覆盖始终
  参与评分。
- `primary_intent=misaligned` 或 `intent_coverage=contradicted` 时应用硬规则，单会话直接记
  `0.0`，防止其他局部正确行为掩盖主意图错误。
- 本指标是可追溯的 Judge 代理标准，用来判断用户 query 与代理实际行为证据的实质意图
  是否对齐，不等同于人工 Golden Benchmark。
- 确定性规则负责字段存在性、证据去重、调用 ID 去重与冲突、前置失败和 Judge 输出校验；
  只有分项语义标签调用 Judge。MCP 分项只判断工具选择是否合理，不检查参数正确性、调用
  成功、响应内容或手册 Schema 完整性。
- Judge 输出为固定八字段对象：`assessment`、`primary_intent`、`intent_coverage`、
  `planning_alignment`、`mcp_tool_rationality`、`final_answer_alignment`、`reason_code`、
  `confidence`。严格 JSON Schema 禁止额外字段，evaluator 再校验枚举、有限数值和字段间业务
  一致性；规划、MCP、最终回答是否存在由代码从规范化证据中确定，Judge 不得改变评分分母。
- 第一次业务校验失败时，程序只携带安全码 `invalid_intent_judgment_contract` 重试一次，不回显
  非法输出或原始日志；重试仍失败才返回 `NOT_COMPUTABLE / invalid_judge_output`。
- `assessment=not_assessable` 返回 `NOT_COMPUTABLE / insufficient_intent_evidence`，不进入
  聚合分母。Judge 调用异常返回统一运行时的稳定原因码，未知异常仍返回 `judge_failure`。
- evaluator 版本为 `1.3.0`，Judge prompt 版本为 `1.2.0`；Judge 名称、模型版本和 prompt
  版本进入安全元数据。

### 分模式可计算性与字段映射

聊天模式：`CONDITIONALLY_COMPUTABLE`。

- 必需执行标识：`canvas.meta.traceId`；缺失时返回 `missing_trace_id`。必需用户意图：
  `canvas.meta.query`；缺失时返回 `missing_query`。
- 扫描 `canvas.agents[].messages[].content.tool_call[]`。具有 `agentName` 且没有 `toolId`
  的调用视为子 Agent；即使同时存在 `toolName`，仍按子 Agent 处理。
- 子 Agent 证据优先读取 `arguments.query`，否则读取 `arguments.task`；`arguments` 支持对象
  或可解析为对象的 JSON 字符串。没有可靠任务正文时只增加异常诊断，不能使用 `agentName`
  或多模态内容猜测；只要还有其他可靠行为证据，该 Trace 仍可提交 Judge。
- 普通工具请求必须同时具有 `toolId + toolName`，按 `traceId + toolId` 去重；同 ID 同工具名
  合并并增加重复诊断，同 ID 不同工具名返回 `conflicting_call_definition`。缺少调用 ID 或
  工具名分别返回 `missing_call_id`、`missing_tool_name`。
- 代理证据还包括 MCP 能力和 `canvas.answer.answer`；本指标不读取工具参数或 `tool_result`
  正文。`canvas.meta.tools` 明确声明但手册缺失的 MCP 仍作为工具行为证据，使用安全通用描述；
  未被日志声明为 MCP 的框架工具只进入范围外诊断。
- 完全相同的证据正文仅保留第一次出现的位置和类型。
- 只有 query、没有任何子任务、MCP 能力或最终回答证据时返回 `no_intent_evidence`。
- 当没有其他代理证据且回答为固定模型服务失败文本时返回 `pre_intent_failure`。
- Canvas 各层容器类型非法时返回 `invalid_chat_trace_structure`；旧版 `tool_call_queue` 不再
  支持。

研究模式：`CONDITIONALLY_COMPUTABLE`。

- 必需用户意图：首个顶层 `query`，兼容 `body.query`；缺失时返回 `missing_query`。
- 代理证据：`body.task`、`body.description`、`turn.end` 前最后一个可确认属于 assistant 的文本，
  以及 `result.toolCalls[].toolCallId/toolName` 解析得到的 MCP 能力。
- 调用按 `toolCallId` 去重；同 ID 同工具只增加重复诊断，同 ID 不同工具返回
  `conflicting_call_definition`，调用缺少 ID 返回 `missing_call_id`。
- `agent.mcp[].mcp[].tools` 明确声明但手册缺失的 MCP 仍作为工具合理性证据；可验证的服务
  前缀短名称与完整名称同时识别，其他未知工具仍排除。
- 只有 query、没有任何规划文本、MCP 能力或最终回答时返回 `no_intent_evidence`。

两种模式的 query、子任务、规划、回答、调用参数和 Judge 原始响应都只存在于内存请求中，
不得进入评测结果或持久化介质。结果只保留模式、版本、证据与能力数量、诊断计数、固定分项
数值、有效权重、硬规则是否触发、Judge 安全 reason code 和 confidence。

新版聊天日志历史重算状态：312/336 条 Trace 可构造意图 Judge 候选，24 条为明确前置失败。
指定前 30 条聊天日志按 `1.3.0` 契约实跑：28 条可计算且均为 `1.0`，2 条为
`pre_intent_failure`；研究日志 1 条可计算且为 `1.0`。31 条均未出现业务校验重试或
`invalid_judge_output`。该结果只代表指定样本，不作为人工 Golden Benchmark。

### Judge 判定口径

- 可评估 reason code：`all_material_intents_covered`、
  `material_intent_partially_covered`、`material_intent_omitted`、
  `wrong_primary_intent`、`contradicted_user_intent`、`unrelated_agent_behavior`。
- 不可评估 reason code：`insufficient_agent_evidence`、`ambiguous_user_intent`。
- Prompt 内提供完整覆盖、部分覆盖和相反意图三个 few-shot；示例只演示固定标签，不要求
  Judge 计算权重或总分。

### 模拟计算

```text
会话 A：四项均为满分 → (0.45 + 0.20 + 0.15 + 0.20) ÷ 1.0 = 1.0
会话 B：意图部分覆盖 0.5、规划对齐 1、MCP 部分合理 0.5、回答偏离 0
       → (0.45×0.5 + 0.20×1 + 0.15×0.5 + 0.20×0) ÷ 1.0 = 0.5
会话 C：无规划、无 MCP，意图完整且回答对齐
       → (0.45×1 + 0.20×1) ÷ (0.45+0.20) = 1.0
会话 D：主意图错误，即使部分工具合理 → 硬规则覆盖汇总结果 → 0.0
会话 E：用户意图含糊 → not_assessable → 不进入聚合分母
```

## 9. 时间范围提取准确率

### 指标定义

稳定指标名：`time_range_extraction_accuracy`

```text
时间范围提取准确率 =
  正确提取的预期时间字段数 ÷ 用户需求中可评估的时间字段总数
```

- 评估字段包括开始时间、结束时间、数据频率、实际值/预测值属性、最新性要求和报告期；
  多段区间分别计数。
- 用户未指定的字段不进入分母；明确要求但工具参数遗漏的字段计为错误。
- 额外时间条件若改变查询范围，应使受影响的预期字段判错，并计入
  `unexpected_constraint_count` 安全诊断。
- 本指标使用两阶段 LLM Judge 代理标准，不等同于人工 Golden Benchmark。第一阶段只能读取
  query 与 reference time，提取用户预期字段；工具参数不得参与分母构造。程序为预期字段分配
  `time_field_0001` 等稳定 ID。第二阶段只按这些 ID 返回 `correct`、`incorrect` 或
  `not_assessable`，最终计数和准确率由程序计算。
- 第一阶段返回 `not_assessable` 时，使用 `no_temporal_requirement`、
  `missing_reference_time`、`ambiguous_temporal_requirement` 或
  `insufficient_time_evidence`，结果为 `NOT_COMPUTABLE`。
- 第二阶段必须完整返回全部冻结字段 ID；`missing_expected_field_ids`、
  `unexpected_constraint_field_ids` 和 `incompatible_temporal_role_field_ids` 仅用于安全诊断。
  JSON-object 兼容端点省略空诊断数组时按空数组处理，但不得遗漏字段 verdict 或返回未知键。
- 业务结构校验失败时安全重试一次；再次非法才返回
  `NOT_COMPUTABLE / invalid_judge_output`，重试次数记录为
  `judge_validation_retry_count`。
- Judge 调用异常返回 `NOT_COMPUTABLE` 及统一运行时的稳定原因码，未知异常仍归为
  `judge_failure`；输出非法返回
  `NOT_COMPUTABLE / invalid_judge_output`。
- evaluator 版本为 `2.0.0`，Judge prompt 版本为 `2.0.0`；Judge、模型、prompt 和 MCP
  手册版本进入安全元数据。

### 相对时间规则

- 相对时间必须绑定日志中的会话/调用时间或调用方显式提供的 `reference_time`，不得使用
  评测运行当天，保证结果可复现。
- `近 N 年` 按以锚点为终点的 N 年滚动区间；`今年以来` 从锚点所在年份 1 月 1 日开始；
  `上一完整年度` 为锚点年份之前的一个自然年。
- 用户要求相对时间但没有可靠锚点时返回 `missing_reference_time`。
- 工具省略可选日期时，只有 MCP 参数说明明确给出默认观察日语义，Judge 才能使用该默认值。
- `最近半年/近 N 年` 只形成起止字段，不因时间长度自动生成 frequency；只有用户明确要求
  日频、周频、月度、季度或年度观察频率时才生成 frequency。
- `最新` 形成 `freshness`；`未来/预测/预期` 形成 forecast `value_type`。单一明确年份由
  程序补齐该自然年的开始与结束字段，避免 Judge 漏提取。

### 时间证据角色

- `data_period`：业务数据覆盖期间，可匹配开始和结束字段。
- `reporting_period`：财报或报告期，只匹配报告期要求。
- `retrieval_window`：搜索语料回溯窗口，不能单独满足或反驳业务数据期间。
- `frequency`：观察频率；`value_type`：历史实际或预测属性；`free_text_query`：工具自由文本
  中明确写出的时间语义。
- 工具自行选择的默认日期不能创造用户预期字段；宽于用户需求的检索窗口通常也不构成额外
  业务约束。

### 分模式可计算性与字段映射

聊天模式：`CONDITIONALLY_COMPUTABLE`。

- 用户需求：`canvas.meta.query`；缺失时返回 `missing_query`。
- 实际时间证据：普通工具请求的 `arguments`，并结合 `toolName` 对应的 MCP 参数说明。
- 相对时间锚点：调用方显式值优先，否则使用 `canvas.meta.create_time`。新版请求对象没有
  独立 `create_time`，不得从数组位置推断调用时间。
- `reference_time_source` 记录为 `provided`、`canvas_meta`、`event` 或 `missing`；旧聊天
  请求级来源 `tool_request` 不再接受。
- 调用按 `traceId + toolId` 去重；参数无法完整解析时返回
  `incomplete_time_observation`。
- 没有工具调用但用户存在明确时间要求时仍调用 Judge，缺失的预期字段可计算为零分；固定
  模型服务失败则返回 `pre_time_extraction_failure`。

研究模式：`CONDITIONALLY_COMPUTABLE`。

- 用户需求：首个顶层 `query`，兼容 `body.query`；缺失时返回 `missing_query`。
- 实际时间证据：`result.toolCalls[].input`，结合 `toolName` 对应的 MCP 参数说明。
- 相对时间锚点：调用方显式值优先，否则依次读取事件 `timestamp`、`create_time` 或
  `part.state.time.start`。
- 调用按 `toolCallId` 去重；缺失 ID 返回 `missing_call_id`，同 ID 定义冲突返回
  `conflicting_call_definition`，参数无法完整解析返回 `incomplete_time_observation`。

两种模式的 query、reference time、参数值、日期原文和 Judge 原始响应只允许在内存中存在。
持久化结果仅保留字段计数、调用与异常诊断、版本标识、reference time 是否存在及其来源。

2026-09-09 使用 `qwen-flash` 对前 30 条新版聊天 Trace 复验：11 条可计算，15 条为
`no_temporal_requirement`，2 条为 `pre_time_extraction_failure`，2 条为
`insufficient_time_evidence`。可判定字段共 18 个，其中正确 9 个、错误 9 个，字段微平均
`9 / 18 = 0.5`。第 10、21、25 条不再被工具默认日期制造假分母；第 19 条稳定提取单一年度的
起止字段并得到 `2 / 2`。研究日志本次可直接计算，结果为 `0 / 2 = 0`；该模式总体仍标记为
`CONDITIONALLY_COMPUTABLE`，因为缺少 query、可靠锚点、完整调用或可识别 Schema 时必须返回
稳定的 `NOT_COMPUTABLE`，不得估算。

### 模拟计算

```text
用户要求：2020A-2025A 历史数据，2026E-2028E 预测数据，年度频率
实际参数：2020A-2024A 历史数据，2026E-2028E 预测数据，年度频率

可评估字段数 = 7
正确字段数 = 6
错误字段数 = 1
时间范围提取准确率 = 6 ÷ 7 ≈ 0.85714
```

## 10. 陈述支持率

### 指标定义

稳定指标名：`statement_support_rate`

```text
陈述支持率 =
  有充分证据支持的可验证原子陈述数 ÷ 可验证原子陈述总数
```

- 原子陈述是最终回答中能够独立判断证据支持情况的最小语义单元。事实、数值、时间、趋势、
  关系、事实性概括、明确归属的预测，以及可由证据简单推导的结论进入可验证分母。
- 标题、目录、过渡语、操作说明、纯建议、价值判断、限制说明和无事实免责声明不进入分母，
  Judge 必须标记为 `excluded`。混合句必须拆分；建议本身排除，但建议所依据的事实仍进入分母。
- 确定性规则对每个原子陈述返回 `supported`、`unsupported`、`not_assessable` 或 `excluded`。
  `supported` 必须引用至少一个本次请求中的真实证据单元；其余 verdict 不得引用证据。
- 明确没有证据调用、所有证据调用明确失败，或成功检索结果为空时，仍拆分最终回答；其中
  可验证事实应判为 `unsupported`，因此指标可以正常得到 `0`，不得因分子为零而不可计算。
- `not_assessable` 仍属于可验证陈述分母，但不伪装成 `unsupported`。同时输出诊断字段：

```text
automatic_assessment_coverage =
  (supported_claim_count + unsupported_claim_count) ÷ verifiable_claim_count
```

- 最终回答不含可验证陈述时返回 `NOT_COMPUTABLE / no_verifiable_claims`。默认实现不调用外部
  模型或网络，evaluator 版本为 `3.0.0`，规则版本为 `rules-1.0.0`。
- 确定性规则覆盖：标点/Markdown 分句、建议与免责声明排除、NFKC 规范化、字符 bigram
  相似度、指标别名、年份、金额/百分比单位归一化、否定或数值冲突，以及同比的白名单
  简单推导。复杂因果、弱相关概括和规则无法可靠确认的语义返回 `not_assessable`。

### 确定性判定契约

程序按回答顺序分配稳定 `claim_id`，并把每条陈述与全部证据单元横向比对。数值事实要求
时间、指标和归一化数值同时满足；文本事实要求强包含或高字符 bigram 重合；只有白名单公式
可以跨证据推导。任何阶段均不调用 LLM、embedding 服务或外部知识库。

```json
{
  "claims": [
    {
      "claim_id": "c0001",
      "claim_type": "verifiable",
      "verdict": "supported",
      "reason_code": "directly_supported",
      "supporting_unit_ids": ["call-1:0"],
      "confidence": 0.96
    }
  ]
}
```

- 最终输出中的 `claim_id` 必须非空且唯一；每项只能包含固定字段，不得返回陈述文本、解释
  正文或证据摘要。
- `claim_type` 只能是 `verifiable` 或 `excluded`；可验证陈述不得返回 `excluded`，排除项只能
  返回 `excluded`。
- `supporting_unit_ids` 不得重复或引用请求外 ID。`supported` 必须非空，其他 verdict 必须为空。
- `confidence` 必须是 `[0, 1]` 内有限数值；`reason_code` 必须来自按 verdict 划分的固定安全
  白名单。评测结果只持久化各类计数、版本和安全原因码。

### 分模式可计算性与字段映射

聊天模式：`CONDITIONALLY_COMPUTABLE`。

- 单次执行标识：`canvas.meta.traceId`；用户需求：`canvas.meta.query`；最终回答：
  `canvas.answer.answer`。
- 固定“模型服务暂时不可用，请稍后重试”回答返回 `pre_answer_failure`；缺少最终回答返回
  `missing_final_answer`。
- 证据复用显式 `EvidenceSourceCatalog` 和共享结构化证据单元抽取。
- 没有证据调用、明确失败或明确空结果仍可计算；调用过证据工具但缺少终态响应时返回
  `incomplete_evidence_observation`。

研究模式：`CONDITIONALLY_COMPUTABLE`。

- 会话标识：`sessionID + userMessageID`；用户需求优先读取顶层 `query`，兼容 `body.query`。
- 通过 `message.updated.info.id/role` 识别 assistant message；关联
  `message.part.updated.part.messageID/type/text`，选择 `turn.end` 前最后一个 assistant 非空文本
  作为最终回答。
- 没有最终文本但存在成功产物写入时返回 `missing_final_artifact_content`；其他无最终回答情况
  返回 `missing_final_answer`。
- 证据定义、终态响应和 `callID` 关联由共享证据抽取器处理；已定义证据调用缺少终态时返回
  `incomplete_evidence_observation`。

已登记证据工具的完整普通字符串响应，以及 `data/items/results/documents/chunks/records` 信封
中的字符串，保留为一个“响应级证据单元”。这是材料边界而非事实条数估计；长文本后续只为
传输限额切块。非文本标量或确实无法形成稳定材料边界的部分成功响应仍返回
`NOT_COMPUTABLE / incomplete_evidence_segmentation`，不得只使用可拆分子集判断支持率。
query、最终回答、原子陈述、证据正文、摘要与 Judge 原始响应不得进入持久化结果。

2026-09-09 使用临时 17 工具证据目录 `evidence-scope-fbf64624b51d244b` 对真实前 30 条新版
聊天日志及指定研究日志复跑。聊天模式 27/30 条 `computed`，2 条
`pre_answer_failure`，1 条 `missing_final_answer`；研究模式 1/1 条 `computed`。普通字符串
证据不再造成 `incomplete_evidence_segmentation`，整批无 Judge 超时、截断或网络依赖。

聊天模式可计算样本共有 3,832 条可验证陈述，其中 293 条 supported、3,294 条
unsupported、245 条 not_assessable，微平均陈述支持率约 `0.07646`，自动判定覆盖率约
`0.93606`。研究模式共有 139 条可验证陈述，其中 13 条 supported、121 条 unsupported、
5 条 not_assessable，陈述支持率约 `0.09353`，自动判定覆盖率约 `0.96403`。31 条日志本地
规则评测总耗时约 20.4 秒。由于无法建立人工 Golden 样本，这些数值是可复现的运行基线，
不是对规则语义准确率的人工验收结论。

### 模拟计算

```text
最终回答：“A 公司 2025 年收入 100 亿元，同比增长 10%，建议关注。”

C1 收入 100 亿元 -> supported
C2 同比增长 10% -> unsupported
“建议关注” -> excluded

陈述支持率 = 1 ÷ 2 = 0.5
```

## 11. MCP 工具调用膨胀率

### 指标定义

本指标只统计 `mcp_tool_manual.json` 已登记的 MCP。对同一日志任务中的每次唯一调用，使用
规范化 MCP 能力 ID 与规范化入参生成请求签名；同一签名第一次出现不算膨胀，第二次及之后
均算膨胀。参数或 MCP 不同则不算膨胀，响应内容不参与判断。

```text
N = 当前任务中唯一的已登记 MCP 调用总数
U = 当前任务中不同“规范化 MCP 能力 ID + 规范化入参”签名数

膨胀调用数 = N - U
MCP 工具调用膨胀率 = (N - U) ÷ N
```

- JSON 对象按键排序并递归规范化，字段顺序不影响签名；数组顺序保留。
- 字符串形式的 JSON 对象或数组先解析再规范化，普通字符串按原值比较。
- 同一调用 ID 的重复定义或生命周期事件只计一次；同一 ID 出现不同能力或入参时返回
  `NOT_COMPUTABLE / conflicting_call_definition`。
- 手册外工具不进入分子和分母，只记录 `out_of_scope_tool_call_count`。
- 本指标与完全重复调用率不同：完全重复调用率还要求响应相同，且不限定 MCP 范围；本指标
  只比较手册内 MCP 与入参，不读取响应正文。

### 分模式可计算性与字段映射

聊天模式：`DIRECTLY_COMPUTABLE`。

- 任务边界：JSON 数组中的每个元素；单次执行标识：`canvas.meta.traceId`。
- 调用事件：遍历 `canvas.agents[].messages[].content.tool_call[]`，只接收具有
  `toolId + toolName` 的普通工具请求；只有 `agentName`、没有 `toolId` 的子 Agent 调用不进入
  分子或分母。
- 调用 ID、工具名和入参：请求的 `toolId/toolName/arguments`。
- 同一 `toolId` 跨 Agent 或消息重复出现时只观察一次；相同定义累计安全诊断，不重复计数。
- 缺少调用 ID、工具名或入参分别返回 `missing_call_id`、`missing_tool_name`、
  `missing_call_arguments`；缺少 canvas、meta、agents 或消息容器时返回
  `invalid_chat_trace_structure`。旧版顶层 `tool_call_queue` 不再支持。

研究模式：`DIRECTLY_COMPUTABLE`。

- 任务边界及标识：`sessionID + userMessageID`。
- 调用定义：`result.toolCalls[].toolCallId/toolName/input`。
- 生命周期补充：`message.part.updated.part.callID/tool/state.input`；按 `callID` 合并
  `pending/running/completed`，不得把状态事件条数当成调用次数。
- 调用缺少 `callID/toolCallId` 或工具名时分别返回 `missing_call_id`、`missing_tool_name`。
- 已观察到手册内 MCP 生命周期但没有任何可用入参定义时返回 `missing_call_arguments`。

两种模式均使用版本化 `CapabilityCatalog` 解析手册直接名称和 `mcp__...__工具名`。入参无法
稳定序列化时返回 `unserializable_call_arguments`；没有手册内 MCP 时返回
`NOT_COMPUTABLE / no_registered_mcp_calls`。结果只保留 `manual_version`、调用计数、签名计数、
膨胀计数和安全诊断，不保留工具名、入参、摘要或响应正文。新版 canvas 适配的 evaluator
版本为 `1.1.0`。

### 模拟计算

```text
quote({"symbol": "AAA", "window": 1})
quote({"window": 1, "symbol": "AAA"})
quote({"symbol": "BBB", "window": 1})
news({"symbol": "AAA"})

MCP 调用总数 N = 4
不同请求签名数 U = 3
膨胀调用数 = 1
MCP 工具调用膨胀率 = 1 ÷ 4 = 0.25
```

新版聊天日志微平均验算：4,266 次普通工具调用中 2,911 次精确匹配当前 MCP 手册，按 Trace
统计得到 2,878 个不同请求签名和 33 次膨胀调用，`33 / 2911 ≈ 0.01134`；1,355 次手册外
调用不进入分子和分母。271/336 条 Trace 至少包含一次已登记 MCP 调用。

## 12. 超时率

### 指标定义

本指标衡量唯一工具调用中明确超时、超过工具 SLA，或缺少完整时间记录的调用比例。SLA 必须
来自版本化 `ToolTimeoutPolicyCatalog`，不得为全部工具临时套用统一阈值。

```text
单次调用超时 =
  终态属于策略配置的 timeout / timed_out
  或完整调用耗时 > 对应工具 timeout_ms
  或对应工具已有 SLA、但缺少完整调用耗时观测

超时率 = 超时唯一调用数 ÷ 唯一调用总数
```

耗时等于 `timeout_ms` 时不算超时。`failed`、`error`、`cancelled` 和业务错误码本身不等于
超时；只有显式超时状态、SLA 超限或时间观测缺失进入分子。结果分别保留
`explicit_timeout_count`、`sla_exceeded_count` 和 `missing_time_timeout_count`，避免把真实超限
与日志质量问题混为一类。

### 分模式可计算性与字段映射

聊天模式：`CONDITIONALLY_COMPUTABLE`。

- 唯一调用：遍历 `canvas.agents[].messages[].content.tool_call[]`，具有
  `toolId + toolName` 的普通工具请求按 `traceId + toolId` 标识；子 Agent 调用不进入分母。
- 工具名称：请求的 `toolName`；匹配结果提供非空 `toolName` 时必须一致，否则返回
  `conflicting_call_definition`。
- 完整调用耗时：同 `toolId` 的 `content.tool_result[].durationSeconds × 1000`。该字段是单次
  工具执行耗时，不与 `message.content.durationSeconds` 相加或相减。
- 显式超时：匹配结果顶层或结构化 `result/rawResult` 中的 `status/state` 命中策略状态集合。
- 同一 `toolId` 的重复请求或结果只观察一次并记录重复计数；请求和结果可位于不同 Agent 或
  message，结果侧孤立 Agent 返回不反向生成调用。
- 缺少匹配结果或结果缺少合法 `durationSeconds` 时，在该工具已有 SLA 的前提下按
  `missing_time_timeout` 分类。旧版顶层 `tool_call_queue` 不再支持。

研究模式：`CONDITIONALLY_COMPUTABLE`。

- 唯一调用：按 `message.part.updated.part.callID` 合并生命周期事件。
- 工具名称：`part.tool`，支持目录别名和 `mcp__...__工具名` 后缀解析。
- 开始时间：同一 `callID` 的最早 `part.state.time.start`。
- 结束时间：同一 `callID` 终态事件的最晚 `part.state.time.end`。
- 显式超时：`part.state.status` 命中策略状态集合。

两种模式都必须能从版本化目录解析每个工具的 `timeout_ms`。当前仓库没有生产 SLA 目录，
因此真实日志在配置落地前返回 `NOT_COMPUTABLE / missing_tool_sla`；不能因未观察到显式
timeout 就输出 0%。无调用、缺少调用 ID、非法聊天结构或未知 SLA 分别返回
`no_tool_calls`、`missing_call_id`、`invalid_chat_trace_structure`、`missing_tool_sla`。非法时间、
负时序和调用定义冲突分别返回 `ERROR / invalid_timestamp`、`ERROR / invalid_event_order`、
`ERROR / conflicting_call_definition`。

新版 canvas 适配的 evaluator 版本为 `1.1.0`。

新版聊天日志重算状态：4,266 次普通请求中 3,443 次具有可用工具执行耗时，823 次缺少完整
耗时观测。当前仓库没有生产 SLA 目录，因此总体仍为
`NOT_COMPUTABLE / missing_tool_sla`，不得把 823 次直接判为超时，也不得输出 0%。

### 模拟计算

```text
工具 A：耗时 4000 ms，SLA 5000 ms       -> 未超时
工具 B：耗时 3000 ms，SLA 2000 ms       -> SLA 超时
工具 A：终态 timed_out                  -> 显式超时
工具 A：缺少完整调用耗时，SLA 已配置     -> 观测缺失超时

超时调用数 = 3
唯一调用总数 = 4
超时率 = 3 ÷ 4 = 0.75
```

## 13. 端到端总耗时

### 指标定义

本指标衡量单条 Trace 从用户请求开始到最终回答完成之间经过的墙钟总耗时。展示名称为
“端到端总耗时”，稳定指标名继续使用 `end_to_end_latency_ms`，单位为毫秒，避免与现有实现和
历史结果产生重复指标。

```text
end_to_end_latency_ms =
  最终回答完成时间 - 用户请求开始时间
```

该时间范围自然包含模型推理、工具执行、Agent 调度、子任务等待、排队和框架开销，是用户实际
等待时间的主指标。它不是各模型步骤耗时与各工具耗时的算术和：步骤和工具可能并行、嵌套，
某个 Agent 消息耗时也可能覆盖其内部工具调用时间。

### 统计边界与去重

- 统计单位是单条 Trace 或单个研究回合，不跨 Trace 累加后作为单条结果。
- 开始边界必须对应当前用户请求，结束边界必须对应该请求的最终回答完成。
- orchestrator、worker、工具和框架内部事件只位于开始与结束边界之间，不单独加到主值中。
- 中间 Agent 文本、工具终态和子 Agent 返回都不能作为最终结束边界。
- 同一边界的重复协议快照去重；同一 Trace 出现不同开始或结束边界时返回错误，不选择最早或
  最晚值猜测。
- Judge、离线评测器、报告生成和落盘耗时不属于被评测 Trace，不进入本指标。

### 分模式可计算性与字段映射

聊天模式：`DIRECTLY_COMPUTABLE`。

- 开始时间：`canvas.meta.create_time`。
- 结束时间：`canvas.answer.create_time`。
- 两个字段为秒级时间戳，规范化后计算毫秒差值并保留 `timestamp_precision=seconds`；结果为
  `0 ms` 时只表示边界落在同一秒。
- `canvas.answer.durationSeconds` 是终止 Agent 消息耗时的复制，不是完整 Trace 时延，禁止用作
  本指标。
- `canvas.agents[].messages[].content.durationSeconds` 和
  `tool_result.durationSeconds` 只用于步骤或工具诊断，不参与端到端总耗时求和。
- 旧版顶层聊天结构不支持，返回 `ERROR / invalid_chat_trace_structure`。

研究模式：`DIRECTLY_COMPUTABLE`。

- 回合边界：同一 `sessionID + userMessageID` 的 `turn.start` 与 `turn.end`。
- 指标值优先使用 `turn.end.durationMs`；同时计算 `turn.end.ts - turn.start.ts`，将
  `时间戳差值 - durationMs` 记录为 `boundary_difference_ms`。
- `durationMs` 缺失时回退到时间戳差值；相同重复边界去重并记录计数，不同重复边界返回
  `ERROR / conflicting_turn_boundary`。
- `message.updated.info.time`、模型 step 边界和工具生命周期只能用于分阶段诊断，不替代
  `turn.start/turn.end` 回合边界。

两种模式缺少开始或结束边界时分别返回 `NOT_COMPUTABLE / missing_start_timestamp` 或
`missing_end_timestamp`。非法时间戳、非法报告耗时和负时序分别返回
`ERROR / invalid_timestamp`、`ERROR / invalid_duration` 和 `ERROR / invalid_event_order`。
规范化结果只保留标识、时间边界、报告耗时、精度和安全诊断，不保留 query、回答或工具正文。
evaluator 版本为 `1.0.0`。

### 输出字段

```text
metric_name = end_to_end_latency_ms
value = 端到端总耗时，单位毫秒
status = computed | not_computable | error
latency_source = timestamp_delta | turn_end_duration
timestamp_precision = seconds | milliseconds
boundary_difference_ms
duplicate_boundary_event_count
evaluator_version
```

- 聊天模式 `latency_source = timestamp_delta`，`boundary_difference_ms = null`。
- 研究模式优先使用 `latency_source = turn_end_duration`；只有 `durationMs` 缺失时才回退到
  `timestamp_delta`。
- 非 `COMPUTED` 状态不得输出部分 `value`。
- `boundary_difference_ms` 仅用于检查研究模式上游报告耗时与时间戳边界的一致性，不修改主值。

### 观测价值

- 直接反映用户感知的整体响应速度，是所有工具调用效率指标的 Trace 级基准。
- 与 `tool_calls_count` 联合观察，可以区分“调用次数膨胀”与“少量调用但整体仍慢”。
- 与 `per_tool_call_latency` 联合观察，可以定位单个慢工具是否构成主要瓶颈。
- 与 `per_tool_repeat_call_count` 联合观察，可以判断同名工具反复调用是否伴随总耗时上升。
- 与 `total_token_consumption` 联合观察，可以区分高 Token 的模型循环问题与低 Token、长等待的
  外部工具问题。
- 工具可能并行、嵌套或被模型步骤包含，因此不得把各工具耗时简单求和后当作
  `end_to_end_latency_ms`，两者只用于相关性和瓶颈分析。

### 模拟计算

```text
聊天：10:00:00 开始，10:01:25 回答完成
端到端时延 = 85,000 ms，来源 timestamp_delta

研究：时间戳差值 936,403 ms，turn.end.durationMs = 936,405 ms
端到端时延 = 936,405 ms，来源 turn_end_duration
boundary_difference_ms = -2 ms
```

## 14. 平均模型步骤时延

### 指标定义

本指标衡量单条 Trace 内有效模型步骤的平均观测时延，稳定指标名为
`average_model_step_latency_ms`，单位为毫秒。

```text
单步骤时延 = 步骤结束时间 - 步骤开始时间

单条 Trace 平均模型步骤时延 =
  有效步骤时延总和 ÷ 有效步骤数

全量微平均 =
  全部 Trace 的 total_step_duration_ms 总和
  ÷ 全部 Trace 的 valid_step_count 总和
```

步骤总和仅作为微平均聚合输入，不是独立的墙钟时延指标；并行或嵌套步骤不得相加后解释为
Trace 总耗时。

### 分模式可计算性与字段映射

聊天模式：`DIRECTLY_COMPUTABLE`。

- 每个 `canvas.agents[].messages[]` 是一个步骤观测，读取
  `message.content.durationSeconds × 1000`。
- orchestrator 和 worker 消息都作为独立步骤进入分母，但不推断父子包含关系。
- `tool_result.durationSeconds` 是独立工具执行耗时，不得从消息耗时中相减。
- `canvas.answer.durationSeconds` 是最后一个 Agent message 耗时的复制，不重复进入步骤集合。
- 缺失 `durationSeconds` 的步骤进入 `latency_missing_count`，不补零；非法值和负值分别返回
  `ERROR / invalid_duration`、`ERROR / invalid_event_order`。

研究模式：`DIRECTLY_COMPUTABLE`。

- 使用 `message.part.updated` 中的 `step-start` 和 `step-finish`，按 `part.messageID` 配对；两种
  part 的 `part.id` 不同，不能按 part ID 配对。
- 主边界使用事件数值型 `time`，步骤时延为 `step-finish.time - step-start.time`。
- 两侧都存在合法 `ts` 时计算时间戳差值，并记录
  `timestamp_cross_check_count` 与 `max_abs_boundary_difference_ms`；`ts` 缺失不影响主指标。
- `message.updated.info.time.created/completed` 覆盖更宽的消息生命周期，不作为本指标边界。
- 同一 loop 的两个 `llm.response` 是重复协议投影，不进入步骤计数。
- 相同重复边界去重并记录计数；同一 `messageID` 的不同重复边界返回
  `ERROR / conflicting_step_boundary`。缺少 `messageID` 返回 `ERROR / missing_step_id`。

两种模式都只使用完整有效步骤。部分缺失时仍对有效步骤计算，并显式记录
`latency_missing_count`；无任何有效步骤时返回
`NOT_COMPUTABLE / no_observable_model_steps`。规范化和评估结果不保留消息、推理、工具参数或
回答正文。evaluator 版本为 `1.0.0`。

### 模拟计算

```text
步骤 A：1250 ms
步骤 B：缺失，不补零
步骤 C：2000 ms

valid_step_count = 2
latency_missing_count = 1
total_step_duration_ms = 3250
average_model_step_latency_ms = 3250 ÷ 2 = 1625 ms
```

## 15. 端到端总 Token 消耗

### 指标定义

本指标衡量单条 Trace 从请求开始到最终回答结束期间，全部已确认 Agent 模型步骤的 Token
消耗总和。展示名称为“端到端总 Token 消耗”，稳定指标名继续使用
`total_token_consumption`，单位为 token，避免与现有实现和历史结果产生重复指标。

```text
聊天模式 Token 消耗 =
  Σ 唯一模型步骤的 (input_tokens + output_tokens)

研究模式 Token 消耗 =
  Σ 唯一 step-finish 的 tokens.total
```

“端到端”表示统计范围覆盖当前 Trace 内 orchestrator、worker 和其他可识别 Agent 的全部模型
步骤，不表示把工具耗时、墙钟时间或工具响应正文换算为 Token。本指标不是费用指标，不应用
缓存折扣、模型单价或供应商价格系数。

当前主指标只汇总 Agent 模型步骤 Token，不包含工具内部再次调用模型产生的 Token。原因是两种
现有日志都没有按工具调用 ID 提供工具自身 `usageTokens.total`。不得把发起工具调用的模型步骤
Token 重复记为工具 Token。未来上游补齐逐工具 Token 后，如需将其纳入端到端总量，必须提升
`evaluator_version` 并新增明确的 `tool_token_consumption` 分项，禁止静默改变历史口径。

### 统计边界与去重

- 统计单位是单条 Trace，不跨 Trace 累加后再作为单条结果。
- 每个模型步骤只计一次；同一 Token 快照在 answer、message update 或协议投影中重复出现时
  必须去重。
- 聊天模式包含 orchestrator 和 worker 的模型步骤；仅含工具结果、Agent 返回或框架控制信息的
  消息不进入模型步骤集合。
- 研究模式以 `messageID` 标识唯一模型步骤；同一 loop 的多个协议投影不重复计数。
- cache Token 不得在已经包含它的 input 或 total 之外再次累加。cache 构成由
  `cache_token_ratio` 单独解释。
- Judge、离线评测器或报告生成阶段自身消耗的 Token 不属于被评测 Trace，不进入本指标。

### 分模式可计算性与字段映射

聊天模式：`DIRECTLY_COMPUTABLE`。

- 模型步骤来源：`canvas.agents[].messages[]`。具有 `durationSeconds` 或 `usageTokens` 的消息
  视为模型步骤；仅含 `tool_result` 的消息是工具结果投影，不进入 Token 覆盖分母。
- 单步骤值：`content.usageTokens.input_tokens + output_tokens`。
- `cache_tokens` 不额外加入总量；当前主值只使用 `input_tokens + output_tokens`，避免缓存 Token
  被重复计算。
- `canvas.answer.usageTokens` 是某条 Agent message 的重复快照，不进入计算。
- orchestrator 和 worker 均代表独立模型步骤，其 Token 可以相加。
- 同一条 Agent message 既包含 `usageTokens` 又包含一个或多个 `tool_call` 时，Token 仍只属于该
  模型步骤，不按工具数量复制或分摊。

研究模式：`DIRECTLY_COMPUTABLE`。

- 权威来源：`message.part.updated.part.type == step-finish` 的 `part.tokens.total`。
- 使用 `part.messageID` 标识唯一模型步骤；相同重复快照去重并记录
  `duplicate_token_snapshot_count`，不同快照返回 `ERROR / conflicting_token_snapshot`。
- `message.updated.info.tokens` 与 step-finish 是重复快照，不参与计算。
- 每个 loop 的两个 `llm.response.result.usage` 是重复协议投影，不参与计算。
- `tokens.input/output/reasoning/cache` 不单独解析，直接使用上游 `total`。
- 当分项完整时，可校验
  `total = input + output + reasoning + cache.read + cache.write`；校验值只作为诊断，不重复加入
  主指标。

Token 消耗必须覆盖全部已识别模型步骤。全部步骤缺少 Token 时返回
`NOT_COMPUTABLE / no_observable_token_usage`；部分步骤缺失时返回
`NOT_COMPUTABLE / incomplete_token_coverage`，不得输出部分总量。非负整数校验失败、缺少研究
步骤 ID 分别返回 `ERROR / invalid_token_count`、`ERROR / missing_step_id`。规范化结果只保留
逐步骤总数、覆盖计数和重复计数，不保留消息、推理或工具正文。evaluator 版本为 `1.0.0`。

### 输出字段

```text
metric_name = total_token_consumption
value = 端到端总 Token 消耗
status = computed | not_computable | error
model_step_count
token_observed_count
token_missing_count
duplicate_token_snapshot_count
evaluator_version
```

`COMPUTED` 状态必须满足 `token_observed_count == model_step_count` 且
`token_missing_count == 0`。非 `COMPUTED` 状态不得输出部分 `value`，避免不完整总量进入跨 Trace
聚合。

### 观测价值

- 直接衡量单条 Agent 工作流的整体模型资源消耗，是 Token 优化的主指标。
- 与 `tool_calls_count`、`per_tool_repeat_call_count` 联合观察，可以判断总 Token 增长是否来自工具
  调用轮次和重复调用膨胀。
- 与 `output_token_ratio` 联合观察，可以区分 Token 主要消耗在上下文输入还是模型输出。
- 与 `cache_token_ratio` 联合观察，可以识别大量 Token 是否来自重复读取缓存上下文。
- 与 `end_to_end_latency_ms` 联合观察，可以区分“高 Token 且慢”“高 Token 但不慢”和
  “Token 不高但外部工具很慢”等不同瓶颈。
- 该指标不能直接替代费用。跨模型、跨供应商或跨缓存策略比较时，必须同时保留模型版本、
  Token 口径版本和缓存语义。

### 模拟计算

```text
聊天步骤 A：input=100，output=20，cache 字段忽略 -> 120
聊天步骤 B：input=40，output=10                  -> 50
单条 Trace Token 消耗 = 120 + 50 = 170

研究步骤 A：tokens.total=100
研究步骤 B：tokens.total=250
单条 Trace Token 消耗 = 100 + 250 = 350
```

## 补充字段：工具调用效率观测

本节只定义工具调用次数、同名工具重复调用、工具耗时、工具关联模型步骤 Token，以及用于解释
工具调用成本的 Trace 级 Token 比例和端到端时延。工具关联模型步骤 Token 表示“产生该工具
调用的模型步骤消耗”，不表示工具服务内部执行消耗。所有字段均由日志和版本化目录确定，不使用
人工主观判断。

### 统一工具范围与同名聚合

进入本节统计范围的调用必须属于 `business_tool`。以下事件不得进入工具调用次数、平均工具
耗时或平均工具 Token：

- `agent_delegation`：父 Agent 创建子 Agent 的委派事件。
- `agent_return`：子 Agent 向父 Agent 返回结果的控制事件，例如聊天日志中的 `respond`。
- `framework_control`：框架内部调度、收尾或协议投影。

工具名称先通过版本化 `tool_name_alias_catalog_version` 归一到稳定名称，再按稳定名称聚合。
工具类别优先读取上游 `call_category`；当前日志缺少该字段时，允许使用版本化
`tool_category_catalog_version` 做条件映射。目录缺失时返回
`NOT_COMPUTABLE / missing_tool_category_catalog`，不得把所有带 `toolId/callID` 的事件默认视为
业务工具。

每个 Trace 输出以下同名工具统计结构：

```text
per_tool_statistics[tool_name] = {
  call_count,
  repeat_call_count,
  tool_step_count,
  single_tool_step_count,
  multi_tool_step_count,
  token_observed_count,
  token_missing_count,
  tool_associated_token_total,
  per_tool_call_tokens,
  latency_observed_count,
  latency_missing_count,
  tool_latency_total_ms,
  per_tool_call_latency
}

repeat_call_count = max(call_count - 1, 0)
```

调用按聊天模式的 `traceId + toolId` 或研究模式的 `sessionID + userMessageID + callID` 唯一化。
相同生命周期快照去重；同一调用 ID 对应不同规范化工具名时返回
`ERROR / conflicting_call_definition`。

### tool_calls_count

#### 指标定义

`tool_calls_count` 表示单条 Trace 内唯一业务工具调用总数，不包含 Agent 委派、Agent 返回和
框架控制事件。

```text
tool_calls_count = Σ per_tool_statistics[tool_name].call_count

同名工具重复调用总数 =
  Σ per_tool_statistics[tool_name].repeat_call_count
```

没有业务工具调用时本指标计算为 `0`，不是不可计算。

#### 字段映射

聊天模式：`CONDITIONALLY_COMPUTABLE`。

- 调用定义：`canvas.agents[].messages[].content.tool_call[]`。
- 调用 ID：`toolId`；工具名称：`toolName`。
- `agentName` 子 Agent 委派，以及被分类为 `agent_return/framework_control` 的调用不进入统计。
- 当前日志中的 `respond` 带 `taskId`，对应结果仅包含 `toolId`，应由工具类别目录归为
  `agent_return`，不能作为缺耗时的业务工具。

研究模式：`CONDITIONALLY_COMPUTABLE`。

- 调用事件：`message.part.updated.part.type == tool`。
- 调用 ID：`part.callID`；工具名称：`part.tool`。
- 同一 `callID` 的 pending、running 和终态事件合并为一次调用。
- 仍需工具类别目录排除框架工具和控制事件。

缺少调用 ID 返回 `ERROR / missing_call_id`；缺少工具名称返回 `ERROR / missing_tool_name`。

#### 观测价值

- 直接观察工作流扇出规模；调用数异常升高通常意味着搜索扩散、规划反复或循环未及时终止。
- `repeat_call_count` 定位同名工具在单条 Trace 内被反复调用的问题。
- 与 `end_to_end_latency_ms` 联合观察，可区分“调用数量多导致慢”和“调用数量少但单次调用慢”。
- 与 Trace 总 Token 联合观察，可判断 Token 增长是否伴随工具调用轮次膨胀。

### per_tool_repeat_call_count

#### 指标定义

`per_tool_repeat_call_count` 表示单条 Trace 内每一种业务工具在首次调用之后发生的额外调用次数。
只有同名工具调用总数大于 `1` 时才进入结果；调用一次不算重复。

```text
call_count[tool_name] =
  当前 Trace 内该规范化工具名的唯一调用数

repeat_call_count[tool_name] =
  max(call_count[tool_name] - 1, 0)

total_repeat_call_count =
  Σ repeat_call_count[tool_name]
```

例如，同一 Trace 内工具 A 调用 4 次、工具 B 调用 2 次、工具 C 调用 1 次，则 A 重复 3 次、
B 重复 1 次、C 不进入重复工具列表，`total_repeat_call_count = 4`。

当多个工具发生重复调用时，使用 JSON 数组输出。数组按 `repeat_call_count` 降序、
`call_count` 降序、`tool_name` 升序排列，保证相同输入产生稳定结果：

```json
{
  "per_tool_repeat_call_count": [
    {
      "tool_name": "tool_a",
      "call_count": 4,
      "repeat_call_count": 3
    },
    {
      "tool_name": "tool_b",
      "call_count": 2,
      "repeat_call_count": 1
    }
  ],
  "repeated_tool_name_count": 2,
  "total_repeat_call_count": 4
}
```

没有重复工具时返回 `COMPUTED`，数组为空，两个汇总计数均为 `0`；不得返回不可计算或省略
字段。

跨 Trace 聚合时必须先在每条 Trace 内计算重复次数，再按工具名求和：

```text
report_repeat_call_count[tool_name] =
  Σ_trace max(call_count[trace, tool_name] - 1, 0)
```

禁止把全量数据集中某工具的总调用数直接减 `1`，否则会把不同 Trace 各自一次的正常调用误判为
重复调用。报告级结果可额外输出 `affected_trace_count`，表示发生该工具重复调用的 Trace 数。

#### 字段映射

聊天模式：`CONDITIONALLY_COMPUTABLE`。

- 复用 `tool_calls_count` 的 `canvas.agents[].messages[].content.tool_call[]`、`toolId`、
  `toolName` 和工具类别目录。
- 按 `traceId + toolId` 唯一化后，以规范化 `tool_name` 分组计数。
- `agent_delegation`、`agent_return` 和 `framework_control` 不进入分组；聊天日志中的 `respond`
  必须归类为 `agent_return`。

研究模式：`CONDITIONALLY_COMPUTABLE`。

- 复用 `message.part.updated.part.type == tool`、`part.callID`、`part.tool` 和工具类别目录。
- 同一 `callID` 的多个状态事件先合并为一次调用，再按规范化 `tool_name` 分组计数。

工具类别目录缺失时返回 `NOT_COMPUTABLE / missing_tool_category_catalog`。缺少调用 ID、缺少工具
名称或同一调用 ID 的工具定义冲突时，分别返回 `ERROR / missing_call_id`、
`ERROR / missing_tool_name`、`ERROR / conflicting_call_definition`。

#### 观测价值

- 直接定位单条 Trace 内被反复调用的具体工具及额外调用次数。
- 区分“工具种类多”与“少数同名工具反复调用”；两者的优化方式不同。
- 与 `per_tool_call_latency` 联合观察，可以估算重复调用带来的额外时延压力。
- 与 `per_tool_call_tokens` 联合观察，可以定位重复次数和单次 Token 同时较高的重点工具。
- 报告级 `affected_trace_count` 可判断问题是集中在少量异常 Trace，还是普遍存在于多数请求。

### per_tool_call_latency

#### 指标定义

`per_tool_call_latency` 表示同一 Trace 内，同名业务工具所有可观测调用的平均执行耗时，单位为
毫秒。

```text
单次工具耗时 = 工具终态时间 - 工具开始时间

per_tool_call_latency[tool_name] =
  Σ 同名工具有效调用耗时
  ÷ latency_observed_count
```

部分调用缺少耗时时，仍可对有效调用计算平均值，但必须同时输出
`latency_observed_count` 和 `latency_missing_count`。同名工具没有任何有效耗时时返回
`NOT_COMPUTABLE / no_observable_tool_duration`，不得补零。

#### 字段映射

聊天模式：`DIRECTLY_COMPUTABLE`。

- 按 `traceId + toolId` 关联 `tool_call` 与 `tool_result`。
- 单次耗时：`tool_result.durationSeconds × 1000`。
- 相同耗时快照去重；同一调用出现不同耗时返回
  `ERROR / conflicting_duration_observation`。

研究模式：`DIRECTLY_COMPUTABLE`。

- 按 `callID` 合并工具生命周期。
- 开始时间：所有状态事件中最早的 `part.state.time.start`。
- 结束时间：终态事件中最晚的 `part.state.time.end`。
- 单次耗时为结束时间减开始时间；重复状态只记录诊断。

两种模式缺少完整边界时，该调用标记为 `NOT_COMPUTABLE / missing_tool_duration`；非法时间和
负时序分别返回 `ERROR / invalid_duration`、`ERROR / invalid_event_order`。

#### 观测价值

- 按工具名比较平均耗时，定位长期偏慢的工具类型。
- 结合 `repeat_call_count` 判断某工具的总影响来自“单次慢”还是“调用次数多”。
- 结合 `end_to_end_latency_ms` 识别单工具长尾，但不得把并行或嵌套调用的耗时简单相加为
  Trace 总耗时。

### max_tool_latency_share

#### 指标定义

`max_tool_latency_share` 表示单条 Trace 内，累计执行耗时最高的同名业务工具占全部业务工具
累计执行耗时的比例。它衡量工具耗时是否集中在某一种工具上，不表示该工具占端到端墙钟耗时
的比例。

```text
tool_latency_total_ms[tool_name] =
  Σ 同名工具全部唯一调用的 duration_ms

total_tool_latency_ms =
  Σ 全部业务工具唯一调用的 duration_ms

max_tool_latency_share =
  max(tool_latency_total_ms[tool_name])
  ÷ total_tool_latency_ms
```

结果范围为 `(0, 1]`。指标同时输出 `max_latency_tool_names`、
`max_tool_latency_total_ms` 和 `total_tool_latency_ms`。多个工具的累计耗时并列最高时，
`max_latency_tool_names` 使用按规范化工具名升序排列的 JSON 数组，不得任意选择其中一个。

本指标要求当前 Trace 的全部业务工具调用均具有有效耗时。存在任一调用缺少耗时时返回
`NOT_COMPUTABLE / incomplete_tool_duration_coverage`；无业务工具调用时返回
`NOT_COMPUTABLE / no_tool_calls`；全部工具累计耗时为零时返回
`NOT_COMPUTABLE / zero_tool_duration`。非法耗时、负时序和同一调用的耗时观察冲突继续沿用
`per_tool_call_latency` 的错误原因，不得使用部分调用估算占比。

#### 字段映射

聊天模式：`DIRECTLY_COMPUTABLE`。

- 复用 `per_tool_call_latency` 的 `traceId + toolId` 调用关联和业务工具分类。
- 单次调用耗时读取 `tool_result.durationSeconds × 1000`。
- 按规范化 `tool_name` 汇总 `tool_latency_total_ms`，再从所有业务工具累计耗时计算最大占比。
- `tool_result.durationSeconds` 缺失的调用进入耗时缺失计数，并使本 Trace 的该指标不可计算。

研究模式：`DIRECTLY_COMPUTABLE`。

- 复用 `per_tool_call_latency` 的 `callID` 生命周期合并和业务工具分类。
- 单次调用耗时为最晚终态 `part.state.time.end` 减最早
  `part.state.time.start`。
- 同一 `callID` 的重复状态先去重，再按规范化 `tool_name` 汇总累计耗时。
- 任一业务工具调用缺少完整开始或结束时间时，本 Trace 的该指标不可计算。

工具可能并行或嵌套，因此 `total_tool_latency_ms` 是逐调用耗时之和，可能大于
`end_to_end_latency_ms`。本指标的分子和分母都使用同一累计工具耗时口径，所以不得将
`max_tool_latency_share` 解读为端到端时间占比，也不得为了消除重叠而擅自截断调用耗时。

#### 观测价值

- 比例高表示工具执行成本集中在少数工具上，可优先排查头部工具的单次性能和调用策略。
- 与 `per_tool_call_latency` 联合观察，可以区分“单次调用慢”和“单次不慢但累计调用次数多”。
- 与 `repeat_call_count` 联合观察，可以识别最大耗时工具是否由重复调用累计形成。
- 与 `end_to_end_latency_ms` 联合观察，可以判断工具累计耗时集中是否同时伴随用户等待时间增长；
  但两者不能直接相减或相除解释并行贡献。
- 报告级可按模式分别统计该工具成为最大耗时工具的 Trace 数及比例，定位稳定的系统瓶颈。

#### 模拟计算

```text
search：3 次调用，耗时分别为 1000、1200、800 ms
calculator：1 次调用，耗时为 500 ms

tool_latency_total_ms[search] = 3000 ms
tool_latency_total_ms[calculator] = 500 ms
total_tool_latency_ms = 3500 ms
max_latency_tool_names = ["search"]
max_tool_latency_total_ms = 3000 ms
max_tool_latency_share = 3000 ÷ 3500 ≈ 0.85714
```

### per_tool_call_tokens

#### 指标定义

`per_tool_call_tokens` 表示同一 Trace 内，产生某一规范化业务工具调用的模型步骤平均 Token
消耗。字段名为兼容既定输出保留，但统计对象是“工具关联模型步骤”，不是工具服务内部执行。

采用完整步骤关联规则：一个模型步骤调用多个不同工具时，该步骤完整 Token 分别关联给每个
不同工具名，不进行平均拆分；同一步骤内同一规范化工具名出现多次时只关联一次，避免同一工具
重复累计同一份步骤 Token。

```text
tool_step_count[tool_name] =
  包含该规范化工具名的唯一模型步骤数

tool_associated_token_total[tool_name] =
  Σ 包含该工具名的唯一模型步骤 Token

per_tool_call_tokens[tool_name] =
  tool_associated_token_total[tool_name]
  ÷ tool_step_count[tool_name]
```

`call_count` 仍表示唯一工具调用次数，`tool_step_count` 表示包含该工具的模型步骤数；一个步骤
并行或批量发出多个同名调用时，两者可能不同，不得互相替代。`token_observed_count` 和
`token_missing_count` 按关联模型步骤计数。

```text
single_tool_step_count[tool_name] =
  只包含一个不同业务工具名、且该工具名为 tool_name 的模型步骤数

multi_tool_step_count[tool_name] =
  包含多个不同业务工具名、且其中包含 tool_name 的模型步骤数

tool_step_count[tool_name] =
  single_tool_step_count[tool_name] + multi_tool_step_count[tool_name]

shared_token_step_count =
  当前 Trace 内包含多个不同业务工具名的唯一模型步骤数
```

同名工具关联的任一模型步骤缺少 Token 时，该工具返回
`NOT_COMPUTABLE / incomplete_token_coverage`，不得输出部分平均值。没有业务工具调用时返回
`NOT_COMPUTABLE / no_tool_calls`；存在业务工具调用但没有可关联模型步骤时返回
`NOT_COMPUTABLE / missing_tool_step_association`。

#### 字段映射

聊天模式：`DIRECTLY_COMPUTABLE`。

- 模型步骤按 `canvas.agents[].messages[]` 的稳定遍历位置识别；只读取同一消息
  `content.tool_call[]` 和 `content.usageTokens`，不读取 `canvas.answer.usageTokens` 重复快照。
- 步骤 Token 使用 `content.usageTokens.input_tokens + output_tokens`，与
  `total_token_consumption` 的聊天模式口径一致；`cache_tokens` 不额外加入。
- 调用按 `traceId + toolId` 唯一化，排除 `agent_delegation`、`agent_return` 和
  `framework_control` 后，将步骤 Token 关联给该步骤内每个不同的规范化 `toolName`。
- 一个步骤内同名工具出现多次时，`call_count` 按不同 `toolId` 计数，但
  `tool_step_count`、`token_observed_count` 和 `tool_associated_token_total` 只累计一次。

研究模式：`DIRECTLY_COMPUTABLE`。

- 工具调用读取 `message.part.updated` 中 `part.type == tool` 的 `part.messageID`、
  `part.callID` 和 `part.tool`；同一 `callID` 的 pending、running、completed 等生命周期快照先
  合并为一次调用。
- 模型步骤 Token 只读取同一 `messageID` 下 `part.type == step-finish` 的
  `part.tokens.total`。`message.updated.info.tokens` 和 `llm.response.result.usage` 是重复投影，
  不参与累计。
- 按 `part.messageID` 将模型步骤 Token 关联给该步骤内每个不同的规范化工具名；同一步骤同名
  工具只关联一次。
- 缺少 `messageID`、同一 `messageID` 出现冲突 Token 快照，分别返回
  `ERROR / missing_step_id`、`ERROR / conflicting_token_snapshot`。

多工具步骤允许跨工具重复关联同一份步骤 Token，因此：

```text
Σ tool_associated_token_total[tool_name]
可能大于 total_token_consumption
```

这种重叠是已确认的归因规则，不是重复日志。结果必须输出
`tool_token_attribution = full_step_association`、`single_tool_step_count`、
`multi_tool_step_count` 和 `shared_token_step_count`，禁止把各工具关联 Token 相加解释为 Trace
总 Token，也禁止把多工具步骤 Token 平均拆分。

#### 观测价值

- 识别哪些工具通常由高 Token 模型步骤产生，定位工具选择和参数生成阶段的上下文成本。
- 与 `per_tool_call_latency` 联合观察，可区分“模型规划成本高”和“工具执行本身慢”。
- 与 `repeat_call_count` 联合观察，可以判断同名工具反复调用是否持续触发高 Token 模型步骤。
- 本指标不能用于计算工具供应商费用；工具内部 Token 仍需工具终态提供独立 usage 字段。

#### 研究日志验算

指定研究日志共有 27 个唯一 `step-finish`，Trace 总 Token 为 `18,064,255`；其中 26 个步骤
包含业务工具，22 个为单工具步骤，4 个为双工具步骤，共对应 30 次唯一工具调用。按完整步骤
关联规则，工具步骤 Token 按步骤去重后的合计为 `17,353,963`，但各工具关联 Token 相加为
`19,803,424`，其中 `2,449,461` 是双工具步骤带来的合法重叠。

`excel` 出现在 22 个单工具步骤中：

```text
tool_associated_token_total[excel] = 14,904,502
tool_step_count[excel] = 22
per_tool_call_tokens[excel] = 14,904,502 ÷ 22 ≈ 677,477.36
```

### max_tool_token_share

#### 指标定义

`max_tool_token_share` 表示单条 Trace 内，关联模型步骤 Token 最多的规范化业务工具占端到端
模型总 Token 的比例。它衡量某一种工具与高 Token 模型步骤的关联集中度，不表示该工具服务
内部消耗，也不要求所有工具的占比加总为 `1`。

```text
tool_associated_token_total[tool_name] =
  Σ 包含该工具名的唯一模型步骤 Token

max_tool_token_share =
  max(tool_associated_token_total[tool_name])
  ÷ total_token_consumption
```

结果范围为 `(0, 1]`。同一模型步骤包含多个不同工具时，完整步骤 Token 分别进入各工具的
`tool_associated_token_total`，但分母始终使用不重复的 Trace 级 `total_token_consumption`。
因此各工具的 share 允许重叠，其总和可能大于 `1`。

指标同时输出 `max_token_tool_names` 和 `max_tool_associated_tokens`。多个工具并列最高时，
`max_token_tool_names` 使用按规范化工具名升序排列的 JSON 数组。

本指标要求 Trace 总 Token 以及全部工具关联步骤 Token 覆盖完整。存在缺失时返回
`NOT_COMPUTABLE / incomplete_token_coverage`；无业务工具调用时返回
`NOT_COMPUTABLE / no_tool_calls`；Trace 总 Token 为零时返回
`NOT_COMPUTABLE / zero_total_token_consumption`；工具调用无法关联模型步骤时返回
`NOT_COMPUTABLE / missing_tool_step_association`。

#### 字段映射

聊天模式：`DIRECTLY_COMPUTABLE`。

- 分母复用 `total_token_consumption`：汇总所有唯一模型消息的
  `content.usageTokens.input_tokens + output_tokens`。
- 分子复用 `per_tool_call_tokens` 的同消息 `toolName + usageTokens` 完整步骤关联结果。

研究模式：`DIRECTLY_COMPUTABLE`。

- 分母复用 `total_token_consumption`：按 `messageID` 去重并汇总全部 `step-finish.tokens.total`。
- 分子复用 `per_tool_call_tokens` 的同 `messageID` 工具与 `step-finish` 关联结果。
- 不读取 `message.updated.info.tokens` 或 `llm.response.result.usage`，避免重复计算。

#### 观测价值

- 比例高表示大部分模型 Token 出现在包含同一种工具的步骤中，应优先检查该工具对应的上下文、
  参数生成和重复调用链路。
- 与 `repeat_call_count` 联合观察，可以区分“少量高 Token 步骤”和“重复调用累计模型成本”。
- 与 `output_token_ratio`、`cache_token_ratio` 联合观察，可以进一步判断关联成本主要来自模型输出、
  新输入还是缓存上下文。
- 多工具步骤会让多个工具共享同一份 Token，因此该指标用于定位关联热点，不能用于工具间费用
  分摊。

#### 研究日志验算

```text
total_token_consumption = 18,064,255
max_token_tool_names = ["excel"]
max_tool_associated_tokens = 14,904,502
max_tool_token_share = 14,904,502 ÷ 18,064,255 ≈ 0.82508
```

### output_token_ratio

#### 指标定义

`output_token_ratio` 是 Trace 级模型输出 Token 占模型总 Token 的比例，用于解释工具工作流中
Token 是消耗在上下文输入还是模型输出。

```text
output_token_ratio =
  Σ 模型步骤 output_tokens
  ÷ total_token_consumption
```

本指标不是工具自身 Token 比例，也不把输出 Token 归属到具体工具。模型 Token 覆盖不完整时
返回 `NOT_COMPUTABLE / incomplete_token_coverage`；分母为零时返回
`NOT_COMPUTABLE / zero_total_token_consumption`。

#### 字段映射

聊天模式：`DIRECTLY_COMPUTABLE`。

- 分子：`canvas.agents[].messages[].content.usageTokens.output_tokens` 之和。
- 分母：同一批唯一模型步骤的 `input_tokens + output_tokens` 之和。
- `canvas.answer.usageTokens` 是最终 Agent 消息的重复快照，不参与计算。

研究模式：`DIRECTLY_COMPUTABLE`。

- 分子：唯一 `message.part.updated` 中 `part.type == step-finish` 的 `part.tokens.output` 之和。
- 分母：同一批唯一步骤的 `part.tokens.total` 之和。
- `message.updated.info.tokens` 和同 loop 的 `llm.response.result.usage` 是重复投影，不参与计算。

#### 观测价值

- 比例低且总 Token 高，说明成本主要来自上下文输入、缓存读取或多轮模型调用，而不是回答过长。
- 比例高时，可进一步检查模型是否产生过长中间文本或最终回答。
- 与 `tool_calls_count` 联合观察，可以判断工具调用轮次增加是否主要放大输入上下文。

### cache_token_ratio

#### 指标定义

`cache_token_ratio` 是 Trace 级缓存 Token 占模型总 Token 的比例。

```text
cache_token_ratio =
  cache_token_consumption
  ÷ total_token_consumption
```

缓存 Token 是模型步骤资源，不属于工具自身 Token。必须先确认供应商的缓存计数包含关系，禁止
在未知语义下把 cache Token 再次加入总 Token。

#### 字段映射

聊天模式：`CONDITIONALLY_COMPUTABLE`。

- 候选分子：`canvas.agents[].messages[].content.usageTokens.cache_tokens` 之和。
- 候选分母：相同模型步骤的 `input_tokens + output_tokens` 之和。
- 只有配置确认 `cache_tokens` 已包含在 `input_tokens` 中时才按上述公式计算；否则返回
  `NOT_COMPUTABLE / unknown_cache_token_semantics`。
- `canvas.answer.usageTokens` 不参与计算。

研究模式：`DIRECTLY_COMPUTABLE`。

- 分子：唯一 `message.part.updated` 中 `part.type == step-finish` 的
  `part.tokens.cache.read + part.tokens.cache.write` 之和。
- 分母：同一批唯一步骤的 `part.tokens.total` 之和。
- 每一步必须满足
  `total = input + output + reasoning + cache.read + cache.write`；不满足时返回
  `ERROR / inconsistent_token_breakdown`。

缺少 cache 分项返回 `NOT_COMPUTABLE / no_observable_cache_usage`；部分步骤缺失返回
`NOT_COMPUTABLE / incomplete_cache_token_coverage`；分母为零返回
`NOT_COMPUTABLE / zero_total_token_consumption`。

#### 观测价值

- 比例高表示模型调用主要在重复读取缓存上下文，优化重点应放在减少模型步骤数、缩短上下文或
  避免重复携带大型工具结果。
- 比例低但 input Token 高，说明主要消耗来自未缓存的新输入或频繁变化的上下文。
- 缓存命中不等于零成本或零时延，本指标只能解释 Token 构成，不能单独代表成本节省比例。

### 输出字段约束

单条 Trace 至少输出以下字段：

```text
tool_calls_count
output_token_ratio
cache_token_ratio
max_tool_token_share
max_token_tool_names[]
max_tool_associated_tokens
tool_token_attribution
max_tool_latency_share
max_latency_tool_names[]
max_tool_latency_total_ms
total_tool_latency_ms
end_to_end_latency_ms
per_tool_statistics[].tool_name
per_tool_statistics[].call_count
per_tool_statistics[].repeat_call_count
per_tool_statistics[].tool_step_count
per_tool_statistics[].single_tool_step_count
per_tool_statistics[].multi_tool_step_count
shared_token_step_count
per_tool_statistics[].tool_associated_token_total
per_tool_repeat_call_count[].tool_name
per_tool_repeat_call_count[].call_count
per_tool_repeat_call_count[].repeat_call_count
repeated_tool_name_count
total_repeat_call_count
per_tool_statistics[].per_tool_call_tokens
per_tool_statistics[].tool_latency_total_ms
per_tool_statistics[].per_tool_call_latency
```

每个指标或同名工具字段必须携带独立的 `status`、稳定 `reason` 和 `evaluator_version`。一个字段
不可计算不得阻止其他字段输出；不得用零值代替缺失 Token、缺失耗时或未知缓存语义。结果只
保留规范化工具名、数值、计数、状态和安全原因码，不保留工具参数、工具响应、query 或回答
正文。
