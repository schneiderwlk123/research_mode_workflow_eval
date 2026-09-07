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

## 1. 调用事件完整率

### 指标定义

稳定指标名：`tool_call_event_completeness_rate`

```text
调用事件完整率 =
  同时观察到请求事件和终态响应事件的唯一工具调用数
  ÷ 已识别的唯一工具调用数
```

- 结果范围为 `[0, 1]`。
- 失败、超时或取消只要存在明确终态，也属于完整调用；本指标不判断业务是否成功。
- 没有工具调用时返回 `NOT_COMPUTABLE`，原因 `no_tool_calls`。
- 研究模式只要发现工具事件缺少或携带空白 `callID`，整次会话返回
  `NOT_COMPUTABLE`，原因 `missing_call_id`，不得生成临时标识。

### 字段映射

聊天模式：

- 单次执行标识：`canvas.meta.traceId`。
- 调用候选：`canvas.agents[].messages[].content.tool_call[]` 中具有
  `toolId + toolName` 的普通工具请求。
- 唯一调用：按 `traceId + toolId` 去重；新版样本中 4,266 个普通请求的 `toolId` 均非空且
  唯一。
- 请求已观察：存在上述普通工具请求定义。
- 终态已观察：同一 Trace 的任意 `content.tool_result[]` 中存在相同 `toolId`。结果即使只
  保留 `toolId` 也表示结果事件已观察，但不代表响应正文可比较。
- 只出现于结果侧的 Agent 返回不进入分母；请求缺少匹配结果时仍计入分母但不计入分子。

研究模式：

- 会话标识：`sessionID + userMessageID`，优先从 `turn.start` 恢复。
- 调用候选：`message.part.updated` 中 `part.type == "tool"` 的事件。
- 唯一调用：按 `part.callID` 去重。
- 请求已观察：同一 `callID` 至少出现一次 `part.state.status` 为 `pending` 或
  `running` 的事件。
- 终态已观察：同一 `callID` 至少出现一次状态为 `completed`、`failed`、`error`、
  `cancelled`、`canceled`、`timeout` 或 `timed_out` 的事件。
- 相同 `callID + status` 的重复事件只记录诊断，不重复计算调用。

规范化结果仅保留 `trace_id`、`mode`、`call_id`、`request_observed`、
`terminal_observed`、`source_event_count` 和安全诊断计数，不保存请求参数、响应正文或
`part.state.input/output/raw`。

新版聊天日志微平均验算：296/336 条 Trace 含普通工具请求，唯一请求 4,266 次，其中
4,251 次观察到同 `toolId` 结果，调用事件完整率 `4251 / 4266 ≈ 0.99648`；其余 40 条
Trace 返回 `NOT_COMPUTABLE / no_tool_calls`。

## 2. 完全重复调用率

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

## 3. 必填参数完整率

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

## 4. Schema 合法率

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

## 5. 参数值准确率

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
- Judge 对一个调用整体判断一次，但必须返回逐字段结构化结果。字段状态只能是
  `correct`、`incorrect` 或 `not_assessable`。
- query 明确约束且工具字段能够承载的遗漏参数可由 Judge 追加为
  `incorrect / missing_query_required`；追加路径必须存在于对应工具 Schema。
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
- 工具名称、工具描述，以及 MCP 手册中的参数路径与描述。
- 当前调用的实际参数。

Judge 适配器必须声明稳定的 `judge_name`、`judge_version` 和 `prompt_version`，并返回：

```json
{
  "fields": [
    {
      "field_path": "start_date",
      "verdict": "correct",
      "reason_code": "normalized_equivalent",
      "confidence": 0.98
    }
  ]
}
```

评测器重新校验 Judge 输出：字段路径不得重复，置信度必须为 `[0, 1]` 内有限数值，实际
参数字段不得遗漏；实际参数之外的路径必须属于工具 Schema，且只能用于
`incorrect / missing_query_required`。`reason_code` 必须来自按 verdict 划分的固定安全
白名单，防止 Judge 把 query 或参数正文写入持久化诊断。非法输出按单调用 Judge 失败隔离。

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
全部候选调用失败时返回 `ERROR / judge_failure`。结果只保留聚合计数、原因类别及版本，不得
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

新版聊天日志重算状态：4,266 次普通调用中可构造 2,911 个手册覆盖 Judge 候选，覆盖 271 条
Trace。当前未执行真实 Judge，因此本次不产生 `parameter_value_accuracy_rate` 数值。

## 6-7. 路由 Precision 与路由 Recall

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
- 标准能力集合不是从实际调用反推。路由 Judge 必须综合整体用户意图、所有可见子任务
  意图、完整 MCP 能力目录以及历史安全映射，选择完成需求所需的最小充分能力集合。
- 两项指标都只评估路由选择层。调用是否成功属于执行质量，不混入路由选择判断。
- 不在 MCP 手册中的 `respond` 等框架工具不进入实际能力集合，只记录
  `out_of_scope_tool_call_count`；Judge 不得选择未在当前目录注册的能力。
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

Judge 返回：

```json
{
  "subtasks": [
    {
      "intent_signature": ["market_data", "valuation"],
      "subtask_type": "company_analysis",
      "required_capability_ids": ["pricePerformance"]
    }
  ],
  "required_capability_ids": ["pricePerformance"]
}
```

- `intent_signature` 和 `subtask_type` 只能使用小写 snake_case，禁止包含公司、人名、
  证券代码、日期、金额、地区或用户原文。
- 顶层能力集合必须严格等于所有子任务能力集合的去重并集。
- 所有能力 ID 必须存在于当前 MCP 目录；评测器会重新校验，不信任 Judge 输出。
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
  `NOT_COMPUTABLE / conflicting_call_definition`。参数差异不影响路由能力集合。
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
  时返回 `conflicting_call_definition`。

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
计数，`routing_precision` 与 `routing_recall` 的 evaluator 版本现均为 `1.1.0`。

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

## 8. 子任务冗余率

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

## 9. 意图识别准确率

### 指标定义

稳定指标名：`intent_recognition_accuracy`

```text
意图识别准确率 =
  Judge 判为 correct 的可判定会话数 ÷
  （Judge 判为 correct 的会话数 + Judge 判为 incorrect 的会话数）
```

- 单会话 `correct` 记为 `1.0`，`incorrect` 记为 `0.0`；跨会话聚合即为上述公式。
- 本指标是可追溯的 Judge 代理标准，用来判断用户 query 与代理实际行为证据的实质意图
  是否对齐，不虚构结构化意图预测字段，也不等同于人工 Golden Benchmark。
- 确定性规则负责字段存在性、证据去重、调用 ID 去重与冲突、前置失败和 Judge 输出校验；
  只有语义对齐判断调用 Judge。
- Judge 输出必须且只能包含 `verdict`、`reason_code`、`confidence`。`confidence` 必须是
  `[0, 1]` 内的有限数值，且 verdict 与 reason code 必须匹配固定白名单。
- `not_assessable` 返回 `NOT_COMPUTABLE / insufficient_intent_evidence`，不进入准确率分母。
- Judge 调用异常返回 `NOT_COMPUTABLE / judge_failure`；结构化输出非法返回
  `NOT_COMPUTABLE / invalid_judge_output`。
- evaluator 版本为 `1.1.0`；Judge 名称、模型版本和 prompt 版本进入安全元数据。

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
- 代理证据还包括已解析到 MCP 目录的能力和 `canvas.answer.answer`；本指标不读取工具参数或
  `tool_result` 正文。手册外框架工具只进入范围外诊断。
- 完全相同的证据正文仅保留第一次出现的位置和类型；未知框架工具只进入范围外诊断。
- 只有 query、没有任何子任务、MCP 能力或最终回答证据时返回 `no_intent_evidence`。
- 当没有其他代理证据且回答为固定模型服务失败文本时返回 `pre_intent_failure`。
- Canvas 各层容器类型非法时返回 `invalid_chat_trace_structure`；旧版 `tool_call_queue` 不再
  支持。

研究模式：`CONDITIONALLY_COMPUTABLE`。

- 必需用户意图：首个顶层 `query`，兼容 `body.query`；缺失时返回 `missing_query`。
- 代理证据：`body.task`、`body.description`，以及
  `result.toolCalls[].toolCallId/toolName` 解析得到的 MCP 能力。
- 调用按 `toolCallId` 去重；同 ID 同工具只增加重复诊断，同 ID 不同工具返回
  `conflicting_call_definition`，调用缺少 ID 返回 `missing_call_id`。
- 只有 query、没有任何规划文本或 MCP 能力时返回 `no_intent_evidence`。

两种模式的 query、子任务、规划、回答、调用参数和 Judge 原始响应都只存在于内存请求中，
不得进入评测结果或持久化介质。结果只保留模式、版本、证据与能力数量、诊断计数、Judge
安全 reason code 和 confidence。

新版聊天日志重算状态：312/336 条 Trace 可构造意图 Judge 候选，24 条为明确前置失败；候选
包含 1,505 个去重行为证据片段和按 Trace 去重后合计 950 个实际 MCP 能力；另记录 2 个
无文本子 Agent 诊断、2 个重复证据和 1,355 次手册外工具调用。未运行 Judge，因此本次不
产生 `intent_recognition_accuracy` 数值。

### Judge 判定口径

- `correct`：`material_intents_aligned`、`normalized_equivalent`。
- `incorrect`：`wrong_primary_intent`、`material_intent_omitted`、
  `unrelated_agent_behavior`。
- `not_assessable`：`insufficient_agent_evidence`、`ambiguous_user_intent`。

### 模拟计算

```text
会话 A：用户要求查询行情，代理子任务和 MCP 能力均为行情查询 → correct → 1.0
会话 B：用户要求行情和新闻，代理证据只覆盖行情 → incorrect → 0.0
会话 C：用户意图含糊，现有行为证据无法稳定判定 → not_assessable → 不进入分母

correct = 1
incorrect = 1
意图识别准确率 = 1 ÷ (1 + 1) = 0.5
```

## 10. 时间范围提取准确率

### 指标定义

稳定指标名：`time_range_extraction_accuracy`

```text
时间范围提取准确率 =
  正确提取的预期时间字段数 ÷ 用户需求中可评估的时间字段总数
```

- 评估字段包括开始时间、结束时间、数据频率，以及实际值/预测值属性；多段区间分别计数。
- 用户未指定的字段不进入分母；明确要求但工具参数遗漏的字段计为错误。
- 额外时间条件若改变查询范围，应使受影响的预期字段判错，并计入
  `unexpected_constraint_count` 安全诊断。
- 本指标使用 LLM Judge 代理标准，不等同于人工 Golden Benchmark。Judge 只返回聚合计数、
  固定 reason code 和 confidence，不得返回日期、query、参数值或解释正文。
- Judge 返回 `assessed` 时，`correct_field_count + incorrect_field_count` 必须大于零；结果为
  `correct_field_count / assessable_field_count`。
- Judge 返回 `not_assessable` 时，使用 `no_temporal_requirement`、
  `missing_reference_time`、`ambiguous_temporal_requirement` 或
  `insufficient_time_evidence`，结果为 `NOT_COMPUTABLE`。
- Judge 调用异常或输出非法分别返回 `NOT_COMPUTABLE / judge_failure` 和
  `NOT_COMPUTABLE / invalid_judge_output`。
- evaluator 版本为 `1.1.0`；Judge、模型、prompt 和 MCP 手册版本进入安全元数据。

### 相对时间规则

- 相对时间必须绑定日志中的会话/调用时间或调用方显式提供的 `reference_time`，不得使用
  评测运行当天，保证结果可复现。
- `近 N 年` 按以锚点为终点的 N 年滚动区间；`今年以来` 从锚点所在年份 1 月 1 日开始；
  `上一完整年度` 为锚点年份之前的一个自然年。
- 用户要求相对时间但没有可靠锚点时返回 `missing_reference_time`。
- 工具省略可选日期时，只有 MCP 参数说明明确给出默认观察日语义，Judge 才能使用该默认值。

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

新版聊天日志重算状态：312/336 条 Trace 可构造时间范围 Judge 候选，24 条为明确前置失败；
336 条均具有可解析的 `canvas.meta.create_time`，候选中包含 4,266 次唯一普通工具调用，
其中 2,911 次被手册覆盖、1,355 次为未知 Schema。当前未执行时间 Judge，因此本次不产生
`time_range_extraction_accuracy` 数值。研究日志形成 1 个候选，30 次唯一调用中 5 次被手册
覆盖，时间锚点来源为 `event`。

### 模拟计算

```text
用户要求：2020A-2025A 历史数据，2026E-2028E 预测数据，年度频率
实际参数：2020A-2024A 历史数据，2026E-2028E 预测数据，年度频率

可评估字段数 = 7
正确字段数 = 6
错误字段数 = 1
时间范围提取准确率 = 6 ÷ 7 ≈ 0.85714
```

## 11. 证据查准率

### 指标定义

稳定指标名：`evidence_precision_rate`

```text
证据查准率 =
  Judge 判定为 relevant 的唯一证据单元数 ÷ 实际取得的唯一证据单元总数
```

- `evidence_unit` 是 Agent 从外部数据源成功取得、具有稳定来源边界、包含可验证事实，且能
  独立参与相关性判断的最小来源记录。RAG Chunk、单条搜索结果、数据库行、行情或财务
  记录、带来源定位的网页/报告片段均可属于证据。
- 用户 query、规划、推理、最终回答、工具参数、错误消息、框架工具输出，以及 Excel、代码
  或公式生成的派生结果不属于本指标的证据。证据工具必须通过版本化 MCP 手册和显式工具
  名单共同登记，避免把计算或写作工具误计为外部来源。
- 显式工具名单和别名映射生成稳定 `evidence_scope_version`；它与 MCP 手册版本一同进入
  结果，工具范围改变时不会与旧口径混合聚合。
- JSON 数组及明确的 `data/items/results/documents/chunks/records` 容器按原生元素拆分；单条
  结构化记录作为一个单元；字符串化 JSON 先解析。没有原生边界的长文本不按标点或长度
  猜测切分。
- 只有记录自带 `chunk_id/record_id/row_id/source_id/document_id/doc_id/url/link` 等稳定
  来源标识，且来源标识与规范化内容都相同时才在会话内去重。缺少来源标识时保留每次实际
  返回，避免合并不同来源的相同事实；SHA-256 摘要仅用于内存去重，不进入结果。重复调用
  成本仍由 `exact_duplicate_tool_call_rate` 评估。
- Judge 必须为每个单元返回一次 `relevant`、`irrelevant` 或 `not_assessable`。只要存在
  `not_assessable`，完整分母就无法可靠判断，返回
  `NOT_COMPUTABLE / incomplete_relevance_judgment`，不得将其当作无关或从分母删除。
- Judge 异常和非法结构分别返回 `NOT_COMPUTABLE / judge_failure` 与
  `NOT_COMPUTABLE / invalid_judge_output`。evaluator 版本为 `1.1.0`，Judge 名称、模型、
  prompt 和 MCP 手册版本进入安全元数据。

### 相关性口径

- `relevant`：证据直接回应用户需求，支持或反驳必要事实，为必要计算提供输入，或约束必要
  的主体、时间和范围。
- `irrelevant`：证据仅主题相似、与需求无关、属于不必要背景，或不包含事实内容。
- `not_assessable`：单元内容不足、用户需求含糊，或当前 Judge 不支持该证据格式。
- 最终答案不进入 Judge 请求，防止用生成结果反向合理化检索结果。Judge 不得使用外部知识
  补全证据，只能比较本次 query 和实际取得的证据单元。

### 分模式可计算性与字段映射

聊天模式：`CONDITIONALLY_COMPUTABLE`。

- 单次执行标识：`canvas.meta.traceId`；用户需求：`canvas.meta.query`，缺失时返回
  `missing_query`。
- 调用候选：具有 `toolId + toolName` 的普通工具请求；只有显式证据工具目录中的 MCP 工具
  进入范围。
- 调用标识：请求的 `toolId`，与 `traceId` 组成复合键；新版样本不允许退化为数组位置。
- 响应在同一 Trace 内按 `toolId` 关联 `content.tool_result[]`，优先使用 `result`，为空时使用
  `rawResult`；失败状态、空响应和范围外工具不产生证据单元。结果侧孤立 Agent 返回不属于
  证据调用。
- 同一 `toolId` 的重复请求和重复结果只观察一次；同 ID 不同工具返回
  `conflicting_call_definition`，同 ID 不同终态结果返回 `conflicting_call_result`。缺少调用 ID、
  工具名或非法容器均显式返回安全原因，不能退化为数组位置。
- 旧版 `tool_call_queue` 不再支持，输入旧结构返回 `invalid_chat_trace_structure`。

研究模式：`CONDITIONALLY_COMPUTABLE`。

- 会话标识：`sessionID + userMessageID`；用户需求优先读取顶层 `query`，兼容
  `body.query`。
- 工具定义来自 `result.toolCalls[].toolCallId/toolName`；响应来自相同 `callID` 的
  `part.type == "tool"` 且 `part.state.status == "completed"` 的事件，正文优先读取
  `part.state.output`，其次读取 `part.state.raw`。
- 调用按 `callID` 去重；缺少 ID 返回 `missing_call_id`，同 ID 不同工具返回
  `conflicting_call_definition`。重复终态只读取第一次成功响应。

两种模式没有证据工具、没有成功证据响应或所有响应均无稳定边界时，分别返回
`no_evidence_calls`、`no_successful_evidence_responses` 和 `no_stable_evidence_units`。
成功响应具有明确结构但结果集合为空时返回 `no_evidence_units`。
部分成功响应可以拆分、部分不能拆分时返回 `incomplete_evidence_segmentation`，不得只用
已拆分子集计算并静默缩小分母。
query、证据正文、内容摘要与 Judge 原始响应只存在于内存中；最终结果仅保留单位数量、
相关性计数、安全诊断和版本标识。

新版聊天日志重算状态：当前仓库仍没有生产版显式证据工具目录，且本次未运行证据 Judge，
因此不能确定证据调用分母，也不产生 `evidence_precision_rate` 数值。使用手册全部 77 个
工具构造的非生产“结构上限目录”仅用于验证解析链路：336 条 Trace 中 140 条形成候选，
共观察 2,911 次目录内调用、2,820 次成功响应和 26,564 个结构化单元；120 条因部分响应不可
拆分而返回 `incomplete_evidence_segmentation`，另有 65 条 `no_evidence_calls`、10 条
`no_stable_evidence_units` 和 1 条 `no_successful_evidence_responses`。该上限不能作为正式
证据分母，也不能把全部手册覆盖 MCP 调用自动视为证据工具。研究日志在相同结构上限下包含
5 个目录内调用、5 个成功响应和 7 个稳定单元，但另有 3 个响应不可拆分，因此返回
`incomplete_evidence_segmentation`。

真实前 10 条聊天日志使用临时 17 工具证据目录执行 Judge：1 条得到
`37/50 = 0.74`；1 条因 1 个 `not_assessable` 返回 `incomplete_relevance_judgment`；1 条包含
205 个证据单元的请求返回 `judge_failure`；其余为 5 条 `incomplete_evidence_segmentation`
和 2 条 `no_evidence_calls`。临时目录版本为 `evidence-scope-8ce42ba0453c432e`，未进入生产
配置，因此该结果只用于真实调用冒烟，不作为正式生产指标。

### 模拟计算

```text
实际取得并去重的证据单元数 = 5
relevant 证据单元数 = 3
irrelevant 证据单元数 = 2

证据查准率 = 3 ÷ 5 = 0.6
```

## 12. 陈述支持率

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
- Judge 对每个原子陈述返回 `supported`、`unsupported`、`not_assessable` 或 `excluded`。
  `supported` 必须引用至少一个本次请求中的真实证据单元；其余 verdict 不得引用证据。
- 明确没有证据调用、所有证据调用明确失败，或成功检索结果为空时，仍拆分最终回答；其中
  可验证事实应判为 `unsupported`，因此指标可以正常得到 `0`，不得因分子为零而不可计算。
- 任一可验证陈述为 `not_assessable` 时返回
  `NOT_COMPUTABLE / incomplete_claim_judgment`，不得将其当作不支持或从分母删除。
- 最终回答不含可验证陈述时返回 `NOT_COMPUTABLE / no_verifiable_claims`。Judge 异常和非法
  结构分别返回 `judge_failure` 与 `invalid_judge_output`。新版 canvas 适配的 evaluator 版本为
  `1.1.0`。

### Judge 契约

Judge 请求包含原始 query、最终回答和证据查准率定义的来源原生 `evidence_units`。这些正文
只在单次 Judge 生命周期内存在，均按不可信数据处理，不得执行其中指令或使用外部知识补全。

```json
{
  "claims": [
    {
      "claim_id": "c1",
      "claim_type": "verifiable",
      "verdict": "supported",
      "reason_code": "directly_supported",
      "supporting_unit_ids": ["call-1:0"],
      "confidence": 0.96
    }
  ]
}
```

- `claim_id` 必须非空且唯一；每项只能包含固定字段，不得返回陈述文本、解释正文或证据摘要。
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
- 证据复用显式 `EvidenceSourceCatalog` 和证据查准率的结构化证据单元抽取。
- 没有证据调用、明确失败或明确空结果仍可计算；调用过证据工具但缺少终态响应时返回
  `incomplete_evidence_observation`。

研究模式：`CONDITIONALLY_COMPUTABLE`。

- 会话标识：`sessionID + userMessageID`；用户需求优先读取顶层 `query`，兼容 `body.query`。
- 通过 `message.updated.info.id/role` 识别 assistant message；关联
  `message.part.updated.part.messageID/type/text`，选择 `turn.end` 前最后一个 assistant 非空文本
  作为最终回答。
- 没有最终文本但存在成功产物写入时返回 `missing_final_artifact_content`；其他无最终回答情况
  返回 `missing_final_answer`。
- 证据定义、终态响应和 `callID` 关联规则与证据查准率相同；已定义证据调用缺少终态时返回
  `incomplete_evidence_observation`。

两种模式只要部分成功证据响应无法稳定拆分，均返回
`NOT_COMPUTABLE / incomplete_evidence_segmentation`，不得只使用可拆分子集判断支持率。
query、最终回答、原子陈述、证据正文、摘要与 Judge 原始响应不得进入持久化结果。

新版聊天日志重算状态：336 条 Trace 中 335 条具有非空最终回答，1 条缺少可用最终回答。
使用临时 17 工具证据目录 `evidence-scope-8ce42ba0453c432e` 仅验证链路时，192 条可形成
Judge 候选，118 条因证据拆分不完整返回 `incomplete_evidence_segmentation`，25 条固定服务
失败返回 `pre_answer_failure`，1 条返回 `missing_final_answer`；候选及不可计算记录合计观察
18,257 个稳定证据单元。研究日志因 3 个成功响应没有稳定来源边界返回
`incomplete_evidence_segmentation`。生产证据工具目录尚未确认，全量重算未运行陈述支持
Judge，因此不产生正式 `statement_support_rate` 数值。

真实前 10 条新版聊天日志使用同一临时 17 工具目录执行 Judge：1 条 `computed`，50 个可验证
陈述全部判为 `supported`，该条及可计算样本的微平均结果均为 `50/50 = 1.0`；4 条返回
`incomplete_evidence_segmentation`，1 条返回 `missing_final_answer`，1 条返回
`pre_answer_failure`。其余 Judge 候选中，1 条空证据请求返回 `invalid_judge_output`，2 条分别在
包含 205 和 50 个证据单元时于 120 秒超时边界返回 `judge_failure`。成功请求耗时约 95.446 秒。
该临时目录未进入生产配置，且样本仅有 1 条成功计算，因此结果只用于真实链路冒烟，不作为
正式生产指标基线。

### 模拟计算

```text
最终回答：“A 公司 2025 年收入 100 亿元，同比增长 10%，建议关注。”

C1 收入 100 亿元 -> supported
C2 同比增长 10% -> unsupported
“建议关注” -> excluded

陈述支持率 = 1 ÷ 2 = 0.5
```

## 13. 最终数值与召回证据一致率

### 指标定义

本指标只评估最终回答中声称直接来自中间召回证据的事实数值。用户输入数字、日期、证券代码、
序号、模型自行计算的合计或比率以及非事实数字均排除，避免与公式计算准确率混淆。

```text
最终数值与召回证据一致率 =
  一致的直接召回数值声明数 ÷ 可验证的直接召回数值声明总数
```

Judge 只负责拆分数值声明，按主体、指标、时间、统计范围、单位、币种和明确来源完成语义对齐，
并返回答案值、证据值、统一单位和倍率。evaluator 使用 `Decimal` 确定性比较，不接受 Judge 直接
给出“一致/不一致”结论。

```text
答案规范值 = answer_value × answer_scale
证据规范值 = evidence_value × evidence_scale
舍入容差 = 0.5 × 10^(-answer_decimal_places) × answer_scale

一致条件 = |答案规范值 - 证据规范值| <= 舍入容差
```

- `aligned_evidence` 必须引用至少一个真实 `evidence_unit_id`，并同时提供双方数值、正倍率、
  统一单位和答案展示小数位。
- 完整证据中没有对应值时使用 `no_matching_retrieved_value`，该声明计入分母并判为不一致。
- 用户输入、日期或编号、派生计算和非事实数字分别以固定 reason code 排除。
- 来源冲突、单位不明、数值声明歧义或证据不完整返回 `not_assessable`；只要存在此类声明，
  整个会话返回 `NOT_COMPUTABLE / incomplete_numeric_judgment`，不得缩小分母。
- 最终回答没有直接召回数值时返回 `NOT_COMPUTABLE / no_retrieved_numeric_claims`。
- 数值、回答、证据正文和 Judge 原始响应只存在于内存中；持久化结果仅包含计数和版本信息。

### 分模式可计算性与字段映射

聊天模式：`CONDITIONALLY_COMPUTABLE`。

- 会话标识、需求和最终回答：`canvas.meta.traceId`、`canvas.meta.query` 和
  `canvas.answer.answer`。
- 证据复用显式 `EvidenceSourceCatalog`，从普通工具请求中筛选已登记证据工具，并按
  `traceId + toolId` 关联 `content.tool_result[]` 的成功响应。
- 缺少最终回答、证据终态或稳定证据边界时沿用 `missing_final_answer`、
  `incomplete_evidence_observation` 和 `incomplete_evidence_segmentation`。

研究模式：`CONDITIONALLY_COMPUTABLE`。

- 会话标识：`sessionID + userMessageID`。
- 最终回答选择 `turn.end` 前最后一个 assistant 文本；证据按 `callID` 连接工具定义和成功终态。
- 成功生成产物但日志没有最终正文时返回 `missing_final_artifact_content`；接入产物读取能力前
  不检查文件内部数值。

### 模拟计算

```text
证据：收入 123.46 亿元；净利润 10.20 亿元；毛利率 25.3%
回答：收入 123.5 亿元；净利润 12.2 亿元；毛利率 25.3%

收入：正常舍入，一致
净利润：不一致
毛利率：一致

最终数值与召回证据一致率 = 2 ÷ 3 ≈ 0.66667
```

新版聊天日志重算状态：最终回答字段可直接读取，但没有生产版证据工具目录，也未运行数值
对齐 Judge，因此本次不产生 `retrieved_numeric_consistency_rate` 数值。

## 14. MCP 工具调用膨胀率

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
- 调用事件：具有 `toolId + toolName` 的普通 `content.tool_call[]` 请求。
- 调用 ID、工具名和入参：请求的 `toolId/toolName/arguments`。
- 缺少调用 ID 或入参分别返回 `missing_call_id`、`missing_call_arguments`；非法队列或事件返回
  `invalid_tool_call_queue`、`invalid_tool_call_events`。

研究模式：`DIRECTLY_COMPUTABLE`。

- 任务边界及标识：`sessionID + userMessageID`。
- 调用定义：`result.toolCalls[].toolCallId/toolName/input`。
- 生命周期补充：`message.part.updated.part.callID/tool/state.input`；按 `callID` 合并
  `pending/running/completed`，不得把状态事件条数当成调用次数。
- 已观察到手册内 MCP 生命周期但没有任何可用入参定义时返回 `missing_call_arguments`。

两种模式均使用版本化 `CapabilityCatalog` 解析手册直接名称和 `mcp__...__工具名`。入参无法
稳定序列化时返回 `unserializable_call_arguments`；没有手册内 MCP 时返回
`NOT_COMPUTABLE / no_registered_mcp_calls`。结果只保留 `manual_version`、调用计数、签名计数、
膨胀计数和安全诊断，不保留工具名、入参、摘要或响应正文。

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

## 15. 首次工具决策时延

### 指标定义

本指标衡量当前用户请求创建后，模型首次决定进入子 Agent 或工具执行链所需的时间。它只使用
事件时间，不读取工具参数、响应或用户正文。

```text
首次工具决策时延（毫秒） = 最早有效决策时间 - 当前请求开始时间
```

新版聊天日志只保留请求起点，没有子 Agent 或工具决策节点的绝对时间，因此当前不可计算。
研究日志仍使用工具 `pending` 事件，而不是工具执行开始时间或可能晚于工具完成才写入的
`llm.response.result.toolCalls` 时间。

### 分模式可计算性与字段映射

聊天模式：`NOT_COMPUTABLE`。

- 当前请求开始可读取 `canvas.meta.create_time`。
- 子 Agent 和普通工具决策可从 `content.tool_call[]` 识别，但对象都没有独立绝对时间戳。
- `message.content.durationSeconds` 是当前 Agent 消息或步骤自身耗时，不能反推该消息或其中
  首个调用的绝对发生时间；数组位置也不能替代时间。
- 只要存在决策节点就返回 `NOT_COMPUTABLE / missing_tool_decision_timestamp`；完全没有
  子 Agent 或普通工具决策时返回 `NOT_COMPUTABLE / no_tool_decision`。

研究模式：`DIRECTLY_COMPUTABLE`。

- 当前请求开始：`turn.start.ts`。
- 工具决策：`message.part.updated` 中 `part.type == "tool"` 且
  `part.state.status == "pending"` 的事件 `ts`。
- 相同 `callID` 的重复 pending 事件只保留最早时间，并记录重复计数。
- `part.state.time.start` 是执行开始时间，不用于本指标。

两种模式都以 `trace_id` 为单次评测边界，结果单位固定为毫秒。没有决策、缺少起点、聊天
决策时间戳、研究 pending 时间、观察不完整或研究调用 ID 时，分别返回
`no_tool_decision`、`missing_turn_start_time`、`missing_tool_decision_timestamp`、
`missing_tool_decision_time`、`incomplete_tool_decision_observation` 或 `missing_call_id`。
非法时间戳和负时延分别返回 `ERROR / invalid_timestamp` 与 `ERROR / invalid_event_order`，
不得填零或裁剪长尾。

### 模拟计算

```text
若未来聊天 schema 增加决策时间：
请求 create_time = 10:00:00.000
首个子 Agent 决策时间 = 10:00:06.000
首个 tool_call 决策时间 = 10:00:09.000

首次工具决策时延 = min(6 秒, 9 秒) = 6000 毫秒

研究 turn.start.ts = 03:25:38.806
首个工具 pending.ts = 03:26:07.665

首次工具决策时延 = 28859 毫秒
```

新版聊天日志重算状态：304/336 条 Trace 存在子 Agent 或普通工具决策，但全部缺少决策绝对
时间，因此返回 `missing_tool_decision_timestamp`；其余 32 条返回 `no_tool_decision`。新版
聊天模式不能汇总该指标的毫秒值。

## 20. 超时率

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

- 唯一调用：具有 `toolId + toolName` 的普通工具请求，按 `traceId + toolId` 标识。
- 工具名称：请求的 `toolName`；匹配结果提供非空 `toolName` 时必须一致，否则返回
  `conflicting_call_definition`。
- 完整调用耗时：同 `toolId` 的 `content.tool_result[].durationSeconds × 1000`。该字段是单次
  工具执行耗时，不与 `message.content.durationSeconds` 相加或相减。
- 显式超时：匹配结果顶层或结构化 `result/rawResult` 中的 `status/state` 命中策略状态集合。
- 缺少匹配结果或结果缺少合法 `durationSeconds` 时，在该工具已有 SLA 的前提下按
  `missing_time_timeout` 分类；结果侧孤立 Agent 返回不进入调用分母。

研究模式：`CONDITIONALLY_COMPUTABLE`。

- 唯一调用：按 `message.part.updated.part.callID` 合并生命周期事件。
- 工具名称：`part.tool`，支持目录别名和 `mcp__...__工具名` 后缀解析。
- 开始时间：同一 `callID` 的最早 `part.state.time.start`。
- 结束时间：同一 `callID` 终态事件的最晚 `part.state.time.end`。
- 显式超时：`part.state.status` 命中策略状态集合。

两种模式都必须能从版本化目录解析每个工具的 `timeout_ms`。当前仓库没有生产 SLA 目录，
因此真实日志在配置落地前返回 `NOT_COMPUTABLE / missing_tool_sla`；不能因未观察到显式
timeout 就输出 0%。无调用、缺少研究调用 ID、非法队列或未知 SLA 分别返回
`no_tool_calls`、`missing_call_id`、`invalid_tool_call_queue`、`missing_tool_sla`。非法时间、
负时序和调用定义冲突分别返回 `ERROR / invalid_timestamp`、`ERROR / invalid_event_order`、
`ERROR / conflicting_call_definition`。

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
