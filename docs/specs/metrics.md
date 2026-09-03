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
- 本批次只计算选择层 Recall。调用是否成功属于执行质量，不混入路由选择判断。
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

- 整体意图：`query`。
- 子任务意图：`tool_call_queue[]` 中 `event_type == "call_sub_agent_begin"` 的
  `request_payload.taskName` 和 `request_payload.arguments.query`，按文本去重。
- 实际能力：`event_type == "tool_call"` 的 `tool_request_args.toolName`。

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
- 子任务事件存在但 payload、`arguments` 或任务正文非法时返回
  `NOT_COMPUTABLE / invalid_subtask_event`，不得静默缩小分母。
- 研究模式尚无确认的结构化子任务事件契约，返回
  `NOT_COMPUTABLE / missing_structured_subtasks`，不得从普通描述文本推测。

### 字段映射

聊天模式：

- 会话标识：`traceId`。
- 子任务候选：`tool_call_queue[]` 中 `event_type == "call_sub_agent_begin"` 的事件。
- 一个候选事件对应一个实际子任务，不在比较前去重。
- 子任务文本：组合 `request_payload.taskName` 和 `request_payload.arguments.query`；兼容
  现有日志中的 `taskDescription` 和 `arguments.task`。标题与正文均为空时事件非法。

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
- evaluator 版本为 `1.0.0`；Judge 名称、模型版本和 prompt 版本进入安全元数据。

### 分模式可计算性与字段映射

聊天模式：`CONDITIONALLY_COMPUTABLE`。

- 必需用户意图：顶层 `query`；缺失时返回 `missing_query`。
- 代理证据：`call_sub_agent_begin.request_payload.taskName`，兼容 `taskDescription`；
  `request_payload.arguments.query`，兼容 `arguments.task`；已解析到 MCP 目录的
  `tool_call.tool_request_args.toolName`；以及顶层 `answer`。
- 完全相同的证据正文仅保留第一次出现的位置和类型；未知框架工具只进入范围外诊断。
- 只有 query、没有任何子任务、MCP 能力或最终回答证据时返回 `no_intent_evidence`。
- 当没有其他代理证据且回答为固定模型服务失败文本时返回 `pre_intent_failure`。

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

## 11. 时间范围提取准确率

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
- evaluator 版本为 `1.0.0`；Judge、模型、prompt 和 MCP 手册版本进入安全元数据。

### 相对时间规则

- 相对时间必须绑定日志中的会话/调用时间或调用方显式提供的 `reference_time`，不得使用
  评测运行当天，保证结果可复现。
- `近 N 年` 按以锚点为终点的 N 年滚动区间；`今年以来` 从锚点所在年份 1 月 1 日开始；
  `上一完整年度` 为锚点年份之前的一个自然年。
- 用户要求相对时间但没有可靠锚点时返回 `missing_reference_time`。
- 工具省略可选日期时，只有 MCP 参数说明明确给出默认观察日语义，Judge 才能使用该默认值。

### 分模式可计算性与字段映射

聊天模式：`CONDITIONALLY_COMPUTABLE`。

- 用户需求：顶层 `query`；缺失时返回 `missing_query`。
- 实际时间证据：`tool_call_queue[].tool_request_args.arguments`，并结合 MCP 工具和参数说明。
- 相对时间锚点：调用方显式值优先，否则取首个工具请求的
  `tool_request_args.create_time`；无法取得时保留为空，由 Judge 判断是否需要。
- 调用按 `toolId` 去重；参数无法完整解析时返回 `incomplete_time_observation`。
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

### 模拟计算

```text
用户要求：2020A-2025A 历史数据，2026E-2028E 预测数据，年度频率
实际参数：2020A-2024A 历史数据，2026E-2028E 预测数据，年度频率

可评估字段数 = 7
正确字段数 = 6
错误字段数 = 1
时间范围提取准确率 = 6 ÷ 7 ≈ 0.85714
```

## 12. 指标与口径提取准确率

稳定指标名：`metric_scope_extraction_accuracy`

```text
指标与口径提取准确率 =
  正确字段数 ÷（正确字段数 + 错误字段数 + 意外新增字段数）
```

第一阶段 Judge 只读取 query，冻结指标名称及用户明确要求的单位、币种、频率、统计口径和
实际/预测属性；第二阶段只能针对冻结字段比较去重工具参数。相同规范化 query 在单次运行中
复用同一需求结果，防止实际参数改变标准分母。无工具调用但存在明确指标需求时确定性记零。

聊天模式和研究模式均为 `CONDITIONALLY_COMPUTABLE`。聊天取顶层 `query` 与
`tool_call_queue[].tool_request_args.arguments`；研究取顶层 `query`（兼容 `body.query`）与
`result.toolCalls[].input`。缺失 query、调用 ID、冲突定义、参数解析不完整分别使用
`missing_query`、`missing_call_id`、`conflicting_call_definition`、
`incomplete_metric_observation`。需求无指标或有歧义、两阶段 Judge 输出非法及 Judge 失败均
返回对应 `NOT_COMPUTABLE`，不得估算。

结果只保留字段类型判定和聚合计数，不持久化 query、指标名称、口径值、工具参数或 Judge
原始响应。evaluator 版本为 `1.0.0`。

```text
正确字段 = 7，错误字段 = 1，意外新增字段 = 1
指标与口径提取准确率 = 7 ÷ 9 ≈ 0.77778
```
