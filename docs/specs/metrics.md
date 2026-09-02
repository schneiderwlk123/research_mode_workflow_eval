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
