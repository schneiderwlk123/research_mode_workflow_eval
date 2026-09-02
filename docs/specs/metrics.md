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

## 2. 调用成功率

### 指标定义

稳定指标名：`tool_call_success_rate`

```text
调用成功率 =
  明确符合成功规则的唯一工具调用数
  ÷ 已识别的唯一工具调用总数
```

- 结果范围为 `[0, 1]`。
- 采用严格全量口径：只要任一终态调用缺少工具名、成功规则或可判定的业务结果，整条
  Trace 返回 `NOT_COMPUTABLE`，原因 `undetermined_tool_outcome`，不得缩小分母。
- 缺少终态、明确失败、取消或超时的调用计入分母但不计入成功数。
- 没有工具调用时返回 `NOT_COMPUTABLE`，原因 `no_tool_calls`。
- 成功规则必须携带稳定 `rule_id`；评测结果记录实际使用的全部规则标识。

### 单次调用成功规则

单次调用只有在产生终态响应，并满足显式工具规则且未命中失败条件时才判定成功。
失败条件优先于成功条件，避免同时出现成功标志和业务错误码时误判成功。工具可通过
`completed_is_success` 显式声明仅凭完成终态即可成功；没有该声明时，研究模式的
`completed` 只代表执行层完成，不自动代表业务成功。

规则条件由响应内的字段路径和可接受值组成。字符串比较忽略首尾空白与大小写，数值
按值比较，布尔值不与 `0/1` 混同。字段路径可进入 JSON 对象字符串，但不对普通文本
做模糊匹配。

### 字段映射

聊天模式：

- 唯一调用沿用调用事件完整率的数组位置标识。
- 工具名优先读取 `tool_request_args.toolName`，响应侧
  `tool_response_result.toolName` 用于补充和一致性校验；两者冲突时返回
  `NOT_COMPUTABLE`，原因 `tool_name_mismatch`。
- 规则在 `tool_response_result` 上执行，可检查 `result`、`rawResult` 内配置的
  `code`、`status`、`success`、`errorcode` 等状态字段。
- 响应缺失或为空时明确判定为非成功；响应存在但工具名或规则不足时结果未知。

研究模式：

- 唯一调用按 `part.callID` 去重。
- 工具名通过模型结果中的 `result.toolCalls[].toolCallId -> toolName` 与
  `part.callID` 关联；同一 `callID` 出现冲突名称时返回 `tool_name_mismatch`。
- `failed`、`error`、`cancelled`、`canceled`、`timeout`、`timed_out` 明确判定为
  非成功。
- `completed` 状态继续在 `part.state.output` 或 `part.state.raw` 上执行工具规则。
- 没有终态的调用明确判定为非成功；有 `completed` 但无法按规则判定时结果未知。

规范化结果新增 `tool_name`、`success_observed` 和 `success_rule_id`。响应正文只在内存中
用于规则匹配，不进入规范化 Trace、评测元数据或持久化产物。成功判定专用格式问题
写入 `success_normalization_error`，不得改变调用事件完整率的计算结果。

### 模拟计算

```text
唯一调用数 = 4
成功调用数 = 2
明确非成功调用数 = 2
未知调用数 = 0
调用成功率 = 2 ÷ 4 = 0.5
```

如果其中任一调用的 `success_observed` 为未知，则严格模式返回
`NOT_COMPUTABLE / undetermined_tool_outcome`，不输出部分样本成功率。
