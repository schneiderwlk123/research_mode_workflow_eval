# 指标定义与字段映射

本文集中记录所有评测指标的定义和原始日志字段映射。后续指标按
`docs/specs/metric-development-order.md` 的顺序追加，不再为单个指标创建独立规格文件。

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

- 会话标识：`traceId`。
- 调用候选：`tool_call_queue` 中 `event_type == "tool_call"` 的对象。
- 唯一调用：当前没有统一 `callID`，一个 `tool_call` 对象视为一次调用，并按数组位置
  生成会话内标识。
- 请求已观察：存在且非空的 `tool_request_args`。
- 终态已观察：存在且非空的 `tool_response_result`。
- 缺少请求或响应的调用仍计入分母，但不计入分子。

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

- 会话标识：`traceId`。
- 唯一调用：`tool_call_queue` 中每个 `event_type == "tool_call"` 的对象。
- 工具名称：`tool_request_args.toolName`。
- 参数：`tool_request_args.arguments`。
- 响应内容：优先使用 `tool_response_result.result`，为空时使用 `rawResult`。

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

真实日志微平均验算：聊天模式 `35 / 907 ≈ 0.03859`，研究模式
`1 / 30 ≈ 0.03333`。

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

- 会话标识：`traceId`。
- 唯一调用：`tool_call_queue` 中每个 `event_type == "tool_call"` 的对象。
- 工具名称：`tool_request_args.toolName`。
- 参数：`tool_request_args.arguments`；对象或可解析为对象的 JSON 字符串均可计算。

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

真实日志微平均验算：聊天模式 `696 / 832 ≈ 0.83654`；研究模式
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

- 会话标识：`traceId`。
- 唯一调用：`tool_call_queue` 中每个 `event_type == "tool_call"` 的对象。
- 工具名称：`tool_request_args.toolName`。
- 参数：`tool_request_args.arguments`。

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

真实日志微平均验算：聊天模式 `642 / 643 ≈ 0.99844`，其中 1 个调用类型不合法，
13 个调用携带额外字段但按当前口径仍合法；研究模式 `5 / 5 = 1.0`。

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

- 用户需求：`query`。
- 会话标识：`traceId`。
- 调用标识：`tool_call_queue[].tool_request_args.toolId`。
- 工具名称和参数：`tool_request_args.toolName/arguments`。

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
