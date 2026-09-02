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
- 未知工具 Schema 不进入分子或分母；结果同时报告 Schema 覆盖调用数、总调用数和
  `schema_coverage_rate`，避免把低覆盖率隐藏在完整率中。
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

Schema 覆盖率 = 4 ÷ 5 = 0.8
必填参数完整率 = 4 ÷ 6 ≈ 0.66667
```

真实日志微平均验算：聊天模式 `696 / 832 ≈ 0.83654`，Schema 覆盖
`643 / 907 ≈ 0.70893`；研究模式 `10 / 11 ≈ 0.90909`，Schema 覆盖
`5 / 30 ≈ 0.16667`。
